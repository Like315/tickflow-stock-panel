"""Stable contracts for the investment-expert paper agent."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RiskConstitution(BaseModel):
    """Non-evolvable execution and portfolio boundaries."""

    model_config = ConfigDict(frozen=True)

    initial_capital: float = Field(default=1_000_000.0, gt=0)
    max_positions: int = Field(default=5, ge=1, le=50)
    max_exposure_pct: float = Field(default=0.60, gt=0, le=1)
    max_position_pct: float = Field(default=0.20, gt=0, le=1)
    max_volume_participation: float = Field(default=0.05, gt=0, le=0.20)
    lot_size: int = Field(default=100, ge=1)
    volume_unit_shares: int = Field(default=100, ge=1)
    commission_pct: float = Field(default=0.0002, ge=0, le=0.01)
    min_commission: float = Field(default=5.0, ge=0)
    stamp_tax_pct: float = Field(default=0.0005, ge=0, le=0.01)
    slippage_bps: float = Field(default=5.0, ge=0, le=100)
    max_data_lag_seconds: int = Field(default=90, ge=1, le=600)
    buy_order_ttl_minutes: int = Field(default=1, ge=1, le=30)
    max_daily_loss_pct: float = Field(default=0.03, gt=0, le=0.20)
    max_total_drawdown_pct: float = Field(default=0.15, gt=0, le=0.50)


class ExpertPolicy(BaseModel):
    """The only evolvable asset; every version is immutable once persisted."""

    model_config = ConfigDict(frozen=True)

    id: str
    version: int = Field(ge=1)
    parent_id: str | None = None
    status: Literal["champion", "candidate", "shadow", "rejected", "retired"] = "candidate"
    candidate_limit: int = Field(default=50, ge=5, le=200)
    min_completed_bars: int = Field(default=2, ge=1, le=30)
    entry_start: str = Field(default="09:31", pattern=r"^\d{2}:\d{2}$")
    entry_end: str = Field(default="14:30", pattern=r"^\d{2}:\d{2}$")
    min_vwap_bias: float = Field(default=0.001, ge=-0.05, le=0.10)
    min_breakout_pct: float = Field(default=0.001, ge=0, le=0.10)
    entry_probability_threshold: float = Field(default=0.55, ge=0.50, le=0.95)
    overnight_us_candidate_weight: float = Field(default=0.15, ge=0, le=0.50)
    overnight_us_entry_weight: float = Field(default=0.10, ge=0, le=0.50)
    overnight_us_exit_weight: float = Field(default=0.08, ge=0, le=0.50)
    news_candidate_weight: float = Field(default=0.25, ge=0, le=0.50)
    exit_vwap_bias: float = Field(default=-0.002, ge=-0.10, le=0.05)
    stop_loss_pct: float = Field(default=-0.05, ge=-0.30, le=-0.001)
    take_profit_pct: float = Field(default=0.08, ge=0.001, le=0.50)
    max_hold_days: int = Field(default=10, ge=1, le=60)
    target_position_pct: float = Field(default=0.10, gt=0, le=0.50)
    mutation_note: str = "baseline"

    @model_validator(mode="after")
    def validate_window(self) -> ExpertPolicy:
        if self.entry_start >= self.entry_end:
            raise ValueError("entry_start must be earlier than entry_end")
        return self


class TrainedDecisionModel(BaseModel):
    """Immutable, chronologically trained probability model used only as an entry gate."""

    model_config = ConfigDict(frozen=True)

    id: str
    version: int = Field(ge=1)
    feature_names: list[str]
    weights: list[float]
    intercept: float
    means: list[float]
    scales: list[float]
    trained_start: date
    trained_end: date
    sample_count: int = Field(ge=1)
    dataset_manifest_hash: str
    metrics: dict[str, Any]

    @model_validator(mode="after")
    def validate_vectors(self) -> TrainedDecisionModel:
        size = len(self.feature_names)
        if size == 0 or not all(
            len(values) == size for values in (self.weights, self.means, self.scales)
        ):
            raise ValueError("decision model vectors must have identical non-zero lengths")
        if any(scale <= 0 for scale in self.scales):
            raise ValueError("decision model scales must be positive")
        return self

    def predict_probability(self, features: dict[str, Any]) -> float | None:
        values: list[float] = []
        for name in self.feature_names:
            raw = features.get(name)
            if raw is None:
                return None
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(value):
                return None
            values.append(value)
        score = self.intercept + sum(
            weight * ((value - mean) / scale)
            for weight, value, mean, scale in zip(
                self.weights, values, self.means, self.scales, strict=True
            )
        )
        score = max(-35.0, min(35.0, score))
        return 1.0 / (1.0 + math.exp(-score))


class MinuteBar(BaseModel):
    symbol: str
    datetime: datetime
    received_at: datetime
    raw_open: float = Field(gt=0)
    raw_high: float = Field(gt=0)
    raw_low: float = Field(gt=0)
    raw_close: float = Field(gt=0)
    volume: float = Field(ge=0)
    amount: float = Field(ge=0)
    complete: bool = True
    is_suspended: bool = False
    is_limit_up: bool = False
    is_limit_down: bool = False

    @model_validator(mode="after")
    def validate_ohlc(self) -> MinuteBar:
        highest = max(self.raw_open, self.raw_close, self.raw_low)
        lowest = min(self.raw_open, self.raw_close, self.raw_high)
        # Provider floats can carry machine-scale residue around the exchange
        # tick price; tolerate only that residue, not materially invalid OHLC.
        if self.raw_high < highest and not math.isclose(
            self.raw_high, highest, rel_tol=1e-12, abs_tol=1e-9
        ):
            raise ValueError("raw_high is inconsistent with OHLC")
        if self.raw_low > lowest and not math.isclose(
            self.raw_low, lowest, rel_tol=1e-12, abs_tol=1e-9
        ):
            raise ValueError("raw_low is inconsistent with OHLC")
        return self


class OrderIntent(BaseModel):
    id: str
    decision_id: str
    symbol: str
    side: Literal["buy", "sell"]
    shares: int = Field(gt=0)
    signal_time: datetime
    reason: str


class PositionLot(BaseModel):
    lot_id: str
    symbol: str
    acquired_date: date
    shares: int = Field(gt=0)
    remaining_shares: int = Field(gt=0)
    entry_price: float = Field(gt=0)
    entry_cost: float = Field(ge=0)


class ExecutionEvent(BaseModel):
    id: str
    event_type: Literal[
        "order_submitted",
        "order_filled",
        "order_partially_filled",
        "order_rejected",
        "order_blocked",
        "data_rejected",
    ]
    occurred_at: datetime
    order_id: str | None = None
    symbol: str | None = None
    side: Literal["buy", "sell"] | None = None
    shares: int = 0
    price: float | None = None
    fees: float = 0.0
    realized_pnl: float | None = None
    reason: str = ""
    cash_after: float | None = None
