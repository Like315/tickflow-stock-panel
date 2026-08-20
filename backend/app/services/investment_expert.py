"""Low-intervention paper trading and evolution orchestrator."""
from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from app.data_providers import get_provider
from app.market_calendar import is_cn_trading_day
from app.market_time import CN_TZ, cn_now
from app.paper_agent.dataset import TrainingDatasetBuilder
from app.paper_agent.evolution import PolicyEvaluator, PolicyEvolutionEngine
from app.paper_agent.execution import StrictMinuteExecutor
from app.paper_agent.models import MinuteBar, PositionLot, RiskConstitution
from app.paper_agent.runtime import InvestmentExpertRuntime
from app.paper_agent.store import PaperAgentStore
from app.paper_agent.training import ExpertModelTrainer
from app.price_limits import is_risk_warning_name, price_limit_pct
from app.services.kline_sync import sync_and_persist_daily_batch
from app.tickflow.capabilities import Cap, CapabilitySet
from app.tickflow.rate_limits import resolve_limit

logger = logging.getLogger(__name__)


class InvestmentExpertService:
    def __init__(
        self,
        repo,
        data_dir: Path,
        *,
        capset=None,
        strategy_engine=None,
        screener_service=None,
        us_market_service=None,
        trading_day_checker: Callable[[date], bool] = is_cn_trading_day,
    ) -> None:
        self.repo = repo
        self.data_dir = data_dir
        self.capset: CapabilitySet | None = None
        self.strategy_engine = strategy_engine
        self.screener_service = screener_service
        self.us_market_service = us_market_service
        self._trading_day_checker = trading_day_checker
        self.store = PaperAgentStore(data_dir)
        recovered = self.store.recover_interrupted_records(before_trade_date=cn_now().date())
        if recovered["sessions"] or recovered["datasets"]:
            logger.warning("investment expert recovered interrupted records: %s", recovered)
        self.store.ensure_baseline_policy()
        self.constitution = RiskConstitution()
        self.minute_provider = get_provider("tickflow")
        self.update_capabilities(capset)
        self.dataset_builder = TrainingDatasetBuilder(repo, data_dir, self.minute_provider)
        self.dataset_root = data_dir / "user_data" / "investment_expert" / "training"
        self._executor_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="investment-expert")
        self._task_lock = threading.RLock()
        self._cycle_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._close_event = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._active_future: Future | None = None
        self._active_task: str | None = None
        self._last_error: str | None = None
        self._runtime: InvestmentExpertRuntime | None = None
        self._executor: StrictMinuteExecutor | None = None
        self._session: dict[str, Any] | None = None
        self._candidates: list[str] = []
        self._market_symbols: list[str] = []
        self._candidate_context: dict[str, dict[str, Any]] = {}
        self._next_fetch_at: datetime | None = None
        self._last_processed_bar: datetime | None = None
        self._finalized_date: date | None = None
        self._session_start_equity = self.constitution.initial_capital
        self._equity_peak = self.constitution.initial_capital
        self._risk_trip_reason: str | None = None
        self._overnight_us_context: dict[str, Any] | None = None
        self._prepare_failure_reason: str | None = None

    def update_capabilities(self, capset: CapabilitySet | None) -> None:
        """Refresh the live TickFlow capability snapshot without restarting the service."""
        if capset is self.capset:
            return
        had_minute = bool(self.capset and self.capset.has(Cap.KLINE_MINUTE_BATCH))
        self.capset = capset
        has_minute = bool(capset and capset.has(Cap.KLINE_MINUTE_BATCH))
        if capset is not None:
            minute_limit = resolve_limit(
                capset,
                Cap.KLINE_MINUTE_BATCH,
                default_batch=50,
                default_rpm=30,
            )
            configure_limits = getattr(self.minute_provider, "configure_minute_limits", None)
            if configure_limits is not None:
                configure_limits(batch_size=minute_limit.batch, rpm=minute_limit.rpm)
        if had_minute != has_minute:
            logger.info(
                "InvestmentExpertService capabilities updated: KLINE_MINUTE_BATCH %s -> %s",
                had_minute,
                has_minute,
            )

    def boot_check(self) -> None:
        if self.store.get_runtime_setting("enabled", False):
            self.start()

    def start(self) -> dict[str, Any]:
        if self.capset is not None and not self.capset.has(Cap.KLINE_MINUTE_BATCH):
            self.store.set_runtime_setting("enabled", False)
            return {
                "status": "blocked",
                "running": False,
                "reason": "tickflow_minute_batch_capability_required",
            }
        self.store.set_runtime_setting("enabled", True)
        with self._task_lock:
            if self._poll_thread is not None and self._poll_thread.is_alive():
                return {"status": "reused", "running": True}
            self._stop_event.clear()
            self._poll_thread = threading.Thread(
                target=self._poll_loop,
                name="investment-expert-paper-runtime",
                daemon=True,
            )
            self._poll_thread.start()
        current = cn_now()
        if current.weekday() >= 5 or current.time() >= time(15, 5):
            self._maybe_submit_initial_dataset()
        return {"status": "started", "running": True}

    def stop(self) -> dict[str, Any]:
        self.store.set_runtime_setting("enabled", False)
        self._stop_event.set()
        thread = self._poll_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        return {"status": "stopped", "running": False}

    def close(self) -> None:
        self._stop_event.set()
        self._close_event.set()
        thread = self._poll_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        future = self._active_future
        if future is not None and not future.done():
            future.cancel()
        self._executor_pool.shutdown(wait=False, cancel_futures=True)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_paper_cycle_once()
            except Exception as exc:
                self._last_error = str(exc)[:500]
                logger.exception("investment expert paper cycle failed")
            self._stop_event.wait(10)

    @staticmethod
    def _is_continuous_session(now: datetime) -> bool:
        clock = now.timetz().replace(tzinfo=None)
        # Keep a one-minute close grace so the 11:29/14:59 bars can become complete.
        return time(9, 30) <= clock < time(11, 31) or time(13, 0) <= clock < time(15, 1)

    def run_paper_cycle_once(self, now: datetime | None = None) -> dict[str, Any]:
        if not self._cycle_lock.acquire(blocking=False):
            return {"status": "reused", "reason": "paper_cycle_in_progress"}
        try:
            return self._run_paper_cycle_once(now)
        finally:
            self._cycle_lock.release()

    def _run_paper_cycle_once(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or cn_now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=CN_TZ)
        if not self._trading_day_checker(now.date()):
            return {"status": "idle", "reason": "market_closed"}
        clock = now.timetz().replace(tzinfo=None)
        if time(9, 15) <= clock < time(15, 5) and self._runtime is None:
            prepared = self._prepare_session(now)
            if not prepared:
                return {
                    "status": "degraded",
                    "reason": self._prepare_failure_reason or "no_point_in_time_candidates",
                }
        if self._is_continuous_session(now):
            return self._process_new_minute_bars(now)
        if clock >= time(15, 5) and self._session and self._finalized_date != now.date():
            return self._finalize_session(now)
        if clock >= time(15, 5) and self._session is None:
            bootstrap = self._maybe_submit_initial_dataset()
            if bootstrap is not None:
                return bootstrap
        return {"status": "idle", "reason": "outside_continuous_session"}

    def _maybe_submit_initial_dataset(self) -> dict[str, Any] | None:
        dataset = self.store.status().get("dataset")
        if dataset is not None and dataset.get("status") == "succeeded":
            return None
        if dataset is not None and dataset.get("status") == "failed":
            finished_at = dataset.get("finished_at")
            if finished_at:
                try:
                    failed_date = datetime.fromisoformat(str(finished_at)).astimezone(CN_TZ).date()
                    if failed_date == cn_now().date():
                        return None
                except ValueError:
                    return None
        return self.submit_dataset_bootstrap(years=3, candidate_limit=50, download_minutes=True)

    def _prepare_session(self, now: datetime) -> bool:
        champion = self.store.get_champion() or self.store.ensure_baseline_policy()
        overnight_context = self._load_overnight_us_context(now.date())
        if self.us_market_service is not None and overnight_context is None:
            self._prepare_failure_reason = "overnight_us_market_unavailable"
            return False
        candidates, context = self._select_candidates(
            now.date(),
            champion.candidate_limit,
            overnight_context=overnight_context,
            overnight_weight=champion.overnight_us_candidate_weight,
        )
        if not candidates:
            self._prepare_failure_reason = "no_point_in_time_candidates"
            return False
        session = self.store.start_session(
            now.date(), champion.id, mode="paper", candidates=candidates
        )
        executor = self._restore_executor()
        held_symbols = {lot.symbol for lot in executor.lots if lot.remaining_shares > 0}
        context.update(self._load_symbol_context(held_symbols - set(candidates), now.date()))
        active_model = self.store.get_active_model()
        self._runtime = InvestmentExpertRuntime(
            session_id=session["id"],
            policy=champion,
            candidates=set(candidates),
            executor=executor,
            candidate_context=context,
            decision_model=active_model,
            entry_guard=self._refresh_risk_state,
        )
        self._executor = executor
        self._session = session
        self._candidates = candidates
        self._market_symbols = sorted(set(candidates) | held_symbols)
        self._candidate_context = context
        self._overnight_us_context = overnight_context
        self._prepare_failure_reason = None
        floor = now.replace(second=0, microsecond=0)
        self._next_fetch_at = (
            floor - timedelta(minutes=1)
            if floor.time() > time(9, 31)
            else datetime.combine(now.date(), time(9, 30), tzinfo=CN_TZ)
        )
        self._last_processed_bar = None
        self._finalized_date = None
        self._session_start_equity = executor.equity()
        self._equity_peak = max(
            executor.equity(),
            self.store.portfolio_peak_equity() or executor.equity(),
        )
        self._risk_trip_reason = None
        return True

    def _load_overnight_us_context(self, trade_date: date) -> dict[str, Any] | None:
        if self.us_market_service is None:
            return {
                "available": False,
                "status": "not_configured",
                "market_date": None,
                "score": 0.0,
                "tilt": 0.0,
                "benchmarks": {},
            }
        try:
            overview = self.us_market_service.get_overview()
            market_time = datetime.fromisoformat(str(overview.get("market_time") or ""))
            market_date = market_time.date()
            if market_date >= trade_date or (trade_date - market_date).days > 7:
                logger.warning(
                    "investment expert rejected stale US overnight context: %s for %s",
                    market_date,
                    trade_date,
                )
                return None
            benchmark_weights = {
                "SPY.US": 0.40,
                "QQQ.US": 0.30,
                "DIA.US": 0.20,
                "IWM.US": 0.10,
            }
            benchmark_returns: dict[str, float] = {}
            weighted_sum = 0.0
            available_weight = 0.0
            for row in overview.get("benchmarks") or []:
                symbol = str(row.get("symbol") or "").upper()
                weight = benchmark_weights.get(symbol)
                value = row.get("change_pct")
                if weight is None or value is None:
                    continue
                change_pct = float(value)
                if not math.isfinite(change_pct):
                    continue
                benchmark_returns[symbol] = change_pct
                weighted_sum += weight * change_pct
                available_weight += weight
            if available_weight < 0.60:
                return None
            index_return = weighted_sum / available_weight
            breadth = overview.get("breadth") or {}
            up_ratio = float(breadth.get("up_ratio") or 0.0)
            down_ratio = float(breadth.get("down_ratio") or 0.0)
            breadth_return = max(-1.0, min(1.0, up_ratio - down_ratio)) * 0.01
            score = 0.8 * index_return + 0.2 * breadth_return
            return {
                "available": True,
                "status": str(overview.get("status") or "unknown"),
                "market_date": market_date.isoformat(),
                "as_of": overview.get("as_of"),
                "score": round(score, 8),
                "tilt": round(max(-1.0, min(1.0, score / 0.02)), 8),
                "benchmarks": benchmark_returns,
                "breadth": {
                    "up_ratio": up_ratio,
                    "down_ratio": down_ratio,
                },
            }
        except (AttributeError, TypeError, ValueError, RuntimeError):
            logger.exception("investment expert US overnight context unavailable")
            return None

    def _refresh_risk_state(self) -> bool:
        if self._executor is None or self._runtime is None:
            return False
        equity = self._executor.equity()
        self._equity_peak = max(self._equity_peak, equity)
        daily_return = equity / self._session_start_equity - 1 if self._session_start_equity else -1
        drawdown = equity / self._equity_peak - 1 if self._equity_peak else -1
        if daily_return <= -self.constitution.max_daily_loss_pct:
            self._risk_trip_reason = "max_daily_loss"
        elif drawdown <= -self.constitution.max_total_drawdown_pct:
            self._risk_trip_reason = "max_total_drawdown"
        if self._risk_trip_reason is not None:
            self._runtime.entries_enabled = False
        return self._risk_trip_reason is None

    def _load_symbol_context(
        self,
        symbols: set[str],
        trade_date: date,
    ) -> dict[str, dict[str, Any]]:
        if not symbols:
            return {}
        daily = self.repo.get_daily_batch(
            sorted(symbols),
            trade_date - timedelta(days=10),
            trade_date - timedelta(days=1),
            columns=["symbol", "date", "close"],
        )
        instruments = self.repo.get_instruments()
        names = {
            str(row["symbol"]): str(row.get("name") or row["symbol"])
            for row in instruments.iter_rows(named=True)
        } if not instruments.is_empty() else {}
        context = {
            symbol: {
                "source_date": None,
                "previous_close": None,
                "name": names.get(symbol, symbol),
                "score": None,
                "daily_momentum_20d": None,
            }
            for symbol in symbols
        }
        if daily.is_empty() or not {"symbol", "date", "close"}.issubset(daily.columns):
            return context
        latest = daily.sort(["symbol", "date"]).group_by("symbol", maintain_order=True).tail(1)
        context.update({
            str(row["symbol"]): {
                "source_date": str(row["date"]),
                "previous_close": float(row["close"]),
                "name": names.get(str(row["symbol"]), str(row["symbol"])),
                "score": None,
                "daily_momentum_20d": None,
            }
            for row in latest.iter_rows(named=True)
        })
        return context

    def _restore_executor(self) -> StrictMinuteExecutor:
        executor = StrictMinuteExecutor(self.constitution)
        snapshot = self.store.latest_portfolio_snapshot()
        if not snapshot:
            return executor
        executor.cash = float(snapshot["cash"])
        payload = snapshot.get("payload") or {}
        if payload.get("executor_state"):
            executor.restore_state(payload["executor_state"])
            return executor
        executor.lots = [PositionLot.model_validate(item) for item in payload.get("lots", [])]
        executor.last_prices = {
            str(symbol): float(value)
            for symbol, value in (payload.get("last_prices") or {}).items()
        }
        return executor

    def _select_candidates(
        self,
        trade_date: date,
        limit: int,
        *,
        overnight_context: dict[str, Any] | None = None,
        overnight_weight: float = 0.15,
    ) -> tuple[list[str], dict[str, dict[str, Any]]]:
        instruments = self.repo.get_instruments()
        if instruments.is_empty() or "symbol" not in instruments.columns:
            return [], {}
        symbols = instruments["symbol"].to_list()
        start_date = trade_date - timedelta(days=180)
        end_date = trade_date - timedelta(days=1)
        latest_available = self.repo.latest_daily_date()
        if latest_available is not None:
            end_date = min(end_date, latest_available)
        history = None
        get_enriched_range = getattr(self.repo, "get_enriched_range", None)
        if get_enriched_range is not None:
            history = get_enriched_range(
                start_date,
                end_date,
                symbols=symbols,
                columns=[
                    "symbol", "date", "open", "high", "low", "close",
                    "volume", "amount", "raw_close",
                ],
            )
        if history is None or history.is_empty():
            history = self.repo.get_daily_batch(
                symbols,
                start_date,
                end_date,
                columns=[
                    "symbol", "date", "open", "high", "low", "close", "volume", "amount"
                ],
            )
        if history.is_empty():
            return [], {}
        latest_date = history["date"].max()
        history = history.sort(["symbol", "date"]).with_columns(
            (
                pl.col("close").cast(pl.Float64)
                / pl.col("close").cast(pl.Float64).shift(20).over("symbol")
                - 1
            ).alias("_momentum"),
            pl.col("close").cast(pl.Float64).shift(1).over("symbol").alias("_previous_close"),
            pl.col("close")
            .cast(pl.Float64)
            .pct_change()
            .rolling_std(window_size=20)
            .over("symbol")
            .alias("_volatility_20d"),
        )
        latest = history.filter(pl.col("date") == latest_date).drop_nulls(["_momentum"])
        if latest.is_empty():
            return [], {}
        name_map = {
            str(row["symbol"]): str(row.get("name") or row["symbol"])
            for row in instruments.iter_rows(named=True)
        }
        eligible_symbols = [
            symbol for symbol in symbols
            if not is_risk_warning_name(name_map.get(str(symbol), ""))
        ]
        latest = latest.filter(pl.col("symbol").is_in(eligible_symbols))
        if latest.is_empty():
            return [], {}
        latest = latest.with_columns(
            pl.col("_momentum").rank(descending=True).alias("_mom_rank"),
            pl.col("amount").rank(descending=True).alias("_amount_rank"),
            pl.col("_volatility_20d").rank(descending=False).alias("_defensive_rank"),
            pl.len().cast(pl.Float64).alias("_count"),
        ).with_columns(
            (1 - (pl.col("_mom_rank") - 1) / pl.col("_count")).alias("_momentum_score"),
            (1 - (pl.col("_defensive_rank") - 1) / pl.col("_count")).alias("_defensive_score"),
            (
                0.7 * (1 - (pl.col("_mom_rank") - 1) / pl.col("_count"))
                + 0.3 * (1 - (pl.col("_amount_rank") - 1) / pl.col("_count"))
            ).alias("_score")
        )
        strategy_scores = self._strategy_consensus_scores(latest_date)
        if strategy_scores:
            strategy_frame = pl.DataFrame({
                "symbol": list(strategy_scores),
                "_strategy_score": list(strategy_scores.values()),
            })
            latest = latest.join(strategy_frame, on="symbol", how="left").with_columns(
                pl.col("_strategy_score").fill_null(0.0),
                (
                    0.75 * pl.col("_score")
                    + 0.25 * pl.col("_strategy_score").fill_null(0.0)
                ).alias("_score"),
            )
        else:
            latest = latest.with_columns(pl.lit(0.0).alias("_strategy_score"))
        overnight_score = float((overnight_context or {}).get("score") or 0.0)
        overnight_tilt = float((overnight_context or {}).get("tilt") or 0.0)
        applied_weight = min(max(overnight_weight, 0.0), 0.5) * abs(overnight_tilt)
        preference = (
            pl.col("_momentum_score")
            if overnight_tilt >= 0
            else pl.col("_defensive_score")
        )
        latest = latest.with_columns(
            (
                (1 - applied_weight) * pl.col("_score")
                + applied_weight * preference
            ).alias("_score"),
            preference.alias("_overnight_fit"),
        )
        latest = latest.sort(["_score", "symbol"], descending=[True, False]).head(limit)
        selected = [str(value) for value in latest["symbol"].to_list()]
        context: dict[str, dict[str, Any]] = {}
        for row in latest.iter_rows(named=True):
            symbol = str(row["symbol"])
            context[symbol] = {
                "source_date": str(latest_date),
                "previous_close": float(row.get("raw_close") or row["close"]),
                "name": name_map.get(symbol, symbol),
                "score": float(row["_score"]),
                "daily_momentum_20d": float(row["_momentum"]),
                "strategy_consensus": float(row["_strategy_score"]),
                "overnight_us_score": overnight_score,
                "overnight_us_tilt": overnight_tilt,
                "overnight_us_fit": float(row["_overnight_fit"]),
            }
        return selected, context

    def _strategy_consensus_scores(self, as_of: date) -> dict[str, float]:
        if self.strategy_engine is None or self.screener_service is None:
            return {}
        preferred = (
            "bullish_alignment",
            "trend_breakout",
            "volume_price_surge",
            "pullback_ma20_bounce",
            "ma_golden_cross",
        )
        available = {str(item["id"]) for item in self.strategy_engine.list_strategies()}
        strategy_ids = [strategy_id for strategy_id in preferred if strategy_id in available]
        if not strategy_ids:
            return {}
        try:
            context = self.screener_service.build_strategy_context(
                self.strategy_engine,
                as_of,
                strategy_ids,
            )
            results = self.strategy_engine.run_all(context, strategy_ids=strategy_ids)
        except Exception:
            logger.exception("investment expert strategy consensus failed; using rank baseline")
            return {}
        votes: dict[str, int] = {}
        for result in results.values():
            for row in result.rows:
                symbol = str(row.get("symbol") or "")
                if symbol:
                    votes[symbol] = votes.get(symbol, 0) + 1
        return {symbol: count / len(strategy_ids) for symbol, count in votes.items()}

    def _process_new_minute_bars(self, now: datetime) -> dict[str, Any]:
        if self._runtime is None or self._executor is None or self._session is None:
            return {"status": "degraded", "reason": "session_not_prepared"}
        start = self._next_fetch_at or now.replace(second=0, microsecond=0)
        end = now
        minute = self.minute_provider.get_minute(
            self._market_symbols,
            start_time=start,
            end_time=end,
            asset_type="stock",
            freq="1m",
        )
        if minute.is_empty():
            return {"status": "degraded", "reason": "minute_data_empty"}
        minute = minute.unique(subset=["symbol", "datetime"], keep="last")
        processed = 0
        decisions = 0
        event_count = 0
        last_dt: datetime | None = None
        for row in minute.sort(["datetime", "symbol"]).iter_rows(named=True):
            bar_time = row["datetime"]
            if bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=CN_TZ)
            if bar_time < start:
                continue
            if self._last_processed_bar is not None and bar_time <= self._last_processed_bar:
                continue
            if bar_time + timedelta(minutes=1) > now:
                continue
            symbol = str(row["symbol"])
            symbol_last_bar = self._executor.last_bar_time.get(symbol)
            if symbol_last_bar is not None and bar_time <= symbol_last_bar:
                continue
            context = self._candidate_context.get(symbol, {})
            is_limit_up, is_limit_down = self._limit_flags(
                symbol=symbol,
                name=str(context.get("name") or ""),
                trade_date=now.date(),
                previous_close=context.get("previous_close"),
                row=row,
            )
            bar = MinuteBar(
                symbol=symbol,
                datetime=bar_time,
                received_at=now,
                raw_open=float(row.get("raw_open", row["open"])),
                raw_high=float(row.get("raw_high", row["high"])),
                raw_low=float(row.get("raw_low", row["low"])),
                raw_close=float(row.get("raw_close", row["close"])),
                volume=float(row["volume"]),
                amount=float(row["amount"]),
                is_suspended=(
                    float(row["volume"]) <= 0 or context.get("previous_close") is None
                ),
                is_limit_up=is_limit_up,
                is_limit_down=is_limit_down,
            )
            step = self._runtime.on_bar(bar)
            self._refresh_risk_state()
            events = list(step.execution_events)
            if step.submitted_event is not None:
                events.append(step.submitted_event)
            self.store.save_execution_events(self._session["id"], events)
            event_count += len(events)
            if step.decision is not None:
                decision = step.decision
                if decision["action"] in {"buy", "sell"} or bar_time.minute % 15 == 0:
                    try:
                        self.store.save_decision(
                            decision_id=decision["id"],
                            session_id=self._session["id"],
                            symbol=decision["symbol"],
                            decision_time=decision["decision_time"],
                            action=decision["action"],
                            features=decision["features"],
                            reason=decision["reason"],
                        )
                    except Exception as exc:
                        if "UNIQUE constraint failed" not in str(exc):
                            raise
                    decisions += 1
            processed += 1
            last_dt = bar_time if last_dt is None else max(last_dt, bar_time)
        if last_dt is not None:
            self._last_processed_bar = last_dt
            self._next_fetch_at = last_dt + timedelta(minutes=1)
            self.store.save_portfolio_snapshot(
                self._session["id"],
                as_of=last_dt + timedelta(minutes=1),
                cash=self._executor.cash,
                equity=self._executor.equity(),
                payload={
                    "lots": [lot.model_dump(mode="json") for lot in self._executor.lots],
                    "last_prices": self._executor.last_prices,
                    "pending_order_ids": sorted(self._executor.pending),
                    "executor_state": self._executor.export_state(),
                },
            )
        self._last_error = None
        return {
            "status": "succeeded",
            "processed_bars": processed,
            "decisions": decisions,
            "execution_events": event_count,
        }

    @staticmethod
    def _limit_flags(
        *,
        symbol: str,
        name: str,
        trade_date: date,
        previous_close: Any,
        row: dict[str, Any],
    ) -> tuple[bool, bool]:
        try:
            previous = float(previous_close)
        except (TypeError, ValueError):
            return False, False
        pct = price_limit_pct(
            symbol, trade_date, is_risk_warning=is_risk_warning_name(name)
        )
        cents = math.floor(previous * 100 + 0.5)
        up_factor = round((1 + pct) * 100)
        down_factor = round((1 - pct) * 100)
        up_price = ((cents * up_factor + 50) // 100) / 100
        down_price = ((cents * down_factor + 50) // 100) / 100
        prices = [float(row.get(f"raw_{key}", row[key])) for key in ("open", "high", "low", "close")]
        one_price = max(prices) - min(prices) <= max(abs(prices[-1]) * 1e-4, 0.01)
        return (
            one_price and abs(prices[-1] - up_price) < 0.005,
            one_price and abs(prices[-1] - down_price) < 0.005,
        )

    def _finalize_session(self, now: datetime) -> dict[str, Any]:
        if self._session is None or self._executor is None:
            return {"status": "degraded", "reason": "session_not_prepared"}
        session_id = self._session["id"]
        persisted_events = self.store.list_execution_events(
            session_id=session_id,
            limit=2000,
        )
        realized = [
            float(event["realized_pnl"])
            for event in persisted_events
            if event.get("realized_pnl") is not None
        ]
        summary = {
            "cash": round(self._executor.cash, 2),
            "equity": round(self._executor.equity(), 2),
            "positions": len({lot.symbol for lot in self._executor.lots}),
            "pending_orders": len(self._executor.pending),
            "closed_trades": len(realized),
            "realized_pnl": round(sum(realized), 2),
            "risk_trip_reason": self._risk_trip_reason,
        }
        self.store.finish_session(session_id, summary)
        reflection = {
            "trade_date": now.date().isoformat(),
            "closed_trades": len(realized),
            "loss_rate": (
                sum(value < 0 for value in realized) / len(realized) if realized else 0.5
            ),
            "realized_pnl": sum(realized),
            "lesson": "insufficient evidence" if not realized else "learn from realized after-cost outcomes",
        }
        self.store.save_reflection(session_id, reflection)
        rollback = None
        if self._risk_trip_reason is not None:
            rollback_reason = f"paper_runtime_{self._risk_trip_reason}"
            rollback = {
                "policy": self.store.rollback_last_promotion(
                    reason=rollback_reason,
                    metrics=summary,
                ),
                "model_event_id": self.store.rollback_last_model_promotion(
                    reason=rollback_reason,
                    metrics=summary,
                ),
            }
        self._finalized_date = now.date()
        evolution_status = "skipped_after_risk_trip"
        dataset = self.store.status().get("dataset")
        if self._risk_trip_reason is None and (
            dataset is None or dataset.get("status") != "succeeded"
        ):
            self.submit_dataset_bootstrap(years=3, candidate_limit=50, download_minutes=True)
            evolution_status = "dataset_bootstrap_submitted"
        elif self._risk_trip_reason is None:
            self.submit_evolution(reflection=reflection)
            evolution_status = "submitted"
        self._runtime = None
        self._executor = None
        self._session = None
        self._candidates = []
        self._market_symbols = []
        self._candidate_context = {}
        return {
            "status": "succeeded",
            "summary": summary,
            "evolution": evolution_status,
            "rollback": rollback,
        }

    def submit_evolution(self, *, reflection: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._task_lock:
            if self._active_future is not None and not self._active_future.done():
                return {"status": "reused", "task": self._active_task}
            self._active_task = "evolution"
            self._active_future = self._executor_pool.submit(
                self._run_evolution, reflection or {"loss_rate": 0.5}
            )
        return {"status": "started", "task": "evolution"}

    def _run_evolution(self, reflection: dict[str, Any]) -> dict[str, Any]:
        if not self._operation_lock.acquire(blocking=False):
            return {"status": "reused"}
        try:
            champion = self.store.get_champion() or self.store.ensure_baseline_policy()
            policies = self.store.list_policies(limit=500)
            next_version = max((policy.version for policy in policies), default=0) + 1
            evolution = PolicyEvolutionEngine()
            candidate, mutation_field = evolution.propose(
                champion, reflection, next_version=next_version
            )
            self.store.save_policy(candidate)
            evaluator = PolicyEvaluator(
                self.dataset_root,
                self.constitution,
                decision_model=self.store.get_active_model(),
            )
            champion_metrics = evaluator.evaluate(champion)
            candidate_metrics = evaluator.evaluate(candidate)
            outcome, reason = evolution.gate(champion_metrics, candidate_metrics)
            self.store.record_experiment(
                champion_policy_id=champion.id,
                candidate_policy_id=candidate.id,
                mutation_field=mutation_field,
                status=outcome,
                champion_metrics=champion_metrics.as_dict(),
                candidate_metrics=candidate_metrics.as_dict(),
                reason=reason,
            )
            if outcome == "promoted":
                self.store.promote(
                    candidate.id, reason=reason, metrics=candidate_metrics.as_dict()
                )
            return {"status": outcome, "reason": reason, "candidate": candidate.id}
        except Exception as exc:
            self._last_error = str(exc)[:500]
            logger.exception("investment expert evolution failed")
            return {"status": "failed", "error": self._last_error}
        finally:
            self._operation_lock.release()

    def submit_dataset_bootstrap(
        self,
        *,
        years: int = 3,
        candidate_limit: int = 50,
        download_minutes: bool = True,
    ) -> dict[str, Any]:
        if (
            download_minutes
            and self.capset is not None
            and not self.capset.has(Cap.KLINE_MINUTE_BATCH)
        ):
            return {
                "status": "blocked",
                "reason": "tickflow_minute_batch_capability_required",
            }
        if (
            download_minutes
            and self._poll_thread is not None
            and self._poll_thread.is_alive()
            and self._is_continuous_session(cn_now())
        ):
            return {"status": "deferred", "reason": "paper_runtime_has_market_priority"}
        with self._task_lock:
            if self._active_future is not None and not self._active_future.done():
                return {"status": "reused", "task": self._active_task}
            self._active_task = "dataset_bootstrap"
            self._active_future = self._executor_pool.submit(
                self._run_dataset_bootstrap,
                years,
                candidate_limit,
                download_minutes,
            )
        return {"status": "started", "task": "dataset_bootstrap"}

    def _run_dataset_bootstrap(
        self, years: int, candidate_limit: int, download_minutes: bool
    ) -> dict[str, Any]:
        current = cn_now()
        end_date = current.date() if current.time() >= time(16, 0) else current.date() - timedelta(days=1)
        start_date = end_date - timedelta(days=365 * years)
        run_id = self.store.record_dataset_run(
            start_date=start_date,
            end_date=end_date,
            status="running",
            manifest={},
        )
        if not self._operation_lock.acquire(blocking=False):
            return {"status": "reused"}
        try:
            self._ensure_daily_history(start_date - timedelta(days=40), end_date)
            manifest = self.dataset_builder.build(
                start_date=start_date,
                end_date=end_date,
                candidate_limit=candidate_limit,
                download_minutes=download_minutes,
                progress_cb=self._dataset_progress,
            )
            self.store.record_dataset_run(
                start_date=start_date,
                end_date=end_date,
                status="succeeded",
                manifest=manifest,
                run_id=run_id,
                finished=True,
            )
            training: dict[str, Any] = {"status": "skipped", "reason": "minute_download_disabled"}
            if download_minutes:
                try:
                    training = self._train_model_locked()
                except Exception as exc:
                    self._last_error = str(exc)[:500]
                    logger.exception("dataset succeeded but initial model training failed")
                    training = {"status": "failed", "error": self._last_error}
            return {"status": "succeeded", "manifest": manifest, "training": training}
        except Exception as exc:
            self._last_error = str(exc)[:500]
            self.store.record_dataset_run(
                start_date=start_date,
                end_date=end_date,
                status="failed",
                manifest={},
                run_id=run_id,
                error=self._last_error,
                finished=True,
            )
            logger.exception("investment expert dataset bootstrap failed")
            return {"status": "failed", "error": self._last_error}
        finally:
            self._operation_lock.release()

    def _ensure_daily_history(self, start_date: date, end_date: date) -> int:
        earliest = self.repo.earliest_daily_date()
        latest = self.repo.latest_daily_date()
        if self.capset is None:
            return 0
        instruments = self.repo.get_instruments()
        if instruments.is_empty() or "symbol" not in instruments.columns:
            return 0
        symbols = sorted(str(symbol) for symbol in instruments["symbol"].to_list())
        ranges: list[tuple[date, date]] = []
        if earliest is None or latest is None:
            ranges.append((start_date, end_date))
        else:
            if earliest > start_date:
                ranges.append((start_date, earliest))
            if latest < end_date:
                ranges.append((latest, end_date))
        written = 0
        for sync_start, sync_end in ranges:
            self._raise_if_closing()
            logger.info(
                "investment expert syncing daily history: %s to %s, %d symbols",
                sync_start,
                sync_end,
                len(symbols),
            )
            written += sync_and_persist_daily_batch(
                symbols,
                self.repo,
                self.capset,
                start_date=datetime.combine(sync_start, time.min),
                end_date=datetime.combine(sync_end, time.max),
                on_chunk_done=lambda _current, _total: self._raise_if_closing(),
            )
        return written

    def _dataset_progress(self, _current: int, _total: int, _label: str) -> None:
        self._raise_if_closing()

    def _raise_if_closing(self) -> None:
        if self._close_event.is_set():
            raise RuntimeError("investment expert service is shutting down")

    def submit_model_training(self) -> dict[str, Any]:
        with self._task_lock:
            if self._active_future is not None and not self._active_future.done():
                return {"status": "reused", "task": self._active_task}
            self._active_task = "model_training"
            self._active_future = self._executor_pool.submit(self._run_model_training)
        return {"status": "started", "task": "model_training"}

    def _run_model_training(self) -> dict[str, Any]:
        if not self._operation_lock.acquire(blocking=False):
            return {"status": "reused"}
        try:
            return self._train_model_locked()
        except Exception as exc:
            self._last_error = str(exc)[:500]
            logger.exception("investment expert model training failed")
            return {"status": "failed", "error": self._last_error}
        finally:
            self._operation_lock.release()

    def _train_model_locked(self) -> dict[str, Any]:
        models = self.store.list_models(limit=500)
        next_version = max((model.version for model in models), default=0) + 1
        model = ExpertModelTrainer(self.dataset_root, self.constitution).train(
            version=next_version
        )
        self.store.save_model(model)
        protected = model.metrics["protected_test"]
        validation = model.metrics["validation"]
        active = self.store.get_active_model()
        reason = "protected_model_gate_passed"
        promoted = (
            protected["samples"] >= 50
            and validation["brier"] <= validation["baseline_brier"]
            and protected["brier"] <= protected["baseline_brier"]
            and protected["selected"] >= 10
            and (protected["selected_mean_net_return"] or float("-inf")) > 0
        )
        if active is not None and promoted:
            active_metrics = active.metrics.get("protected_test", {})
            promoted = (
                protected["brier"] < float(active_metrics.get("brier", 1.0))
                and (protected["selected_mean_net_return"] or float("-inf"))
                >= float(active_metrics.get("selected_mean_net_return") or float("-inf"))
            )
            if not promoted:
                reason = "active_model_did_not_improve"
        elif not promoted:
            reason = "protected_model_gate_rejected"
        if promoted:
            self.store.promote_model(model.id, reason=reason, metrics=model.metrics)
        return {"status": "promoted" if promoted else "shadow", "model": model.id, "reason": reason}

    def status(self) -> dict[str, Any]:
        base = self.store.status()
        executor = self._executor
        snapshot = self.store.latest_portfolio_snapshot() if executor is None else None
        snapshot_payload = (snapshot or {}).get("payload") or {}
        future = self._active_future
        base.update({
            "running": bool(self._poll_thread and self._poll_thread.is_alive()),
            "active_task": self._active_task if future is not None and not future.done() else None,
            "last_error": self._last_error,
            "session_id": self._session["id"] if self._session else None,
            "candidate_count": len(self._candidates),
            "market_symbol_count": len(self._market_symbols),
            "cash": (
                round(executor.cash, 2) if executor
                else round(float(snapshot["cash"]), 2) if snapshot else self.constitution.initial_capital
            ),
            "equity": (
                round(executor.equity(), 2) if executor
                else round(float(snapshot["equity"]), 2) if snapshot else self.constitution.initial_capital
            ),
            "positions": (
                [lot.model_dump(mode="json") for lot in executor.lots]
                if executor else snapshot_payload.get("lots", [])
            ),
            "pending_order_count": (
                len(executor.pending) if executor
                else len((snapshot_payload.get("executor_state") or {}).get("pending", []))
            ),
            "entries_enabled": bool(
                self._runtime is None or self._runtime.entries_enabled
            ),
            "risk_trip_reason": self._risk_trip_reason,
            "overnight_us_market": self._overnight_us_context,
            "session_prepare_error": self._prepare_failure_reason,
            "minute_capable": bool(
                self.capset is None or self.capset.has(Cap.KLINE_MINUTE_BATCH)
            ),
        })
        return base
