"""Validation-gated single-variable policy evolution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from app.paper_agent.execution import StrictMinuteExecutor
from app.paper_agent.models import (
    ExpertPolicy,
    MinuteBar,
    RiskConstitution,
    TrainedDecisionModel,
)
from app.paper_agent.runtime import InvestmentExpertRuntime


@dataclass(frozen=True)
class EvaluationMetrics:
    total_return: float
    max_drawdown: float
    closed_trades: int
    win_rate: float | None
    expectancy: float | None
    violations: int
    processed_dates: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_return": self.total_return,
            "max_drawdown": self.max_drawdown,
            "closed_trades": self.closed_trades,
            "win_rate": self.win_rate,
            "expectancy": self.expectancy,
            "violations": self.violations,
            "processed_dates": self.processed_dates,
        }


class PolicyEvaluator:
    def __init__(
        self,
        dataset_root: Path,
        constitution: RiskConstitution | None = None,
        decision_model: TrainedDecisionModel | None = None,
    ) -> None:
        self.dataset_root = dataset_root
        self.constitution = constitution or RiskConstitution()
        self.decision_model = decision_model

    def evaluate(
        self,
        policy: ExpertPolicy,
        *,
        start_date=None,
        end_date=None,
    ) -> EvaluationMetrics:
        candidate_dir = self.dataset_root / "candidates"
        minute_dir = self.dataset_root / "minute"
        executor = StrictMinuteExecutor(self.constitution)
        equity_curve = [executor.equity()]
        processed_dates = 0
        violations = 0
        for candidate_path in sorted(candidate_dir.glob("date=*/part.parquet")):
            trade_date = datetime.fromisoformat(candidate_path.parent.name.split("=", 1)[1]).date()
            if start_date and trade_date < start_date:
                continue
            if end_date and trade_date > end_date:
                continue
            minute_path = minute_dir / candidate_path.parent.name / "part.parquet"
            if not minute_path.exists():
                continue
            candidate_frame = pl.read_parquet(candidate_path)
            candidates = set(candidate_frame["symbol"].to_list())
            candidate_context = {
                str(row["symbol"]): {
                    "daily_momentum_20d": row.get("_momentum_20d"),
                    "score": row.get("score"),
                }
                for row in candidate_frame.iter_rows(named=True)
            }
            runtime = InvestmentExpertRuntime(
                session_id=f"replay_{trade_date}_{policy.id}",
                policy=policy,
                candidates=candidates,
                executor=executor,
                candidate_context=candidate_context,
                decision_model=self.decision_model,
            )
            minute = pl.read_parquet(minute_path).sort(["datetime", "symbol"])
            for row in minute.iter_rows(named=True):
                try:
                    bar = self._replay_bar(row)
                    step = runtime.on_bar(bar)
                    violations += sum(
                        event.event_type == "data_rejected" for event in step.execution_events
                    )
                except (TypeError, ValueError):
                    violations += 1
            equity_curve.append(executor.equity())
            processed_dates += 1
        initial = self.constitution.initial_capital
        total_return = equity_curve[-1] / initial - 1 if equity_curve else 0.0
        peak = equity_curve[0] if equity_curve else initial
        max_drawdown = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            max_drawdown = min(max_drawdown, value / peak - 1 if peak else 0.0)
        closed = [event.realized_pnl for event in executor.events if event.realized_pnl is not None]
        win_rate = sum(value > 0 for value in closed) / len(closed) if closed else None
        expectancy = sum(closed) / len(closed) / initial if closed else None
        return EvaluationMetrics(
            total_return=round(total_return, 8),
            max_drawdown=round(max_drawdown, 8),
            closed_trades=len(closed),
            win_rate=round(win_rate, 6) if win_rate is not None else None,
            expectancy=round(expectancy, 8) if expectancy is not None else None,
            violations=violations,
            processed_dates=processed_dates,
        )

    @staticmethod
    def _replay_bar(row: dict[str, Any]) -> MinuteBar:
        bar_time = row["datetime"]
        return MinuteBar(
            symbol=str(row["symbol"]),
            datetime=bar_time,
            received_at=bar_time + timedelta(minutes=1, seconds=1),
            raw_open=float(row.get("raw_open", row["open"])),
            raw_high=float(row.get("raw_high", row["high"])),
            raw_low=float(row.get("raw_low", row["low"])),
            raw_close=float(row.get("raw_close", row["close"])),
            volume=float(row["volume"]),
            amount=float(row["amount"]),
            complete=True,
            is_suspended=bool(row.get("is_suspended", False)),
            is_limit_up=bool(row.get("is_limit_up", False)),
            is_limit_down=bool(row.get("is_limit_down", False)),
        )


class PolicyEvolutionEngine:
    """Create exactly one bounded mutation and apply an automatic ratchet gate."""

    MUTATION_FIELDS = ("min_vwap_bias", "min_breakout_pct", "exit_vwap_bias", "target_position_pct")

    def propose(
        self,
        champion: ExpertPolicy,
        reflection: dict[str, Any],
        *,
        next_version: int | None = None,
    ) -> tuple[ExpertPolicy, str]:
        loss_rate = float(reflection.get("loss_rate", 0.5))
        mutation_field = "min_vwap_bias" if loss_rate >= 0.5 else "min_breakout_pct"
        current = float(getattr(champion, mutation_field))
        step = 0.0005 if loss_rate >= 0.5 else -0.00025
        if mutation_field == "min_breakout_pct":
            value = max(0.0, min(0.10, current + step))
        else:
            value = max(-0.05, min(0.10, current + step))
        version = next_version or champion.version + 1
        candidate = champion.model_copy(update={
            "id": f"expert_v{version}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
            "version": version,
            "parent_id": champion.id,
            "status": "candidate",
            mutation_field: value,
            "mutation_note": f"{mutation_field}: {current:.6f} -> {value:.6f}",
        })
        return candidate, mutation_field

    @staticmethod
    def gate(
        champion: EvaluationMetrics,
        candidate: EvaluationMetrics,
        *,
        min_closed_trades: int = 30,
    ) -> tuple[str, str]:
        if candidate.violations > 0:
            return "rejected", "anti_cheat_or_data_quality_violation"
        if candidate.processed_dates == 0:
            return "rejected", "no_protected_evaluation_data"
        if candidate.closed_trades < min_closed_trades:
            return "shadow", "insufficient_closed_trades"
        champion_expectancy = champion.expectancy if champion.expectancy is not None else float("-inf")
        candidate_expectancy = candidate.expectancy if candidate.expectancy is not None else float("-inf")
        if candidate_expectancy <= champion_expectancy:
            return "rejected", "expectancy_did_not_improve"
        if candidate.max_drawdown < champion.max_drawdown - 0.01:
            return "rejected", "max_drawdown_regressed"
        if candidate.total_return < champion.total_return:
            return "rejected", "net_return_regressed"
        return "promoted", "protected_evaluation_passed"
