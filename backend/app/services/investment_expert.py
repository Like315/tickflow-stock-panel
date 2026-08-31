"""Low-intervention paper trading and evolution orchestrator."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import polars as pl

from app.data_providers import get_provider
from app.data_providers.huggingface_archive import (
    HuggingFaceAshareMinuteArchive,
)
from app.market_calendar import is_cn_trading_day
from app.market_time import CN_TZ, cn_now
from app.paper_agent.dataset import TrainingDatasetBuilder
from app.paper_agent.evolution import PolicyEvaluator, PolicyEvolutionEngine
from app.paper_agent.exceptions import StrategyDependencyUnavailableError, StrategyLabError
from app.paper_agent.execution import StrictMinuteExecutor
from app.paper_agent.models import (
    ExpertStrategyRecord,
    MinuteBar,
    PositionLot,
    RiskConstitution,
    StrategyParameterCandidate,
)
from app.paper_agent.runtime import InvestmentExpertRuntime, InvestmentExpertRuntimeConfig
from app.paper_agent.store import PaperAgentStore
from app.paper_agent.strategy_orchestrator import (
    MarketRegime,
    classify_market_regime,
    matched_strategy_ids_by_symbol,
    plan_strategy_allocation,
    weighted_consensus_scores,
)
from app.paper_agent.training import ExpertModelTrainer
from app.price_limits import is_risk_warning_name, price_limit_pct
from app.services import preferences
from app.services.kline_sync import sync_and_persist_daily_batch
from app.tickflow.capabilities import Cap, CapabilitySet
from app.tickflow.policy import base_tier_name
from app.tickflow.rate_limits import resolve_limit

if TYPE_CHECKING:
    from app.backtest.optimizer import OptimizeConfig, StrategyOptimizer
    from app.backtest.strategy import StrategyBacktestResult, StrategyBacktestService
    from app.strategy.engine import StrategyDef

logger = logging.getLogger(__name__)


_OVERNIGHT_US_MODULE_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...], float], ...] = (
    (("半导体", "集成电路", "芯片"), ("XSD.US", "XLK.US"), 1.00),
    (("软件", "IT服务", "计算机应用", "互联网服务"), ("XSW.US", "XLK.US"), 1.00),
    (("生物科技", "生物制品"), ("XBI.US", "XLV.US"), 1.00),
    (("制药", "医药商业", "中药"), ("XPH.US", "XLV.US"), 1.00),
    (("医疗器械", "医疗设备"), ("XHE.US", "XLV.US"), 1.00),
    (("银行",), ("KBE.US", "KRE.US", "XLF.US"), 1.00),
    (("零售", "商贸"), ("XRT.US", "XLY.US"), 1.00),
    (("住宅开发", "房地产开发", "家居用品"), ("XHB.US", "XLRE.US"), 1.00),
    (("油气", "石油", "天然气"), ("XOP.US", "XLE.US"), 1.00),
    (("金属", "钢铁", "矿业", "矿物制品"), ("XME.US", "XLB.US"), 1.00),
    (("航空航天", "国防军工", "军工装备"), ("XAR.US", "XLI.US"), 1.00),
    (("通信设备", "电信运营"), ("XTL.US", "XLC.US"), 1.00),
    (("电子", "计算机", "科技"), ("XLK.US",), 0.75),
    (("传媒", "通信服务"), ("XLC.US",), 0.75),
    (("汽车", "家用电器", "可选消费", "消费者服务"), ("XLY.US",), 0.75),
    (("食品饮料", "农林牧渔", "日常消费", "纺织服饰"), ("XLP.US",), 0.75),
    (("证券", "保险", "非银金融", "金融"), ("XLF.US",), 0.75),
    (("医疗保健", "医药"), ("XLV.US",), 0.75),
    (("机械", "工业", "建筑", "交通运输", "电力设备"), ("XLI.US",), 0.75),
    (("能源", "煤炭"), ("XLE.US",), 0.75),
    (("基础材料", "原材料", "化工", "建筑材料"), ("XLB.US",), 0.75),
    (("房地产",), ("XLRE.US",), 0.75),
    (("公用事业", "电力", "燃气", "水务"), ("XLU.US",), 0.75),
)


@dataclass(frozen=True, slots=True)
class DatasetMinuteSourcePlan:
    """训练数据构建使用的远端与归档分钟源窗口。"""

    start_date: date
    end_date: date
    remote_start_date: date | None
    archive_start: date | None = None
    archive_end: date | None = None
    archive_revision: str | None = None


@dataclass(slots=True)
class DatasetBootstrapRunState:
    """一次训练数据构建任务的持久化进度状态。"""

    plan: DatasetMinuteSourcePlan
    progress_manifest: dict[str, Any]
    run_id: str


@dataclass(frozen=True, slots=True)
class BacktestWindow:
    """一次策略回测使用的闭区间日期窗口。"""

    start: date
    end: date


@dataclass(frozen=True, slots=True)
class StrategyOptimizationWindows:
    """策略参数优化的训练窗口与独立保护窗口。"""

    train: BacktestWindow
    protected: BacktestWindow


@dataclass(frozen=True, slots=True)
class StrategyOptimizationContext:
    """单批内置策略优化共享的只读依赖。"""

    service: StrategyBacktestService
    optimizer: StrategyOptimizer
    active_params: dict[str, dict[str, Any]]
    windows: StrategyOptimizationWindows


@dataclass(frozen=True, slots=True)
class GeneratedStrategyEvaluation:
    """AI 候选策略的优化参数与独立保护集结果。"""

    params: dict[str, Any]
    optimization: dict[str, Any]
    result: StrategyBacktestResult
    windows: StrategyOptimizationWindows


OptimizationOutcome = Literal["promoted", "rejected"]


class InvestmentExpertService:
    """协调投资专家模拟交易、策略实验和持续演进。"""

    def __init__(
        self,
        repo,
        data_dir: Path,
        *,
        capset=None,
        strategy_engine=None,
        screener_service=None,
        us_market_service=None,
        news_sentiment_service=None,
        stock_portfolio_service=None,
        historical_minute_archive=None,
        trading_day_checker: Callable[[date], bool] = is_cn_trading_day,
    ) -> None:
        self.repo = repo
        self.data_dir = data_dir
        self.capset: CapabilitySet | None = None
        self.strategy_engine = strategy_engine
        self.screener_service = screener_service
        self.us_market_service = us_market_service
        self.news_sentiment_service = news_sentiment_service
        self.stock_portfolio_service = stock_portfolio_service
        self.historical_minute_archive = (
            historical_minute_archive or HuggingFaceAshareMinuteArchive(data_dir)
        )
        self._trading_day_checker = trading_day_checker
        self.store = PaperAgentStore(data_dir)
        recovered = self.store.recover_interrupted_records(before_trade_date=cn_now().date())
        if recovered["sessions"] or recovered["datasets"]:
            logger.warning("investment expert recovered interrupted records: %s", recovered)
        self.store.ensure_baseline_policy()
        self.constitution = RiskConstitution()
        self.minute_provider = get_provider("tickflow")
        (
            self.historical_minute_provider,
            self._historical_minute_provider_error,
        ) = self._resolve_historical_minute_provider(self.minute_provider)
        self.update_capabilities(capset)
        self.dataset_builder = TrainingDatasetBuilder(
            repo,
            data_dir,
            self.historical_minute_provider,
        )
        self.dataset_root = data_dir / "user_data" / "investment_expert" / "training"
        self._executor_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="investment-expert"
        )
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
        self._news_sentiment_context: dict[str, Any] | None = None
        self._strategy_orchestration: dict[str, Any] | None = None
        self._next_news_refresh_at: datetime | None = None
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
            if capset.has(Cap.INTRADAY_BATCH):
                intraday_limit = resolve_limit(
                    capset,
                    Cap.INTRADAY_BATCH,
                    default_batch=50,
                    default_rpm=30,
                )
                configure_intraday = getattr(
                    self.minute_provider,
                    "configure_intraday_limits",
                    None,
                )
                if configure_intraday is not None:
                    configure_intraday(
                        batch_size=intraday_limit.batch,
                        rpm=intraday_limit.rpm,
                    )
        if had_minute != has_minute:
            logger.info(
                "InvestmentExpertService capabilities updated: KLINE_MINUTE_BATCH %s -> %s",
                had_minute,
                has_minute,
            )

    @staticmethod
    def _resolve_historical_minute_provider(tickflow_provider):
        provider_name = preferences.get_minute_data_provider()
        if provider_name == "tickflow":
            return tickflow_provider, None
        try:
            from app.data_providers import custom as custom_sources

            if not custom_sources.provider_has_dataset(provider_name, "minute"):
                return tickflow_provider, (
                    f"configured historical minute provider '{provider_name}' "
                    "does not expose the minute dataset"
                )
            return custom_sources.get_provider(provider_name), None
        except Exception as exc:
            return tickflow_provider, (
                f"historical minute provider '{provider_name}' is unavailable: {exc}"
            )

    def _refresh_historical_minute_provider(self) -> None:
        provider, error = self._resolve_historical_minute_provider(self.minute_provider)
        self.historical_minute_provider = provider
        self._historical_minute_provider_error = error
        self.dataset_builder.minute_provider = provider

    def _historical_minute_max_years(self) -> int | None:
        if getattr(self.historical_minute_provider, "name", "tickflow") != "tickflow":
            return None
        # TickFlow's published Pro coverage is one year of minute history.
        return 1 if base_tier_name() == "pro" else None

    def _remote_historical_minute_capable(self, years: int) -> bool:
        if self._historical_minute_provider_error is not None:
            return False
        if getattr(self.historical_minute_provider, "name", "tickflow") == "tickflow":
            if self.capset is not None and not self.capset.has(Cap.KLINE_MINUTE_BATCH):
                return False
            max_years = self._historical_minute_max_years()
            return max_years is None or years <= max_years
        return True

    @staticmethod
    def _subtract_years(value: date, years: int) -> date:
        try:
            return value.replace(year=value.year - years)
        except ValueError:
            return value.replace(year=value.year - years, day=28)

    @staticmethod
    def _dataset_window(years: int) -> tuple[date, date]:
        current = cn_now()
        end_date = (
            current.date() if current.time() >= time(16, 0) else current.date() - timedelta(days=1)
        )
        return InvestmentExpertService._subtract_years(end_date, years), end_date

    def _remote_minute_start_date(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> date | None:
        """Return the first date routed to the configured provider, if any."""
        if self._historical_minute_provider_error is not None:
            return None
        if getattr(self.historical_minute_provider, "name", "tickflow") != "tickflow":
            return start_date
        if self.capset is not None and not self.capset.has(Cap.KLINE_MINUTE_BATCH):
            return None
        max_years = self._historical_minute_max_years()
        if max_years is None:
            return start_date
        return max(start_date, self._subtract_years(end_date, max_years))

    def _local_historical_minute_bounds(
        self,
    ) -> dict[str, tuple[date | None, date | None]]:
        loader = getattr(self.repo, "minute_date_bounds", None)
        if not callable(loader):
            return {}
        bounds: dict[str, tuple[date | None, date | None]] = {}
        for asset_type in ("stock", "index", "etf"):
            try:
                bounds[asset_type] = loader(asset_type)
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.exception(
                    "failed to inspect local %s minute coverage",
                    asset_type,
                )
                bounds[asset_type] = (None, None)
        return bounds

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
                self._last_error = None
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
        news_context = self._load_news_sentiment_context(now)
        candidates, context = self._select_candidates(
            now.date(),
            champion.candidate_limit,
            overnight_context=overnight_context,
            overnight_weight=champion.overnight_us_candidate_weight,
            news_context=news_context,
            news_weight=champion.news_candidate_weight,
            strategy_weight=champion.strategy_consensus_weight,
        )
        if not candidates:
            self._prepare_failure_reason = "no_point_in_time_candidates"
            return False
        session = self.store.start_session(
            now.date(), champion.id, mode="paper", candidates=candidates
        )
        if self._strategy_orchestration is not None:
            self.store.save_strategy_orchestration(
                session["id"],
                now.date(),
                self._strategy_orchestration,
            )
        executor = self._restore_executor()
        held_symbols = {lot.symbol for lot in executor.lots if lot.remaining_shares > 0}
        context.update(self._load_symbol_context(held_symbols - set(candidates), now.date()))
        self._attach_overnight_module_context(context, overnight_context)
        active_model = self.store.get_active_model()
        self._runtime = InvestmentExpertRuntime(
            InvestmentExpertRuntimeConfig(
                session_id=session["id"],
                policy=champion,
                candidates=set(candidates),
                executor=executor,
                candidate_context=context,
                decision_model=active_model,
                entry_guard=self._refresh_risk_state,
            )
        )
        self._executor = executor
        self._session = session
        self._candidates = candidates
        self._market_symbols = sorted(set(candidates) | held_symbols)
        self._candidate_context = context
        self._overnight_us_context = overnight_context
        self._news_sentiment_context = news_context
        self._next_news_refresh_at = now + timedelta(seconds=self._news_refresh_seconds())
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
        latest_sync = self.store.latest_portfolio_sync()
        sync_created_at = latest_sync["created_at"] if latest_sync else None
        synced_equity = float(latest_sync["equity"]) if latest_sync else executor.equity()
        self._equity_peak = max(
            executor.equity(),
            synced_equity,
            self.store.portfolio_peak_equity(since=sync_created_at) or executor.equity(),
        )
        self._risk_trip_reason = None
        return True

    def _load_overnight_us_context(self, trade_date: date) -> dict[str, Any]:
        if self.us_market_service is None:
            return self._unavailable_overnight_us_context("not_configured")
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
                return self._unavailable_overnight_us_context(
                    "stale",
                    market_date=market_date,
                )
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
            proxy_symbols = [
                str(row.get("symbol") or "").upper()
                for key in ("sectors", "themes")
                for row in (overview.get(key) or [])
                if row.get("symbol")
            ]
            proxy_volatilities: dict[str, float] = {}
            volatility_loader = getattr(
                self.us_market_service,
                "get_proxy_volatilities",
                None,
            )
            if callable(volatility_loader) and proxy_symbols:
                try:
                    proxy_volatilities = volatility_loader(proxy_symbols, window=20)
                except Exception:
                    logger.exception("investment expert US module volatility unavailable")
            modules = self._overnight_us_modules(overview, proxy_volatilities)
            if not modules:
                return self._unavailable_overnight_us_context(
                    "incomplete",
                    market_date=market_date,
                )
            market_background_available = available_weight >= 0.60
            index_return = weighted_sum / available_weight if market_background_available else 0.0
            breadth = overview.get("breadth") or {}
            up_ratio = float(breadth.get("up_ratio") or 0.0)
            down_ratio = float(breadth.get("down_ratio") or 0.0)
            breadth_return = max(-1.0, min(1.0, up_ratio - down_ratio)) * 0.01
            market_score = 0.8 * index_return + 0.2 * breadth_return
            return {
                "available": True,
                "status": str(overview.get("status") or "unknown"),
                "market_date": market_date.isoformat(),
                "as_of": overview.get("as_of"),
                "score": round(market_score, 8),
                "tilt": round(max(-1.0, min(1.0, market_score / 0.02)), 8),
                "market_background_available": market_background_available,
                "benchmarks": benchmark_returns,
                "modules": modules,
                "breadth": {
                    "up_ratio": up_ratio,
                    "down_ratio": down_ratio,
                },
            }
        except (AttributeError, TypeError, ValueError, RuntimeError):
            logger.exception("investment expert US overnight context unavailable")
            return self._unavailable_overnight_us_context("unavailable")

    @staticmethod
    def _overnight_us_modules(
        overview: dict[str, Any],
        volatilities: dict[str, float],
    ) -> dict[str, dict[str, Any]]:
        modules: dict[str, dict[str, Any]] = {}
        for kind, key in (("sector", "sectors"), ("theme", "themes")):
            for row in overview.get(key) or []:
                symbol = str(row.get("symbol") or "").upper()
                value = row.get("change_pct")
                if not symbol or value is None:
                    continue
                change_pct = float(value)
                if not math.isfinite(change_pct):
                    continue
                raw_volatility = float(volatilities.get(symbol) or 0.0)
                has_observed_volatility = math.isfinite(raw_volatility) and raw_volatility > 0
                volatility = max(raw_volatility, 0.008) if has_observed_volatility else 0.02
                data_confidence = 1.0 if has_observed_volatility else 0.75
                normalized_signal = math.tanh(change_pct / volatility)
                modules[symbol] = {
                    "symbol": symbol,
                    "name": str(row.get("name") or symbol),
                    "kind": kind,
                    "change_pct": round(change_pct, 8),
                    "volatility_20d": round(volatility, 8),
                    "normalized_signal": round(normalized_signal, 8),
                    "data_confidence": data_confidence,
                }
        return modules

    @staticmethod
    def _unavailable_overnight_us_context(
        status: str,
        *,
        market_date: date | None = None,
    ) -> dict[str, Any]:
        return {
            "available": False,
            "status": status,
            "market_date": market_date.isoformat() if market_date else None,
            "score": 0.0,
            "tilt": 0.0,
            "market_background_available": False,
            "benchmarks": {},
            "modules": {},
        }

    def _load_news_sentiment_context(self, as_of: datetime) -> dict[str, Any]:
        if self.news_sentiment_service is None:
            return self._unavailable_news_sentiment_context("not_configured", as_of)
        try:
            return self.news_sentiment_service.get_context(as_of)
        except Exception:
            logger.exception("investment expert news sentiment unavailable")
            return self._unavailable_news_sentiment_context("unavailable", as_of)

    @staticmethod
    def _unavailable_news_sentiment_context(
        status: str,
        as_of: datetime,
    ) -> dict[str, Any]:
        return {
            "available": False,
            "status": status,
            "as_of": as_of.isoformat(timespec="seconds"),
            "score": 0.0,
            "confidence": 0.0,
            "item_count": 0,
            "signal_count": 0,
            "source_count": 0,
            "regions": {"global": 0, "domestic": 0, "market": 0},
            "items": [],
        }

    def _news_refresh_seconds(self) -> int:
        value = getattr(self.news_sentiment_service, "refresh_seconds", 600)
        try:
            return max(60, int(value))
        except (TypeError, ValueError):
            return 600

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
        names = (
            {
                str(row["symbol"]): str(row.get("name") or row["symbol"])
                for row in instruments.iter_rows(named=True)
            }
            if not instruments.is_empty()
            else {}
        )
        context: dict[str, dict[str, Any]] = {
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
        context.update(
            {
                str(row["symbol"]): {
                    "source_date": str(row["date"]),
                    "previous_close": float(row["close"]),
                    "name": names.get(str(row["symbol"]), str(row["symbol"])),
                    "score": None,
                    "daily_momentum_20d": None,
                }
                for row in latest.iter_rows(named=True)
            }
        )
        return context

    def _restore_executor(self) -> StrictMinuteExecutor:
        executor = StrictMinuteExecutor(self.constitution)
        snapshot = self.store.latest_portfolio_state()
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

    @staticmethod
    def _synced_acquired_date(position: dict[str, Any], today: date) -> date:
        value = position.get("created_at") or position.get("updated_at")
        if not value:
            return today
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return today
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(CN_TZ)
        return min(parsed.date(), today)

    def _normalize_sync_position(
        self, raw: dict[str, Any], today: date
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        """规范化单个来源持仓, 分别返回持仓、错误和降级警告。"""
        symbol = str(raw.get("symbol") or "").strip().upper()
        name = str(raw.get("name") or "").strip()
        display_name = name or symbol or "未知股票"
        raw_quantity = raw.get("quantity")
        raw_entry_price = raw.get("buy_price")
        if raw_quantity is None or raw_entry_price is None:
            return None, f"{display_name} 的数量或成本价无效", None
        try:
            quantity_value = float(raw_quantity)
            entry_price = float(raw_entry_price)
        except (TypeError, ValueError):
            return None, f"{display_name} 的数量或成本价无效", None
        quantity = round(quantity_value)
        invalid = (
            not symbol
            or quantity <= 0
            or not math.isclose(quantity_value, quantity, abs_tol=1e-6)
            or entry_price <= 0
        )
        if invalid:
            return None, f"{display_name} 的持仓数据不完整", None
        if quantity % self.constitution.lot_size != 0:
            message = f"{display_name} 的数量不是 {self.constitution.lot_size} 股整数倍"
            return None, message, None
        current_price = self._sync_position_current_price(raw)
        warning = None
        if current_price is None or current_price <= 0:
            current_price = entry_price
            warning = f"{display_name} 缺少最新价, 暂以成本价建立同步基线"
        position = {
            "symbol": symbol,
            "name": name,
            "quantity": quantity,
            "entry_price": entry_price,
            "current_price": current_price,
            "acquired_date": self._synced_acquired_date(raw, today).isoformat(),
            "cost_amount": round(entry_price * quantity, 2),
            "market_value": round(current_price * quantity, 2),
        }
        return position, None, warning

    @staticmethod
    def _sync_position_current_price(raw: dict[str, Any]) -> float | None:
        """读取来源持仓最新价, 无效值返回空。"""
        value = raw.get("current_price")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _portfolio_sync_blocked_reason(
        self,
        positions: list[dict[str, Any]],
        errors: list[str],
    ) -> str | None:
        """按运行状态和来源数据确定同步阻断原因。"""
        future = self._active_future
        running = bool(self._poll_thread and self._poll_thread.is_alive())
        busy = bool(future is not None and not future.done())
        if running:
            return "runtime_running"
        if busy:
            return "background_task_running"
        if errors:
            return "invalid_source_positions"
        if not positions:
            return "source_portfolio_empty"
        return None

    @staticmethod
    def _portfolio_sync_position_metrics(
        positions: list[dict[str, Any]],
        executor: StrictMinuteExecutor,
    ) -> dict[str, int | float]:
        """汇总预检持仓金额和即将替换的账户规模。"""
        return {
            "position_count": len(positions),
            "source_total_cost_amount": round(sum(item["cost_amount"] for item in positions), 2),
            "source_total_market_value": round(sum(item["market_value"] for item in positions), 2),
            "replace_position_count": sum(lot.remaining_shares > 0 for lot in executor.lots),
            "current_available_cash": round(float(executor.cash), 2),
        }

    def stock_portfolio_sync_preview(self) -> dict[str, Any]:
        """预检股票持仓并返回规范化同步计划。"""
        if self.stock_portfolio_service is None:
            return {
                "can_sync": False,
                "blocked_reason": "stock_portfolio_service_unavailable",
                "positions": [],
                "errors": ["股票持仓服务尚未初始化"],
                "warnings": [],
            }
        source = self.stock_portfolio_service.get_portfolio()
        positions: list[dict[str, Any]] = []
        errors: list[str] = []
        warnings: list[str] = []
        today = cn_now().date()
        for raw in source.get("positions") or []:
            position, error, warning = self._normalize_sync_position(raw, today)
            if position is not None:
                positions.append(position)
            if error is not None:
                errors.append(error)
            if warning is not None:
                warnings.append(warning)
        executor = self._executor or self._restore_executor()
        blocked_reason = self._portfolio_sync_blocked_reason(positions, errors)
        return {
            "can_sync": blocked_reason is None,
            "blocked_reason": blocked_reason,
            "source": "stock_portfolio",
            "source_updated_at": source.get("updated_at"),
            "positions": positions,
            **self._portfolio_sync_position_metrics(positions, executor),
            "errors": errors,
            "warnings": warnings,
        }

    @staticmethod
    def _validate_portfolio_sync_request(
        confirm_replace: bool,
        available_cash: float | None,
    ) -> None:
        """在加锁和读取持仓前校验覆盖确认与现金参数。"""
        if not confirm_replace:
            raise ValueError("同步会覆盖 AI 当前持仓, 必须明确确认")
        if available_cash is not None and (not math.isfinite(available_cash) or available_cash < 0):
            raise ValueError("可用现金必须是大于或等于 0 的有效数字")

    @staticmethod
    def _require_syncable_preview(preview: dict[str, Any]) -> None:
        """把预检阻断状态转换为稳定的业务异常。"""
        if preview.get("can_sync"):
            return
        messages = {
            "runtime_running": "请先停止 AI 投资专家盯盘, 再同步持仓",
            "background_task_running": "后台任务进行中, 请完成后再同步持仓",
            "source_portfolio_empty": "股票持仓为空, 无法同步",
            "invalid_source_positions": "股票持仓包含无法同步的数据",
            "stock_portfolio_service_unavailable": "股票持仓服务尚未初始化",
        }
        detail = messages.get(str(preview.get("blocked_reason")), "当前无法同步股票持仓")
        if preview.get("errors"):
            detail += f": {'; '.join(preview['errors'])}"
        raise RuntimeError(detail)

    def _build_synced_executor(
        self,
        preview: dict[str, Any],
        cash: float,
    ) -> StrictMinuteExecutor:
        """从预检持仓建立新的严格分钟执行器。"""
        executor = StrictMinuteExecutor(self.constitution)
        executor.cash = cash
        executor.lots = [
            PositionLot(
                lot_id=f"synced_{uuid4().hex}",
                symbol=str(item["symbol"]),
                acquired_date=date.fromisoformat(str(item["acquired_date"])),
                shares=int(item["quantity"]),
                remaining_shares=int(item["quantity"]),
                entry_price=float(item["entry_price"]),
                entry_cost=0.0,
            )
            for item in preview["positions"]
        ]
        executor.last_prices = {
            str(item["symbol"]): float(item["current_price"]) for item in preview["positions"]
        }
        return executor

    def _save_portfolio_sync(
        self,
        executor: StrictMinuteExecutor,
        preview: dict[str, Any],
        available_cash: float | None,
    ) -> dict[str, Any]:
        """保存覆盖同步事件和可恢复的执行器状态。"""
        equity = executor.equity()
        payload = {
            "source": "stock_portfolio",
            "source_updated_at": preview.get("source_updated_at"),
            "position_count": len(executor.lots),
            "replaced_position_count": preview["replace_position_count"],
            "cash_source": "not_provided" if available_cash is None else "user_input",
            "baseline_equity": equity,
            "lots": [lot.model_dump(mode="json") for lot in executor.lots],
            "last_prices": executor.last_prices,
            "executor_state": executor.export_state(),
        }
        return self.store.save_portfolio_sync(
            source="stock_portfolio",
            mode="replace",
            cash=executor.cash,
            equity=equity,
            payload=payload,
        )

    def _reset_runtime_after_portfolio_sync(self) -> None:
        """清除旧持仓派生的运行态, 下次启动时从同步快照恢复。"""
        self._executor = None
        self._runtime = None
        self._session = None
        self._candidates = []
        self._market_symbols = []
        self._candidate_context = {}
        self._last_processed_bar = None
        self._next_fetch_at = None
        self._risk_trip_reason = None
        self._last_error = None

    @staticmethod
    def _portfolio_sync_response(
        event: dict[str, Any],
        executor: StrictMinuteExecutor,
    ) -> dict[str, Any]:
        """构造对外稳定的同步成功摘要。"""
        return {
            "status": "succeeded",
            "sync": {
                "id": event["id"],
                "source": event["source"],
                "mode": event["mode"],
                "created_at": event["created_at"],
                "position_count": len(executor.lots),
                "cash": round(executor.cash, 2),
                "equity": round(executor.equity(), 2),
                "payload_hash": event["payload_hash"],
            },
        }

    def sync_stock_portfolio(
        self,
        *,
        confirm_replace: bool,
        available_cash: float | None = None,
    ) -> dict[str, Any]:
        """在互斥锁保护下用股票持仓覆盖 AI 投资专家账户。"""
        self._validate_portfolio_sync_request(confirm_replace, available_cash)
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("AI 投资专家正在执行其他任务, 请稍后重试")
        try:
            preview = self.stock_portfolio_sync_preview()
            self._require_syncable_preview(preview)
            executor = self._build_synced_executor(preview, float(available_cash or 0.0))
            event = self._save_portfolio_sync(executor, preview, available_cash)
            self._reset_runtime_after_portfolio_sync()
            return self._portfolio_sync_response(event, executor)
        finally:
            self._operation_lock.release()

    @staticmethod
    def _canonical_cn_symbol(symbol: str) -> str:
        value = str(symbol).strip().upper()
        parts = value.split(".")
        if len(parts) == 2 and parts[0] in {"SH", "SZ", "BJ"}:
            return f"{parts[1]}.{parts[0]}"
        return value

    def _candidate_classification_texts(
        self,
        instrument_map: dict[str, dict[str, Any]],
    ) -> dict[str, tuple[str, bool]]:
        industry_fields = (
            "industry",
            "industry_name",
            "sector",
            "所属行业",
            "所属同花顺行业",
            "concept",
            "所属概念",
        )
        industries: dict[str, list[str]] = {}
        names: dict[str, str] = {}
        for symbol, row in instrument_map.items():
            key = self._canonical_cn_symbol(symbol)
            names[key] = str(row.get("name") or "").strip()
            industries[key] = [
                str(row.get(field) or "").strip()
                for field in industry_fields
                if str(row.get(field) or "").strip()
            ]

        industry_path = self.data_dir / "ext_data" / "ext_hy_ths" / "part.parquet"
        if industry_path.exists():
            try:
                industry_frame = pl.read_parquet(industry_path)
                if {"symbol", "所属同花顺行业"}.issubset(industry_frame.columns):
                    for row in industry_frame.select("symbol", "所属同花顺行业").iter_rows(
                        named=True
                    ):
                        key = self._canonical_cn_symbol(str(row["symbol"]))
                        industry = str(row.get("所属同花顺行业") or "").strip()
                        if industry:
                            industries.setdefault(key, []).append(industry)
            except (OSError, TypeError, ValueError):
                logger.exception("investment expert A-share industry snapshot unavailable")

        result: dict[str, tuple[str, bool]] = {}
        for key in set(industries) | set(names):
            labels = industries.get(key) or []
            has_industry = bool(labels)
            text = " ".join(labels or [names.get(key, "")]).strip()
            result[key] = (text, has_industry)
        return result

    def _score_candidate_overnight_modules(
        self,
        instrument_map: dict[str, dict[str, Any]],
        overnight_context: dict[str, Any] | None,
    ) -> dict[str, dict[str, Any]]:
        modules = (overnight_context or {}).get("modules") or {}
        if not modules:
            return {}
        classifications = self._candidate_classification_texts(instrument_map)
        result: dict[str, dict[str, Any]] = {}
        for symbol in instrument_map:
            text, has_industry = classifications.get(
                self._canonical_cn_symbol(symbol),
                ("", False),
            )
            if not text:
                continue
            matched = None
            match_confidence = 0.0
            for keywords, proxy_symbols, base_confidence in _OVERNIGHT_US_MODULE_RULES:
                if not any(keyword in text for keyword in keywords):
                    continue
                matched = next(
                    (modules[proxy] for proxy in proxy_symbols if proxy in modules),
                    None,
                )
                if matched is not None:
                    match_confidence = base_confidence * (1.0 if has_industry else 0.60)
                    break
            if matched is None:
                continue
            normalized_signal = float(matched.get("normalized_signal") or 0.0)
            data_confidence = float(matched.get("data_confidence") or 0.0)
            factor = max(
                -1.0,
                min(1.0, normalized_signal * data_confidence * match_confidence),
            )
            result[symbol] = {
                **matched,
                "factor": round(factor, 8),
                "match_confidence": round(match_confidence, 4),
            }
        return result

    def _attach_overnight_module_context(
        self,
        context: dict[str, dict[str, Any]],
        overnight_context: dict[str, Any] | None,
    ) -> None:
        instruments = self.repo.get_instruments()
        instrument_map = (
            {str(row["symbol"]): row for row in instruments.iter_rows(named=True)}
            if not instruments.is_empty() and "symbol" in instruments.columns
            else {}
        )
        factors = self._score_candidate_overnight_modules(
            instrument_map,
            overnight_context,
        )
        factors_by_symbol = {
            self._canonical_cn_symbol(symbol): factor for symbol, factor in factors.items()
        }
        market_score = float((overnight_context or {}).get("score") or 0.0)
        for symbol, row in context.items():
            factor = factors_by_symbol.get(self._canonical_cn_symbol(symbol))
            row["overnight_us_market_score"] = market_score
            row["overnight_us_available"] = factor is not None
            row["overnight_us_score"] = float(factor["change_pct"]) if factor is not None else None
            row["overnight_us_tilt"] = (
                float(factor["normalized_signal"]) if factor is not None else 0.0
            )
            row["overnight_us_factor"] = float(factor["factor"]) if factor is not None else 0.0
            row["overnight_us_module"] = factor.get("name") if factor else None
            row["overnight_us_module_symbol"] = factor.get("symbol") if factor else None
            row["overnight_us_match_confidence"] = (
                float(factor["match_confidence"]) if factor is not None else 0.0
            )

    def _select_candidates(
        self,
        trade_date: date,
        limit: int,
        *,
        overnight_context: dict[str, Any] | None = None,
        overnight_weight: float = 0.15,
        news_context: dict[str, Any] | None = None,
        news_weight: float = 0.25,
        strategy_weight: float = 0.35,
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
                    "symbol",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "amount",
                    "raw_close",
                ],
            )
        if history is None or history.is_empty():
            history = self.repo.get_daily_batch(
                symbols,
                start_date,
                end_date,
                columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount"],
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
        instrument_map = {str(row["symbol"]): row for row in instruments.iter_rows(named=True)}
        name_map = {
            symbol: str(row.get("name") or symbol) for symbol, row in instrument_map.items()
        }
        eligible_symbols = [
            symbol for symbol in symbols if not is_risk_warning_name(name_map.get(str(symbol), ""))
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
            ).alias("_score"),
        )
        regime = classify_market_regime(
            latest,
            source_date=latest_date,
            overnight_context=overnight_context,
            news_context=news_context,
        )
        strategy_scores, orchestration = self._strategy_consensus_scores(
            latest_date,
            regime,
        )
        orchestration["trade_date"] = trade_date.isoformat()
        orchestration["strategy_consensus_weight"] = round(
            min(max(float(strategy_weight), 0.0), 0.75),
            6,
        )
        self._strategy_orchestration = orchestration
        if strategy_scores:
            applied_strategy_weight = min(max(float(strategy_weight), 0.0), 0.75)
            strategy_frame = pl.DataFrame(
                {
                    "symbol": list(strategy_scores),
                    "_strategy_score": list(strategy_scores.values()),
                }
            )
            latest = latest.join(strategy_frame, on="symbol", how="left").with_columns(
                pl.col("_strategy_score").fill_null(0.0),
                (
                    (1 - applied_strategy_weight) * pl.col("_score")
                    + applied_strategy_weight * pl.col("_strategy_score").fill_null(0.0)
                ).alias("_score"),
            )
        else:
            latest = latest.with_columns(pl.lit(0.0).alias("_strategy_score"))
        module_factors = self._score_candidate_overnight_modules(
            instrument_map,
            overnight_context,
        )
        if module_factors:
            module_frame = pl.DataFrame(
                {
                    "symbol": list(module_factors),
                    "_overnight_us_factor": [
                        float(value["factor"]) for value in module_factors.values()
                    ],
                    "_overnight_us_score": [
                        float(value["change_pct"]) for value in module_factors.values()
                    ],
                    "_overnight_us_tilt": [
                        float(value["normalized_signal"]) for value in module_factors.values()
                    ],
                    "_overnight_us_module": [
                        str(value["name"]) for value in module_factors.values()
                    ],
                    "_overnight_us_module_symbol": [
                        str(value["symbol"]) for value in module_factors.values()
                    ],
                    "_overnight_us_match_confidence": [
                        float(value["match_confidence"]) for value in module_factors.values()
                    ],
                }
            )
            latest = latest.join(module_frame, on="symbol", how="left")
        else:
            latest = latest.with_columns(
                pl.lit(None, dtype=pl.Float64).alias("_overnight_us_factor"),
                pl.lit(None, dtype=pl.Float64).alias("_overnight_us_score"),
                pl.lit(None, dtype=pl.Float64).alias("_overnight_us_tilt"),
                pl.lit(None, dtype=pl.Utf8).alias("_overnight_us_module"),
                pl.lit(None, dtype=pl.Utf8).alias("_overnight_us_module_symbol"),
                pl.lit(None, dtype=pl.Float64).alias("_overnight_us_match_confidence"),
            )
        applied_weight = min(max(overnight_weight, 0.0), 0.5)
        latest = latest.with_columns(
            pl.col("_overnight_us_factor").fill_null(0.0),
            pl.col("_overnight_us_tilt").fill_null(0.0),
            pl.col("_overnight_us_match_confidence").fill_null(0.0),
        ).with_columns(
            (applied_weight * pl.col("_overnight_us_factor")).alias("_overnight_us_adjustment"),
            (pl.col("_score") + applied_weight * pl.col("_overnight_us_factor"))
            .clip(0.0, 1.0)
            .alias("_score"),
            pl.col("_momentum_score").alias("_news_momentum_preference"),
            pl.col("_defensive_score").alias("_news_defensive_preference"),
        )
        latest = latest.with_columns(pl.col("_score").alias("_market_score"))
        news_available = bool((news_context or {}).get("available"))
        news_confidence = float((news_context or {}).get("confidence") or 0.0)
        global_news_score = float((news_context or {}).get("score") or 0.0)
        applied_news_weight = (
            min(max(news_weight, 0.0), 0.5) * min(max(news_confidence, 0.0), 1.0)
            if news_available
            else 0.0
        )
        candidate_news = self._score_candidate_news(news_context or {}, instrument_map)
        if candidate_news:
            news_frame = pl.DataFrame(
                {
                    "symbol": list(candidate_news),
                    "_candidate_news_score": [
                        float(value.get("score") or 0.0) for value in candidate_news.values()
                    ],
                    "_candidate_news_matches": [
                        int(value.get("matched_count") or 0) for value in candidate_news.values()
                    ],
                }
            )
            latest = latest.join(news_frame, on="symbol", how="left")
        else:
            latest = latest.with_columns(
                pl.lit(0.0).alias("_candidate_news_score"),
                pl.lit(0).alias("_candidate_news_matches"),
            )
        latest = latest.with_columns(
            pl.col("_candidate_news_score").fill_null(0.0),
            pl.col("_candidate_news_matches").fill_null(0),
        )
        if global_news_score > 0.05:
            news_preference = pl.col("_news_momentum_preference")
        elif global_news_score < -0.05:
            news_preference = pl.col("_news_defensive_preference")
        else:
            news_preference = pl.lit(0.5)
        latest = latest.with_columns(
            (0.4 * news_preference + 0.6 * ((pl.col("_candidate_news_score") + 1) / 2)).alias(
                "_news_fit"
            ),
            (0.4 * global_news_score + 0.6 * pl.col("_candidate_news_score")).alias(
                "_news_factor_score"
            ),
        ).with_columns(
            (
                (1 - applied_news_weight) * pl.col("_score")
                + applied_news_weight * pl.col("_news_fit")
            ).alias("_score")
        )
        latest = latest.sort(["_score", "symbol"], descending=[True, False]).head(limit)
        selected = [str(value) for value in latest["symbol"].to_list()]
        matched_by_symbol = dict(orchestration.get("matched_strategy_ids_by_symbol") or {})
        allocation_params = {
            str(item.get("strategy_id")): dict(item.get("params") or {})
            for item in orchestration.get("allocations", [])
            if item.get("strategy_id")
        }
        allocation_weights = {
            str(item.get("strategy_id")): float(item.get("weight") or 0.0)
            for item in orchestration.get("allocations", [])
            if item.get("strategy_id")
        }
        context: dict[str, dict[str, Any]] = {}
        for row in latest.iter_rows(named=True):
            symbol = str(row["symbol"])
            matched_strategy_ids = [
                str(value) for value in matched_by_symbol.get(symbol, []) if value
            ]
            primary_strategy_id = (
                min(
                    matched_strategy_ids,
                    key=lambda strategy_id: (
                        -allocation_weights.get(strategy_id, 0.0),
                        strategy_id,
                    ),
                )
                if matched_strategy_ids
                else None
            )
            context[symbol] = {
                "source_date": str(latest_date),
                "previous_close": float(row.get("raw_close") or row["close"]),
                "name": name_map.get(symbol, symbol),
                "score": float(row["_score"]),
                "daily_momentum_20d": float(row["_momentum"]),
                "strategy_consensus": float(row["_strategy_score"]),
                "strategy_ids": matched_strategy_ids,
                "primary_strategy_id": primary_strategy_id,
                "strategy_params": {
                    strategy_id: allocation_params.get(strategy_id, {})
                    for strategy_id in matched_strategy_ids
                },
                "strategy_regime": regime.state,
                "market_score": float(row["_market_score"]),
                "momentum_score": float(row["_news_momentum_preference"]),
                "defensive_score": float(row["_news_defensive_preference"]),
                "overnight_us_available": row["_overnight_us_score"] is not None,
                "overnight_us_market_score": float((overnight_context or {}).get("score") or 0.0),
                "overnight_us_score": (
                    float(row["_overnight_us_score"])
                    if row["_overnight_us_score"] is not None
                    else None
                ),
                "overnight_us_tilt": float(row["_overnight_us_tilt"]),
                "overnight_us_factor": float(row["_overnight_us_factor"]),
                "overnight_us_module": row["_overnight_us_module"],
                "overnight_us_module_symbol": row["_overnight_us_module_symbol"],
                "overnight_us_match_confidence": float(row["_overnight_us_match_confidence"]),
                "overnight_us_adjustment": float(row["_overnight_us_adjustment"]),
                "news_sentiment_available": news_available,
                "news_sentiment_score": global_news_score,
                "news_sentiment_confidence": news_confidence,
                "candidate_news_sentiment": float(row["_candidate_news_score"]),
                "candidate_news_matches": int(row["_candidate_news_matches"]),
                "news_factor_score": float(row["_news_factor_score"]),
                "news_fit": float(row["_news_fit"]),
                "news_applied_weight": applied_news_weight,
            }
        return selected, context

    def _score_candidate_news(
        self,
        news_context: dict[str, Any],
        candidates: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        scorer = getattr(self.news_sentiment_service, "score_candidates", None)
        if scorer is None:
            return {}
        try:
            return scorer(news_context, candidates)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            logger.exception("investment expert candidate news scoring unavailable")
            return {}

    def _strategy_consensus_scores(
        self,
        as_of: date,
        regime: MarketRegime,
    ) -> tuple[dict[str, float], dict[str, Any]]:
        if self.strategy_engine is None or self.screener_service is None:
            return {}, {
                "status": "degraded",
                "reason": "strategy_runtime_unavailable",
                "regime": regime.as_dict(),
                "considered_count": 0,
                "active_count": 0,
                "allocations": [],
                "errors": [],
            }

        catalog = self.strategy_engine.list_strategies()
        active_params = self.store.active_strategy_parameters()
        allocations, payload = plan_strategy_allocation(
            catalog,
            regime,
            promoted_ai_strategy_ids=self.store.promoted_expert_strategy_ids(),
        )
        payload.update({"status": "active" if allocations else "degraded", "errors": []})
        if not allocations:
            payload["reason"] = "no_compatible_strategies"
            return {}, payload

        strategy_ids = [allocation.strategy_id for allocation in allocations]
        params_map = {
            strategy_id: dict(active_params[strategy_id]["params"])
            for strategy_id in strategy_ids
            if strategy_id in active_params
        }
        shared_context = None
        try:
            shared_context = self.screener_service.build_strategy_context(
                self.strategy_engine,
                as_of,
                strategy_ids,
                params_map=params_map,
            )
        except Exception as exc:
            logger.warning(
                "investment expert shared strategy context failed; isolating strategies: %s",
                exc,
            )
            payload["errors"].append(
                {"strategy_id": "*", "error": f"shared_context: {str(exc)[:300]}"}
            )

        results: dict[str, Any] = {}
        for allocation in allocations:
            strategy_id = allocation.strategy_id
            try:
                context = shared_context
                if context is None:
                    context = self.screener_service.build_strategy_context(
                        self.strategy_engine,
                        as_of,
                        [strategy_id],
                        params_map=params_map,
                    )
                results[strategy_id] = self.strategy_engine.run(
                    strategy_id,
                    context,
                    params=params_map.get(strategy_id),
                )
            except Exception as exc:
                logger.warning(
                    "investment expert strategy %s failed; excluding from consensus: %s",
                    strategy_id,
                    exc,
                )
                payload["errors"].append({"strategy_id": strategy_id, "error": str(exc)[:300]})

        scores, match_counts = weighted_consensus_scores(allocations, results)
        payload["matched_strategy_ids_by_symbol"] = matched_strategy_ids_by_symbol(results)
        for item in payload["allocations"]:
            strategy_id = str(item["strategy_id"])
            item["match_count"] = match_counts.get(strategy_id, 0)
            strategy = self.strategy_engine.get(strategy_id)
            item["params"] = self.strategy_engine.resolve_params(
                strategy,
                params_map.get(strategy_id),
            )
            parameter_state = active_params.get(strategy_id)
            item["parameter_version_id"] = (
                parameter_state.get("version_id") if parameter_state else None
            )
        if not results:
            payload["status"] = "degraded"
            payload["reason"] = "all_strategies_failed"
        payload["successful_count"] = len(results)
        return scores, payload

    def _refresh_news_sentiment_context(self, now: datetime) -> None:
        if self._runtime is None or self._session is None:
            return
        if self._next_news_refresh_at is not None and now < self._next_news_refresh_at:
            return
        context = self._load_news_sentiment_context(now)
        self._news_sentiment_context = context
        self._next_news_refresh_at = now + timedelta(seconds=self._news_refresh_seconds())
        candidate_metadata = {
            symbol: row
            for symbol, row in self._candidate_context.items()
            if symbol in self._candidates
        }
        scored = self._score_candidate_news(context, candidate_metadata)
        available = bool(context.get("available"))
        confidence = float(context.get("confidence") or 0.0)
        global_score = float(context.get("score") or 0.0)
        policy_weight = min(max(self._runtime.policy.news_candidate_weight, 0.0), 0.5)
        applied_weight = policy_weight * min(max(confidence, 0.0), 1.0) if available else 0.0
        for symbol, row in candidate_metadata.items():
            candidate = scored.get(symbol) or {}
            candidate_score = float(candidate.get("score") or 0.0)
            if global_score > 0.05:
                preference = float(row.get("momentum_score") or 0.5)
            elif global_score < -0.05:
                preference = float(row.get("defensive_score") or 0.5)
            else:
                preference = 0.5
            news_fit = 0.4 * preference + 0.6 * ((candidate_score + 1) / 2)
            news_factor_score = 0.4 * global_score + 0.6 * candidate_score
            market_score = float(row.get("market_score") or row.get("score") or 0.0)
            row.update(
                {
                    "score": (1 - applied_weight) * market_score + applied_weight * news_fit,
                    "news_sentiment_available": available,
                    "news_sentiment_score": global_score,
                    "news_sentiment_confidence": confidence,
                    "candidate_news_sentiment": candidate_score,
                    "candidate_news_matches": int(candidate.get("matched_count") or 0),
                    "news_factor_score": news_factor_score,
                    "news_fit": news_fit,
                    "news_applied_weight": applied_weight,
                }
            )
        self._runtime.candidate_context = self._candidate_context

    def _intraday_change_ranks(
        self,
        minute: pl.DataFrame,
        now: datetime,
    ) -> dict[tuple[datetime, str], int]:
        """按同一分钟的候选池涨幅生成可审计排名。"""
        grouped: dict[datetime, list[tuple[str, float]]] = {}
        for row in minute.iter_rows(named=True):
            symbol = str(row.get("symbol") or "")
            context = self._candidate_context.get(symbol)
            previous_close = float((context or {}).get("previous_close") or 0.0)
            bar_time = row.get("datetime")
            close = float(row.get("raw_close", row.get("close")) or 0.0)
            if not isinstance(bar_time, datetime) or symbol not in self._candidates:
                continue
            if bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=CN_TZ)
            if bar_time + timedelta(minutes=1) > now or previous_close <= 0 or close <= 0:
                continue
            grouped.setdefault(bar_time, []).append((symbol, close / previous_close - 1))
        ranks: dict[tuple[datetime, str], int] = {}
        for bar_time, values in grouped.items():
            values.sort(key=lambda item: (-item[1], item[0]))
            ranks.update(
                {(bar_time, symbol): rank for rank, (symbol, _) in enumerate(values, start=1)}
            )
        return ranks

    def _process_new_minute_bars(self, now: datetime) -> dict[str, Any]:
        if self._runtime is None or self._executor is None or self._session is None:
            return {"status": "degraded", "reason": "session_not_prepared"}
        self._refresh_news_sentiment_context(now)
        start = self._next_fetch_at or now.replace(second=0, microsecond=0)
        end = now
        intraday_fetch = getattr(self.minute_provider, "get_intraday_minute", None)
        if (
            intraday_fetch is not None
            and self.capset is not None
            and self.capset.has(Cap.INTRADAY_BATCH)
        ):
            minute = intraday_fetch(
                self._market_symbols,
                start_time=start,
                end_time=end,
                asset_type="stock",
                freq="1m",
            )
        else:
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
        change_ranks = self._intraday_change_ranks(minute, now)
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
                # A prior attempt can fail after some symbols were applied but
                # before the batch cursor/snapshot was committed.  Resume from
                # the remaining symbols without replaying their decisions.
                last_dt = bar_time if last_dt is None else max(last_dt, bar_time)
                continue
            context = self._candidate_context.get(symbol, {})
            context["intraday_change_rank"] = change_ranks.get((bar_time, symbol))
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
                is_suspended=(float(row["volume"]) <= 0 or context.get("previous_close") is None),
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
        pct = price_limit_pct(symbol, trade_date, is_risk_warning=is_risk_warning_name(name))
        cents = math.floor(previous * 100 + 0.5)
        up_factor = round((1 + pct) * 100)
        down_factor = round((1 - pct) * 100)
        up_price = ((cents * up_factor + 50) // 100) / 100
        down_price = ((cents * down_factor + 50) // 100) / 100
        prices = [
            float(row.get(f"raw_{key}", row[key])) for key in ("open", "high", "low", "close")
        ]
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
            "lesson": (
                "insufficient evidence"
                if not realized
                else "learn from realized after-cost outcomes"
            ),
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
                "strategy_parameter_event": (
                    self.store.rollback_last_strategy_parameter_promotion(
                        reason=rollback_reason,
                        metrics=summary,
                    )
                ),
                "expert_strategy_id": self.store.rollback_latest_expert_strategy(
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
                self.store.promote(candidate.id, reason=reason, metrics=candidate_metrics.as_dict())
            return {"status": outcome, "reason": reason, "candidate": candidate.id}
        except Exception as exc:
            self._last_error = str(exc)[:500]
            logger.exception("investment expert evolution failed")
            return {"status": "failed", "error": self._last_error}
        finally:
            self._operation_lock.release()

    def _latest_orchestration_payload(self) -> dict[str, Any] | None:
        """返回内存中或最近持久化的策略编排载荷。"""
        if self._strategy_orchestration is not None:
            return self._strategy_orchestration
        persisted = self.store.latest_strategy_orchestration()
        return persisted.get("payload") if persisted else None

    def submit_strategy_optimization(self) -> dict[str, Any]:
        """异步提交当前活动内置策略的参数优化任务。"""
        orchestration = self._latest_orchestration_payload()
        strategy_ids = [
            str(item.get("strategy_id"))
            for item in (orchestration or {}).get("allocations", [])
            if item.get("source") == "builtin" and item.get("strategy_id")
        ]
        if not strategy_ids:
            return {
                "status": "deferred",
                "task": "strategy_optimization",
                "reason": "no_active_builtin_strategy_allocation",
            }
        with self._task_lock:
            if self._active_future is not None and not self._active_future.done():
                return {"status": "reused", "task": self._active_task}
            self._active_task = "strategy_optimization"
            self._active_future = self._executor_pool.submit(
                self._run_strategy_optimization,
                strategy_ids,
            )
        return {"status": "started", "task": "strategy_optimization"}

    @staticmethod
    def _optimizer_grid(
        strategy: StrategyDef,
        base_params: dict[str, Any],
    ) -> dict[str, list[Any]]:
        """为最多三个数值参数构造有界搜索网格。"""
        grid: dict[str, list[Any]] = {}
        for item in strategy.meta.get("params", []):
            if not isinstance(item, dict) or item.get("type") not in {"float", "int"}:
                continue
            parameter_id = str(item.get("id") or "")
            if not parameter_id:
                continue
            current = base_params.get(parameter_id, item.get("default"))
            step = item.get("step")
            if current is None or step is None:
                continue
            try:
                current_value = float(current)
                step_value = float(step)
            except (TypeError, ValueError):
                continue
            if step_value <= 0:
                continue
            lower = max(
                float(item.get("min", current_value - step_value)), current_value - step_value
            )
            upper = min(
                float(item.get("max", current_value + step_value)), current_value + step_value
            )
            values = sorted({lower, current_value, upper})
            if item.get("type") == "int":
                values = sorted({round(value) for value in values})
            grid[parameter_id] = values
            # 3^3=27 trials per strategy; keep the multi-strategy task bounded.
            if len(grid) >= 3:
                break
        return grid

    def _strategy_backtest_service(self) -> StrategyBacktestService:
        """构造策略实验使用的回测服务。"""
        from app.backtest.engine import BacktestEngine
        from app.backtest.strategy import StrategyBacktestService

        return StrategyBacktestService(BacktestEngine(self.repo), self.strategy_engine)

    def _run_strategy_backtest(
        self,
        service: StrategyBacktestService,
        strategy_id: str,
        params: dict[str, Any],
        window: BacktestWindow,
    ) -> StrategyBacktestResult:
        """按投资专家统一交易约束运行一次策略回测。"""
        from app.backtest.strategy import StrategyBacktestConfig

        strategy = self.strategy_engine.get(strategy_id)
        min_hold_days = int(
            strategy.meta.get("min_hold_days") or self.constitution.min_hold_trading_days
        )
        max_hold_days = int(
            getattr(strategy, "max_hold_days", None) or self.constitution.max_hold_trading_days
        )
        return service.run(
            StrategyBacktestConfig(
                strategy_id=strategy_id,
                symbols=None,
                start=window.start,
                end=window.end,
                params=params,
                overrides={"max_hold_days": max_hold_days},
                matching="open_t+1",
                entry_fill="open_t+1",
                exit_fill="open_t+1",
                max_positions=self.constitution.max_positions,
                max_exposure_pct=self.constitution.max_exposure_pct,
                initial_capital=self.constitution.initial_capital,
                position_sizing="score_weight",
                mode="full",
                asset_type="stock",
                holding_days=max_hold_days,
                min_hold_days=min_hold_days,
            )
        )

    @staticmethod
    def _parameter_optimization_gate(
        baseline: StrategyBacktestResult,
        candidate: StrategyBacktestResult,
    ) -> tuple[OptimizationOutcome, str]:
        """使用独立保护集判断候选参数是否允许晋级。"""
        if baseline.error:
            return "rejected", "protected_baseline_backtest_failed"
        if candidate.error:
            return "rejected", "protected_backtest_failed"
        candidate_stats = candidate.stats or {}
        baseline_stats = baseline.stats or {}
        if int(candidate_stats.get("n_trades") or 0) < 10:
            return "rejected", "insufficient_protected_trades"
        if float(candidate_stats.get("avg_pnl") or 0.0) <= float(
            baseline_stats.get("avg_pnl") or 0.0
        ):
            return "rejected", "protected_expectancy_did_not_improve"
        if float(candidate_stats.get("total_return") or 0.0) < float(
            baseline_stats.get("total_return") or 0.0
        ):
            return "rejected", "protected_return_regressed"
        if (
            float(candidate_stats.get("max_drawdown") or 0.0)
            < float(baseline_stats.get("max_drawdown") or 0.0) - 0.01
        ):
            return "rejected", "protected_drawdown_regressed"
        return "promoted", "protected_strategy_optimization_passed"

    def _optimizer_backtest_kwargs(self, strategy_id: str) -> dict[str, Any]:
        """返回参数优化阶段统一使用的回测配置。"""
        strategy = self.strategy_engine.get(strategy_id)
        min_hold_days = int(
            strategy.meta.get("min_hold_days") or self.constitution.min_hold_trading_days
        )
        max_hold_days = int(
            getattr(strategy, "max_hold_days", None) or self.constitution.max_hold_trading_days
        )
        return {
            "matching": "open_t+1",
            "entry_fill": "open_t+1",
            "exit_fill": "open_t+1",
            "max_positions": self.constitution.max_positions,
            "max_exposure_pct": self.constitution.max_exposure_pct,
            "initial_capital": self.constitution.initial_capital,
            "position_sizing": "score_weight",
            "mode": "full",
            "asset_type": "stock",
            "holding_days": max_hold_days,
            "min_hold_days": min_hold_days,
        }

    def _optimizer_config(
        self,
        strategy_id: str,
        base_params: dict[str, Any],
        grid: dict[str, list[Any]],
        window: BacktestWindow,
    ) -> OptimizeConfig:
        """构造单个内置策略的训练窗优化配置。"""
        from app.backtest.optimizer import OptimizeConfig

        strategy = self.strategy_engine.get(strategy_id)
        max_hold_days = int(
            getattr(strategy, "max_hold_days", None) or self.constitution.max_hold_trading_days
        )
        return OptimizeConfig(
            strategy_id=strategy_id,
            symbols=None,
            start=window.start,
            end=window.end,
            param_grid=grid,
            objective="sortino",
            max_workers=1,
            base_params=base_params,
            overrides={"max_hold_days": max_hold_days},
            backtest_kwargs=self._optimizer_backtest_kwargs(strategy_id),
        )

    @staticmethod
    def _optimization_metrics(
        optimized: dict[str, Any],
        baseline: StrategyBacktestResult,
        candidate: StrategyBacktestResult,
        window: BacktestWindow,
    ) -> dict[str, Any]:
        """构造参数优化结果的训练与保护集审计指标。"""
        return {
            "train": {
                "objective": optimized.get("objective"),
                "best_score": optimized.get("best_score"),
                "n_combinations": optimized.get("n_combinations"),
            },
            "protected": {
                "start": window.start.isoformat(),
                "end": window.end.isoformat(),
                "baseline": baseline.stats,
                "candidate": candidate.stats,
            },
        }

    def _persist_optimization_candidate(
        self,
        strategy_id: str,
        params: dict[str, Any],
        metrics: dict[str, Any],
        decision: tuple[OptimizationOutcome, str],
    ) -> dict[str, Any]:
        """保存参数候选，并在通过保护集时晋级。"""
        outcome, reason = decision
        version = self.store.save_strategy_parameter_version(
            StrategyParameterCandidate(
                strategy_id=strategy_id,
                params=params,
                metrics=metrics,
                status=outcome,
                reason=reason,
            )
        )
        if outcome == "promoted":
            self.store.promote_strategy_parameters(version["id"], reason=reason, metrics=metrics)
        return {
            "strategy_id": strategy_id,
            "status": outcome,
            "reason": reason,
            "parameter_version_id": version["id"],
            "params": params,
        }

    @staticmethod
    def _strategy_base_params(
        strategy: StrategyDef,
        current_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """合并策略默认参数和当前活动参数。"""
        defaults = InvestmentExpertService._strategy_default_params(strategy)
        active = dict(current_state["params"]) if current_state else {}
        return {**defaults, **active}

    def _optimize_builtin_strategy(
        self,
        strategy_id: str,
        context: StrategyOptimizationContext,
    ) -> dict[str, Any]:
        """优化并保护集评估一个内置策略。"""
        strategy = self.strategy_engine.get(strategy_id)
        if strategy.source != "builtin":
            return {"strategy_id": strategy_id, "status": "skipped", "reason": "not_builtin"}
        current_state = context.active_params.get(strategy_id)
        base_params = self._strategy_base_params(strategy, current_state)
        grid = self._optimizer_grid(strategy, base_params)
        if not grid:
            return {"strategy_id": strategy_id, "status": "skipped", "reason": "no_numeric_params"}
        optimized = context.optimizer.optimize(
            self._optimizer_config(strategy_id, base_params, grid, context.windows.train)
        )
        best = optimized.get("best_params")
        if not best:
            return {
                "strategy_id": strategy_id,
                "status": "rejected",
                "reason": "optimizer_no_result",
            }
        candidate_params = {**base_params, **dict(best)}
        baseline = self._run_strategy_backtest(
            context.service, strategy_id, base_params, context.windows.protected
        )
        candidate = self._run_strategy_backtest(
            context.service, strategy_id, candidate_params, context.windows.protected
        )
        decision = self._parameter_optimization_gate(baseline, candidate)
        metrics = self._optimization_metrics(
            optimized, baseline, candidate, context.windows.protected
        )
        return self._persist_optimization_candidate(
            strategy_id, candidate_params, metrics, decision
        )

    def _optimize_strategy_safely(
        self,
        strategy_id: str,
        context: StrategyOptimizationContext,
    ) -> dict[str, Any]:
        """隔离单策略异常，保证批次中其余策略继续执行。"""
        try:
            return self._optimize_builtin_strategy(strategy_id, context)
        except Exception as exc:
            logger.exception("investment expert strategy optimization item failed: %s", strategy_id)
            return {
                "strategy_id": strategy_id,
                "status": "failed",
                "reason": "strategy_optimization_failed",
                "error": str(exc)[:500],
            }

    def _strategy_optimization_context(self, latest_date: date) -> StrategyOptimizationContext:
        """构造一批内置策略参数优化共享的依赖与窗口。"""
        from app.backtest.optimizer import StrategyOptimizer

        if self.strategy_engine is None:
            raise StrategyDependencyUnavailableError("策略运行时不可用")
        service = self._strategy_backtest_service()
        windows = StrategyOptimizationWindows(
            train=BacktestWindow(
                latest_date - timedelta(days=450), latest_date - timedelta(days=91)
            ),
            protected=BacktestWindow(latest_date - timedelta(days=90), latest_date),
        )
        return StrategyOptimizationContext(
            service=service,
            optimizer=StrategyOptimizer(service, self.strategy_engine),
            active_params=self.store.active_strategy_parameters(),
            windows=windows,
        )

    def _run_strategy_optimization(self, strategy_ids: list[str]) -> dict[str, Any]:
        """串行优化内置策略，并对每个策略独立隔离失败。"""
        if not self._operation_lock.acquire(blocking=False):
            return {"status": "reused"}
        try:
            latest_date = self.repo.latest_daily_date()
            if latest_date is None:
                raise StrategyDependencyUnavailableError("日线行情不可用")
            context = self._strategy_optimization_context(latest_date)
            results = [
                self._optimize_strategy_safely(strategy_id, context)
                for strategy_id in dict.fromkeys(strategy_ids)
            ]
            return {"status": "succeeded", "results": results}
        except Exception as exc:
            self._last_error = str(exc)[:500]
            logger.exception("investment expert strategy optimization failed")
            return {"status": "failed", "error": self._last_error}
        finally:
            self._operation_lock.release()

    def submit_strategy_generation(self) -> dict[str, Any]:
        """在依赖可用时异步提交 AI 专家策略生成任务。"""
        if self.strategy_engine is None:
            return {
                "status": "deferred",
                "task": "strategy_generation",
                "reason": "strategy_runtime_unavailable",
            }
        from app.services.ai_provider import ai_configured

        if not ai_configured():
            return {
                "status": "deferred",
                "task": "strategy_generation",
                "reason": "ai_not_configured",
            }
        with self._task_lock:
            if self._active_future is not None and not self._active_future.done():
                return {"status": "reused", "task": self._active_task}
            self._active_task = "strategy_generation"
            self._active_future = self._executor_pool.submit(self._run_strategy_generation)
        return {"status": "started", "task": "strategy_generation"}

    def _strategy_generation_prompt(self, strategy_id: str) -> str:
        """构造包含市场状态和历史实验反馈的策略生成提示词。"""
        orchestration = self._latest_orchestration_payload() or {
            "regime": {"state": "balanced"},
            "allocations": [],
        }
        previous = self.store.list_expert_strategies(limit=10)
        return f"""为 TickFlow 的 AI 投资专家生成一个新的 A 股日线选股策略。

这是现有策略的补充，不得复制当前最强策略。策略将在最近历史训练窗优化，并在独立保护集回测；
未通过不会参与模拟交易。只使用 T 日及更早数据产生信号，成交统一由系统在 T+1 执行。

强制要求：
- META.id 必须精确为 {strategy_id!r}
- META 增加 expert_owned=True、asset_types=['stock']、timeframes=['1d']
- 优先使用 matrix_native 协议；必须至少定义 1 个数值参数，并给出 default/min/max/step
- 不写文件、不联网、不读取账户、不包含下单逻辑，只输出一个完整策略 Python 文件

当前市场状态与动态策略分配：
{json.dumps(orchestration, ensure_ascii=False, default=str)}

之前生成策略及保护集结果（请针对失败原因提出不同假设）：
{json.dumps(previous, ensure_ascii=False, default=str)}
"""

    @staticmethod
    def _generated_strategy_gate(
        result: StrategyBacktestResult,
    ) -> tuple[OptimizationOutcome, str]:
        """使用独立保护集判断 AI 候选策略是否允许晋级。"""
        if result.error:
            return "rejected", "protected_backtest_failed"
        stats = result.stats or {}
        if int(stats.get("n_trades") or 0) < 30:
            return "rejected", "insufficient_protected_trades"
        if float(stats.get("avg_pnl") or 0.0) <= 0:
            return "rejected", "non_positive_protected_expectancy"
        if float(stats.get("total_return") or 0.0) <= 0:
            return "rejected", "non_positive_protected_return"
        if float(stats.get("max_drawdown") or 0.0) < -0.20:
            return "rejected", "protected_drawdown_limit_exceeded"
        return "promoted", "protected_generated_strategy_passed"

    def _generation_regime_and_parent(self) -> tuple[str, str | None]:
        """读取生成任务对应的最新市场状态与父策略。"""
        payload = self._latest_orchestration_payload() or {}
        regime = str((payload.get("regime") or {}).get("state", "balanced"))
        allocations = payload.get("allocations") or []
        parent = str(allocations[0].get("strategy_id")) if allocations else None
        return regime, parent

    def _record_generated_strategy_shadow(
        self,
        strategy_id: str,
        regime: str,
        parent_strategy_id: str | None,
    ) -> None:
        """在生成代码前记录可恢复的影子策略。"""
        self.store.record_expert_strategy(
            ExpertStrategyRecord(
                strategy_id=strategy_id,
                parent_strategy_id=parent_strategy_id,
                regime=regime,
                status="shadow",
                metrics={},
                reason="awaiting_generation_and_protected_backtest",
            )
        )

    def _generate_strategy_source(self, strategy_id: str, regime: str) -> str:
        """调用 AI 生成策略并强制写入专家所有权元数据。"""
        from app.strategy.ai_generator import AIStrategyGenerator, normalize_strategy_meta_fields

        generator = AIStrategyGenerator()
        generated = asyncio.run(generator.generate(self._strategy_generation_prompt(strategy_id)))
        if not generated.get("valid"):
            raise StrategyLabError(str(generated.get("error") or "AI 策略校验失败"))
        code = normalize_strategy_meta_fields(
            str(generated["code"]),
            {
                "id": strategy_id,
                "name": f"AI投资专家·{regime}",
                "description": "由 AI 投资专家生成并经过独立保护集门控的候选策略",
                "expert_owned": True,
                "expert_regime": regime,
                "asset_types": ["stock"],
                "timeframes": ["1d"],
            },
        )
        validated = generator.validate_code(code)
        if not validated.get("valid"):
            raise StrategyLabError(str(validated.get("error") or "规范化后的策略无效"))
        return code

    def _install_generated_strategy(self, strategy_id: str, code: str) -> StrategyDef:
        """原子写入 AI 策略文件并验证策略引擎加载结果。"""
        if self.strategy_engine is None:
            raise StrategyDependencyUnavailableError("策略运行时不可用")
        out_dir = self.data_dir / "strategies" / "ai"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{strategy_id}.py"
        if path.exists():
            raise StrategyLabError(f"生成策略已存在：{strategy_id}")
        temp_path = path.with_suffix(".py.tmp")
        temp_path.write_text(code, encoding="utf-8")
        temp_path.replace(path)
        try:
            self.strategy_engine.reload()
            loaded = self.strategy_engine.get(strategy_id)
            if loaded.source != "ai" or not loaded.meta.get("expert_owned"):
                raise StrategyLabError("生成策略所有权校验失败")
        except Exception:
            path.unlink(missing_ok=True)
            self.strategy_engine.reload()
            raise
        return loaded

    @staticmethod
    def _strategy_default_params(strategy: StrategyDef) -> dict[str, Any]:
        """从策略元数据提取参数默认值。"""
        return {
            str(item["id"]): item.get("default")
            for item in strategy.meta.get("params", [])
            if isinstance(item, dict) and item.get("id")
        }

    def _evaluate_generated_strategy(
        self,
        strategy_id: str,
        strategy: StrategyDef,
        latest_date: date,
    ) -> GeneratedStrategyEvaluation:
        """在训练窗优化 AI 策略，并在独立保护窗评估。"""
        from app.backtest.optimizer import StrategyOptimizer

        defaults = self._strategy_default_params(strategy)
        grid = self._optimizer_grid(strategy, defaults)
        if not grid:
            raise StrategyLabError("生成策略没有可优化的数值参数")
        windows = StrategyOptimizationWindows(
            train=BacktestWindow(
                latest_date - timedelta(days=730), latest_date - timedelta(days=366)
            ),
            protected=BacktestWindow(latest_date - timedelta(days=365), latest_date),
        )
        service = self._strategy_backtest_service()
        optimized = StrategyOptimizer(service, self.strategy_engine).optimize(
            self._optimizer_config(strategy_id, defaults, grid, windows.train)
        )
        best = optimized.get("best_params")
        if not best:
            raise StrategyLabError("生成策略优化器未返回候选参数")
        params = {**defaults, **dict(best)}
        result = self._run_strategy_backtest(service, strategy_id, params, windows.protected)
        return GeneratedStrategyEvaluation(params, optimized, result, windows)

    @staticmethod
    def _generated_strategy_metrics(
        evaluation: GeneratedStrategyEvaluation,
    ) -> dict[str, Any]:
        """构造 AI 策略训练与保护集审计指标。"""
        optimized, windows = evaluation.optimization, evaluation.windows
        return {
            "train": {
                "start": windows.train.start.isoformat(),
                "end": windows.train.end.isoformat(),
                "objective": optimized.get("objective"),
                "best_score": optimized.get("best_score"),
                "n_combinations": optimized.get("n_combinations"),
            },
            "protected": {
                "start": windows.protected.start.isoformat(),
                "end": windows.protected.end.isoformat(),
                "stats": evaluation.result.stats,
                "error": evaluation.result.error,
            },
        }

    def _persist_generated_evaluation(
        self,
        strategy_id: str,
        evaluation: GeneratedStrategyEvaluation,
        decision: tuple[OptimizationOutcome, str],
    ) -> dict[str, Any]:
        """保存 AI 策略评估、参数版本和晋级事件。"""
        outcome, reason = decision
        metrics = self._generated_strategy_metrics(evaluation)
        version = self.store.save_strategy_parameter_version(
            StrategyParameterCandidate(
                strategy_id=strategy_id,
                params=evaluation.params,
                metrics=metrics,
                status=outcome,
                reason=reason,
            )
        )
        metrics["parameter_version_id"] = version["id"]
        if outcome == "promoted":
            self.store.promote_strategy_parameters(version["id"], reason=reason, metrics=metrics)
        self.store.finish_expert_strategy_evaluation(
            strategy_id, status=outcome, metrics=metrics, reason=reason
        )
        return {"status": outcome, "reason": reason, "strategy_id": strategy_id, "metrics": metrics}

    def _mark_generation_failed(self, strategy_id: str, error: Exception) -> None:
        """把已经登记的影子策略标记为拒绝。"""
        with contextlib.suppress(ValueError):
            self.store.finish_expert_strategy_evaluation(
                strategy_id,
                status="rejected",
                metrics={},
                reason=f"generation_or_evaluation_failed:{str(error)[:200]}",
            )

    def _run_strategy_generation(self) -> dict[str, Any]:
        """生成、优化并保护集评估一个 AI 专家候选策略。"""
        if not self._operation_lock.acquire(blocking=False):
            return {"status": "reused"}
        strategy_id = f"ai_expert_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:6]}"
        recorded = False
        try:
            regime, parent_strategy_id = self._generation_regime_and_parent()
            self._record_generated_strategy_shadow(strategy_id, regime, parent_strategy_id)
            recorded = True
            loaded = self._install_generated_strategy(
                strategy_id, self._generate_strategy_source(strategy_id, regime)
            )
            latest_date = self.repo.latest_daily_date()
            if latest_date is None:
                raise StrategyDependencyUnavailableError("日线行情不可用")
            evaluation = self._evaluate_generated_strategy(strategy_id, loaded, latest_date)
            return self._persist_generated_evaluation(
                strategy_id, evaluation, self._generated_strategy_gate(evaluation.result)
            )
        except Exception as exc:
            if recorded:
                self._mark_generation_failed(strategy_id, exc)
            self._last_error = str(exc)[:500]
            logger.exception("investment expert strategy generation failed")
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
        self._refresh_historical_minute_provider()
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

    def _dataset_minute_source_plan(
        self,
        years: int,
        download_minutes: bool,
    ) -> DatasetMinuteSourcePlan:
        """计算远端分钟源与归档回退各自负责的日期窗口。"""
        start_date, end_date = self._dataset_window(years)
        remote_start = self._remote_minute_start_date(
            start_date=start_date,
            end_date=end_date,
        )
        if not download_minutes or (remote_start is not None and remote_start <= start_date):
            return DatasetMinuteSourcePlan(start_date, end_date, remote_start)
        coverage = self.historical_minute_archive.coverage()
        archive_end = end_date if remote_start is None else remote_start - timedelta(days=1)
        if remote_start is None:
            end_date = min(end_date, coverage.last_date)
            start_date = self._subtract_years(end_date, years)
            archive_end = end_date
        if start_date < coverage.first_date or archive_end > coverage.last_date:
            raise RuntimeError(
                "Hugging Face minute archive does not cover the requested "
                f"fallback window {start_date} to {archive_end}; published "
                f"coverage is {coverage.first_date} to {coverage.last_date}"
            )
        return DatasetMinuteSourcePlan(
            start_date=start_date,
            end_date=end_date,
            remote_start_date=remote_start,
            archive_start=start_date,
            archive_end=archive_end,
            archive_revision=coverage.revision,
        )

    def _dataset_source_manifest(
        self,
        plan: DatasetMinuteSourcePlan,
    ) -> dict[str, Any]:
        """构造可持久化的分钟数据源计划和初始进度。"""
        remote_name = getattr(self.historical_minute_provider, "name", "unknown")
        return {
            "minute_source": remote_name,
            "progress": {"current": 0, "total": 0, "label": None, "pct": 0.0},
            "minute_source_plan": {
                "remote_source": remote_name,
                "remote_start_date": (
                    plan.remote_start_date.isoformat()
                    if plan.remote_start_date is not None
                    else None
                ),
                "fallback_source": (
                    self.historical_minute_archive.name if plan.archive_start is not None else None
                ),
                "fallback_revision": plan.archive_revision,
                "fallback_start_date": (
                    plan.archive_start.isoformat() if plan.archive_start is not None else None
                ),
                "fallback_end_date": (
                    plan.archive_end.isoformat() if plan.archive_end is not None else None
                ),
            },
        }

    def _start_dataset_run(
        self,
        plan: DatasetMinuteSourcePlan,
    ) -> DatasetBootstrapRunState:
        """创建运行中数据集记录并返回可更新状态。"""
        progress_manifest = self._dataset_source_manifest(plan)
        run_id = self.store.record_dataset_run(
            start_date=plan.start_date,
            end_date=plan.end_date,
            status="running",
            manifest=progress_manifest,
        )
        return DatasetBootstrapRunState(plan, progress_manifest, run_id)

    def _dataset_progress_reporter(
        self,
        state: DatasetBootstrapRunState,
    ) -> Callable[[int, int, str], None]:
        """创建同时更新内存状态与持久化记录的进度回调。"""

        def report(current: int, total: int, label: str) -> None:
            """持久化单次数据集构建进度。"""
            self._dataset_progress(current, total, label)
            state.progress_manifest["progress"] = {
                "current": current,
                "total": total,
                "label": label,
                "pct": round(current * 100 / max(total, 1), 2),
            }
            self.store.record_dataset_run(
                start_date=state.plan.start_date,
                end_date=state.plan.end_date,
                status="running",
                manifest=state.progress_manifest,
                run_id=state.run_id,
            )

        return report

    def _backfill_archive_window(
        self,
        state: DatasetBootstrapRunState,
        candidate_limit: int,
        progress_cb: Callable[[int, int, str], None],
    ) -> None:
        """先生成候选池, 再补齐归档分钟源负责的窗口。"""
        plan = state.plan
        if plan.archive_start is None or plan.archive_end is None:
            return
        self.dataset_builder.build(
            start_date=plan.start_date,
            end_date=plan.end_date,
            candidate_limit=candidate_limit,
            download_minutes=False,
        )
        self.historical_minute_archive.backfill_candidates(
            candidate_dir=self.dataset_root / "candidates",
            start_date=plan.archive_start,
            end_date=plan.archive_end,
            progress_cb=progress_cb,
        )

    def _build_dataset_from_plan(
        self,
        state: DatasetBootstrapRunState,
        candidate_limit: int,
        download_minutes: bool,
        progress_cb: Callable[[int, int, str], None],
    ) -> dict[str, Any]:
        """按已审计的数据源计划构建最终训练数据集。"""
        plan = state.plan
        return self.dataset_builder.build(
            start_date=plan.start_date,
            end_date=plan.end_date,
            candidate_limit=candidate_limit,
            download_minutes=download_minutes,
            remote_minutes_enabled=plan.remote_start_date is not None,
            remote_minute_start_date=plan.remote_start_date,
            fallback_minute_source=(
                self.historical_minute_archive.name if plan.archive_start is not None else None
            ),
            fallback_minute_revision=plan.archive_revision,
            progress_cb=progress_cb,
        )

    def _finish_dataset_run(
        self,
        state: DatasetBootstrapRunState,
        manifest: dict[str, Any],
    ) -> None:
        """把数据集任务标记为成功并持久化最终清单。"""
        self.store.record_dataset_run(
            start_date=state.plan.start_date,
            end_date=state.plan.end_date,
            status="succeeded",
            manifest=manifest,
            run_id=state.run_id,
            finished=True,
        )

    def _fail_dataset_run(
        self,
        state: DatasetBootstrapRunState | None,
        error: str,
    ) -> None:
        """在已有运行记录时持久化数据集构建失败状态。"""
        if state is None:
            return
        self.store.record_dataset_run(
            start_date=state.plan.start_date,
            end_date=state.plan.end_date,
            status="failed",
            manifest=state.progress_manifest,
            run_id=state.run_id,
            error=error,
            finished=True,
        )

    def _train_after_dataset(self, download_minutes: bool) -> dict[str, Any]:
        """在已下载分钟数据时训练初始模型, 失败不回滚数据集。"""
        if not download_minutes:
            return {"status": "skipped", "reason": "minute_download_disabled"}
        try:
            return self._train_model_locked()
        except Exception as exc:
            self._last_error = str(exc)[:500]
            logger.exception("dataset succeeded but initial model training failed")
            return {"status": "failed", "error": self._last_error}

    def _run_dataset_bootstrap(
        self,
        years: int,
        candidate_limit: int,
        download_minutes: bool,
    ) -> dict[str, Any]:
        """互斥执行训练数据构建、归档回填和可选初始训练。"""
        if not self._operation_lock.acquire(blocking=False):
            return {"status": "reused"}
        state: DatasetBootstrapRunState | None = None
        try:
            plan = self._dataset_minute_source_plan(years, download_minutes)
            state = self._start_dataset_run(plan)
            self._ensure_daily_history(plan.start_date - timedelta(days=40), plan.end_date)
            report_progress = self._dataset_progress_reporter(state)
            self._backfill_archive_window(state, candidate_limit, report_progress)
            manifest = self._build_dataset_from_plan(
                state, candidate_limit, download_minutes, report_progress
            )
            self._finish_dataset_run(state, manifest)
            training = self._train_after_dataset(download_minutes)
            return {"status": "succeeded", "manifest": manifest, "training": training}
        except Exception as exc:
            self._last_error = str(exc)[:500]
            self._fail_dataset_run(state, self._last_error)
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
        model = ExpertModelTrainer(self.dataset_root, self.constitution).train(version=next_version)
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
            promoted = protected["brier"] < float(active_metrics.get("brier", 1.0)) and (
                protected["selected_mean_net_return"] or float("-inf")
            ) >= float(active_metrics.get("selected_mean_net_return") or float("-inf"))
            if not promoted:
                reason = "active_model_did_not_improve"
        elif not promoted:
            reason = "protected_model_gate_rejected"
        if promoted:
            self.store.promote_model(model.id, reason=reason, metrics=model.metrics)
        return {"status": "promoted" if promoted else "shadow", "model": model.id, "reason": reason}

    def list_sessions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """返回最近的模拟交易会话。"""

        return self.store.list_sessions(limit=limit)

    def list_execution_events(
        self,
        *,
        session_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """返回执行事件，可按会话过滤。"""

        return self.store.list_execution_events(session_id=session_id, limit=limit)

    def list_trade_history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """返回带决策上下文的成交历史。"""

        return self.store.list_trade_history(limit=limit)

    def policy_catalog(self) -> dict[str, Any]:
        """返回策略版本目录及当前冠军策略。"""

        champion = self.store.get_champion()
        return {
            "champion_id": champion.id if champion else None,
            "policies": [policy.model_dump(mode="json") for policy in self.store.list_policies()],
        }

    def model_catalog(self) -> dict[str, Any]:
        """返回模型版本目录及当前启用模型。"""

        active = self.store.get_active_model()
        return {
            "active_model_id": active.id if active else None,
            "models": [model.model_dump(mode="json") for model in self.store.list_models()],
        }

    def list_experiments(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """返回最近的策略进化实验。"""

        return self.store.list_experiments(limit=limit)

    @staticmethod
    def _position_views(
        lots: list[dict[str, Any]],
        last_prices: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], float | None, int]:
        rows: list[dict[str, Any]] = []
        priced_unrealized_pnl = 0.0
        unpriced_count = 0
        for item in lots:
            row = dict(item)
            symbol = str(row.get("symbol") or "")
            shares = int(row.get("shares") or 0)
            remaining_shares = int(row.get("remaining_shares") or 0)
            entry_price = float(row.get("entry_price") or 0.0)
            entry_cost = float(row.get("entry_cost") or 0.0)
            allocated_entry_cost = entry_cost * remaining_shares / shares if shares > 0 else 0.0
            cost_basis = remaining_shares * entry_price + allocated_entry_cost
            try:
                market_price = float(last_prices[symbol])
            except (KeyError, TypeError, ValueError):
                market_price = None
            if market_price is not None and (not math.isfinite(market_price) or market_price <= 0):
                market_price = None

            if market_price is None:
                market_value = None
                unrealized_pnl = None
                unrealized_pnl_pct = None
                unpriced_count += 1
            else:
                market_value = remaining_shares * market_price
                unrealized_pnl = market_value - cost_basis
                unrealized_pnl_pct = unrealized_pnl / cost_basis if cost_basis > 0 else None
                priced_unrealized_pnl += unrealized_pnl

            row.update(
                {
                    "entry_cost": round(entry_cost, 2),
                    "cost_basis": round(cost_basis, 2),
                    "market_price": round(market_price, 4) if market_price is not None else None,
                    "market_value": round(market_value, 2) if market_value is not None else None,
                    "unrealized_pnl": (
                        round(unrealized_pnl, 2) if unrealized_pnl is not None else None
                    ),
                    "unrealized_pnl_pct": (
                        round(unrealized_pnl_pct, 6) if unrealized_pnl_pct is not None else None
                    ),
                }
            )
            rows.append(row)
        unrealized_pnl = round(priced_unrealized_pnl, 2) if unpriced_count == 0 else None
        return rows, unrealized_pnl, unpriced_count

    def status(self) -> dict[str, Any]:
        from app.services.ai_provider import ai_configured

        future = self._active_future
        if future is None or future.done():
            self._refresh_historical_minute_provider()
        base = self.store.status()
        executor = self._executor
        snapshot = self.store.latest_portfolio_state()
        latest_portfolio_sync = self.store.latest_portfolio_sync()
        snapshot_payload = (snapshot or {}).get("payload") or {}
        snapshot_executor_state = snapshot_payload.get("executor_state") or {}
        if executor is not None:
            cash = round(executor.cash, 2)
            equity = round(executor.equity(), 2)
            raw_positions = [lot.model_dump(mode="json") for lot in executor.lots]
            last_prices = dict(executor.last_prices)
            pending_order_count = len(executor.pending)
        else:
            cash = (
                round(float(snapshot["cash"]), 2) if snapshot else self.constitution.initial_capital
            )
            equity = (
                round(float(snapshot["equity"]), 2)
                if snapshot
                else self.constitution.initial_capital
            )
            raw_positions = list(
                snapshot_payload.get("lots") or snapshot_executor_state.get("lots") or []
            )
            last_prices = dict(
                snapshot_payload.get("last_prices")
                or snapshot_executor_state.get("last_prices")
                or {}
            )
            pending_order_count = len(snapshot_executor_state.get("pending") or [])
        positions, unrealized_pnl, unpriced_position_count = self._position_views(
            raw_positions,
            last_prices,
        )
        execution_statistics = self.store.execution_statistics()
        portfolio_baseline_equity = (
            float(latest_portfolio_sync["equity"])
            if latest_portfolio_sync is not None
            else self.constitution.initial_capital
        )
        total_pnl = round(equity - portfolio_baseline_equity, 2)
        total_return = (
            total_pnl / portfolio_baseline_equity if portfolio_baseline_equity > 0 else 0.0
        )
        valuation_as_of = (
            snapshot.get("as_of")
            if snapshot
            else self._last_processed_bar.isoformat() if self._last_processed_bar else None
        )
        performance = {
            **execution_statistics,
            "position_count": len({row["symbol"] for row in positions}),
            "position_lot_count": len(positions),
            "unpriced_position_count": unpriced_position_count,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": total_pnl,
            "total_return": round(total_return, 6),
            "valuation_as_of": valuation_as_of,
        }
        historical_capable = bool(
            self._historical_minute_provider_error is None
            and (
                getattr(self.historical_minute_provider, "name", "tickflow") != "tickflow"
                or self.capset is None
                or self.capset.has(Cap.KLINE_MINUTE_BATCH)
            )
        )
        max_history_years = self._historical_minute_max_years()
        remote_three_year_capable = self._remote_historical_minute_capable(3)
        local_minute_bounds = self._local_historical_minute_bounds()
        archive_fallback_capable = self.historical_minute_archive is not None
        three_year_capable = remote_three_year_capable or archive_fallback_capable
        three_year_error = None
        news_sentiment = None
        if self._news_sentiment_context is not None:
            news_sentiment = {
                **self._news_sentiment_context,
                "items": list(self._news_sentiment_context.get("items") or [])[:8],
            }
        persisted_orchestration = self.store.latest_strategy_orchestration()
        strategy_orchestration = self._strategy_orchestration or (
            persisted_orchestration.get("payload") if persisted_orchestration else None
        )
        base.update(
            {
                "running": bool(self._poll_thread and self._poll_thread.is_alive()),
                "active_task": (
                    self._active_task if future is not None and not future.done() else None
                ),
                "last_error": self._last_error,
                "session_id": self._session["id"] if self._session else None,
                "candidate_count": len(self._candidates),
                "market_symbol_count": len(self._market_symbols),
                "cash": cash,
                "equity": equity,
                "positions": positions,
                "performance": performance,
                "portfolio_baseline_equity": round(portfolio_baseline_equity, 2),
                "portfolio_sync": (
                    {
                        "id": latest_portfolio_sync["id"],
                        "source": latest_portfolio_sync["source"],
                        "mode": latest_portfolio_sync["mode"],
                        "created_at": latest_portfolio_sync["created_at"],
                        "source_updated_at": (latest_portfolio_sync.get("payload") or {}).get(
                            "source_updated_at"
                        ),
                        "position_count": int(
                            (latest_portfolio_sync.get("payload") or {}).get(
                                "position_count",
                                0,
                            )
                        ),
                        "cash": round(float(latest_portfolio_sync["cash"]), 2),
                        "equity": round(float(latest_portfolio_sync["equity"]), 2),
                    }
                    if latest_portfolio_sync is not None
                    else None
                ),
                "pending_order_count": pending_order_count,
                "holding_period": {
                    "minimum_trading_days": self.constitution.min_hold_trading_days,
                    "maximum_trading_days": self.constitution.max_hold_trading_days,
                    "stop_loss_exempt": True,
                },
                "entries_enabled": bool(self._runtime is None or self._runtime.entries_enabled),
                "risk_trip_reason": self._risk_trip_reason,
                "overnight_us_market": self._overnight_us_context,
                "news_sentiment": news_sentiment,
                "strategy_orchestration": strategy_orchestration,
                "expert_strategies": self.store.list_expert_strategies(limit=20),
                "strategy_parameter_versions": self.store.active_strategy_parameters(),
                "strategy_parameter_experiments": self.store.list_strategy_parameter_versions(
                    limit=20
                ),
                "ai_strategy_generation_available": ai_configured(),
                "session_prepare_error": self._prepare_failure_reason,
                "minute_capable": bool(
                    self.capset is None or self.capset.has(Cap.KLINE_MINUTE_BATCH)
                ),
                "live_minute_source": getattr(self.minute_provider, "name", "unknown"),
                "historical_minute_source": getattr(
                    self.historical_minute_provider,
                    "name",
                    "unknown",
                ),
                "historical_minute_error": self._historical_minute_provider_error,
                "historical_minute_capable": historical_capable,
                "historical_minute_max_years": max_history_years,
                "historical_minute_remote_three_year_capable": remote_three_year_capable,
                "historical_minute_archive_fallback_capable": archive_fallback_capable,
                "historical_minute_archive_fallback_source": getattr(
                    self.historical_minute_archive,
                    "name",
                    None,
                ),
                "historical_minute_local_coverage": {
                    asset_type: {
                        "start": start.isoformat() if start else None,
                        "end": end.isoformat() if end else None,
                    }
                    for asset_type, (start, end) in local_minute_bounds.items()
                },
                "historical_minute_three_year_capable": three_year_capable,
                "historical_minute_three_year_error": three_year_error,
                "live_minute_mode": (
                    "intraday_batch"
                    if self.capset is not None and self.capset.has(Cap.INTRADAY_BATCH)
                    else "historical_batch_fallback"
                ),
            }
        )
        return base
