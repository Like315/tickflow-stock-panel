"""Chronological, leakage-safe training for the investment expert entry gate."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import polars as pl

from app.paper_agent.models import RiskConstitution, TrainedDecisionModel

FEATURE_NAMES = (
    "vwap_bias",
    "breakout_pct",
    "daily_momentum_20d",
    "candidate_score",
)


@dataclass(frozen=True)
class _Sample:
    trade_date: date
    values: tuple[float, ...]
    label: int
    net_return: float


class ExpertModelTrainer:
    """Fit a deterministic logistic model with train/validation/protected date splits."""

    def __init__(
        self,
        dataset_root: Path,
        constitution: RiskConstitution | None = None,
    ) -> None:
        self.dataset_root = dataset_root
        self.constitution = constitution or RiskConstitution()

    def train(self, *, version: int) -> TrainedDecisionModel:
        samples = self._load_samples()
        dates = sorted({sample.trade_date for sample in samples})
        if len(dates) < 30 or len(samples) < 300:
            raise ValueError("at least 30 trading dates and 300 executable T+1 samples are required")
        train_end = max(1, int(len(dates) * 0.70))
        validation_end = max(train_end + 1, int(len(dates) * 0.85))
        train_dates = set(dates[:train_end])
        validation_dates = set(dates[train_end:validation_end])
        protected_dates = set(dates[validation_end:])
        train = [sample for sample in samples if sample.trade_date in train_dates]
        validation = [sample for sample in samples if sample.trade_date in validation_dates]
        protected = [sample for sample in samples if sample.trade_date in protected_dates]
        if not validation or not protected:
            raise ValueError("chronological validation/protected splits are empty")

        means, scales = self._fit_scaler(train)
        weights, intercept = self._fit_logistic(train, means, scales)
        metrics = {
            "split": "chronological_70_15_15_by_trade_date",
            "train": self._metrics(train, weights, intercept, means, scales),
            "validation": self._metrics(validation, weights, intercept, means, scales),
            "protected_test": self._metrics(protected, weights, intercept, means, scales),
            "anti_leakage": "features_at_10:01; entry_next_minute_open; label_next_trade_day_09:31_open",
        }
        manifest_path = self.dataset_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        trained_at = datetime.now(UTC)
        return TrainedDecisionModel(
            id=f"entry_model_v{version}_{trained_at.strftime('%Y%m%d%H%M%S')}",
            version=version,
            feature_names=list(FEATURE_NAMES),
            weights=[round(value, 10) for value in weights],
            intercept=round(intercept, 10),
            means=[round(value, 10) for value in means],
            scales=[round(value, 10) for value in scales],
            trained_start=dates[0],
            trained_end=dates[-1],
            sample_count=len(samples),
            dataset_manifest_hash=str(manifest.get("manifest_hash") or "unversioned"),
            metrics=metrics,
        )

    def _load_samples(self) -> list[_Sample]:
        candidate_paths = sorted((self.dataset_root / "candidates").glob("date=*/part.parquet"))
        dated_paths = [
            (date.fromisoformat(path.parent.name.split("=", 1)[1]), path)
            for path in candidate_paths
        ]
        manifest_path = self.dataset_root / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            try:
                manifest_start = date.fromisoformat(str(manifest["start_date"]))
                manifest_end = date.fromisoformat(str(manifest["end_date"]))
            except (KeyError, ValueError):
                pass
            else:
                dated_paths = [
                    item for item in dated_paths
                    if manifest_start <= item[0] <= manifest_end
                ]
        samples: list[_Sample] = []
        for index, (trade_date, candidate_path) in enumerate(dated_paths[:-1]):
            next_date = dated_paths[index + 1][0]
            current_minute_path = self.dataset_root / "minute" / f"date={trade_date}" / "part.parquet"
            next_minute_path = self.dataset_root / "minute" / f"date={next_date}" / "part.parquet"
            if not current_minute_path.exists() or not next_minute_path.exists():
                continue
            candidates = pl.read_parquet(candidate_path)
            current = pl.read_parquet(current_minute_path)
            next_day = pl.read_parquet(next_minute_path)
            if current.is_empty() or next_day.is_empty():
                continue
            for row in candidates.iter_rows(named=True):
                sample = self._sample_symbol(
                    trade_date,
                    str(row["symbol"]),
                    float(row["_momentum_20d"]),
                    float(row["score"]),
                    current,
                    next_day,
                )
                if sample is not None:
                    samples.append(sample)
        return samples

    def _sample_symbol(
        self,
        trade_date: date,
        symbol: str,
        momentum: float,
        score: float,
        current: pl.DataFrame,
        next_day: pl.DataFrame,
    ) -> _Sample | None:
        current_symbol = current.filter(pl.col("symbol") == symbol).sort("datetime")
        next_symbol = next_day.filter(pl.col("symbol") == symbol).sort("datetime")
        if current_symbol.height < 3 or next_symbol.is_empty():
            return None
        rows = current_symbol.iter_rows(named=True)
        visible: list[dict[str, Any]] = []
        entry_row: dict[str, Any] | None = None
        for row in rows:
            bar_time = row["datetime"].time()
            if bar_time <= time(10, 0):
                visible.append(row)
                continue
            entry_row = row
            break
        if len(visible) < 2 or entry_row is None:
            return None
        exit_row = next(
            (
                row
                for row in next_symbol.iter_rows(named=True)
                if row["datetime"].time() >= time(9, 31)
            ),
            None,
        )
        if exit_row is None:
            return None
        if (
            bool(entry_row.get("is_suspended", False))
            or bool(entry_row.get("is_limit_up", False))
            or bool(exit_row.get("is_suspended", False))
            or bool(exit_row.get("is_limit_down", False))
        ):
            return None
        cumulative_volume = sum(float(row["volume"]) for row in visible)
        cumulative_amount = sum(float(row["amount"]) for row in visible)
        volume_shares = cumulative_volume * self.constitution.volume_unit_shares
        if volume_shares <= 0 or cumulative_amount <= 0:
            return None
        close = float(visible[-1].get("raw_close", visible[-1]["close"]))
        vwap = cumulative_amount / volume_shares
        previous_high = max(
            float(row.get("raw_high", row["high"])) for row in visible[:-1]
        )
        if vwap <= 0 or previous_high <= 0:
            return None
        entry = float(entry_row.get("raw_open", entry_row["open"])) * (
            1 + self.constitution.slippage_bps / 10_000
        )
        exit_price = float(exit_row.get("raw_open", exit_row["open"])) * (
            1 - self.constitution.slippage_bps / 10_000
        )
        target_value = self.constitution.initial_capital * 0.10
        shares = max(
            self.constitution.lot_size,
            int(target_value / entry) // self.constitution.lot_size
            * self.constitution.lot_size,
        )
        notional = entry * shares
        buy_fee = max(
            self.constitution.min_commission,
            notional * self.constitution.commission_pct,
        )
        exit_notional = exit_price * shares
        sell_fee = max(
            self.constitution.min_commission,
            exit_notional * self.constitution.commission_pct,
        ) + exit_notional * self.constitution.stamp_tax_pct
        net_return = (
            exit_notional - sell_fee - notional - buy_fee
        ) / (notional + buy_fee)
        values = (
            close / vwap - 1,
            close / previous_high - 1,
            momentum,
            score,
        )
        if not all(math.isfinite(value) for value in (*values, net_return)):
            return None
        return _Sample(
            trade_date=trade_date,
            values=values,
            label=int(net_return > 0),
            net_return=net_return,
        )

    @staticmethod
    def _fit_scaler(samples: list[_Sample]) -> tuple[list[float], list[float]]:
        width = len(FEATURE_NAMES)
        means = [sum(sample.values[i] for sample in samples) / len(samples) for i in range(width)]
        scales = [
            max(
                math.sqrt(
                    sum((sample.values[i] - means[i]) ** 2 for sample in samples)
                    / len(samples)
                ),
                1e-8,
            )
            for i in range(width)
        ]
        return means, scales

    @staticmethod
    def _fit_logistic(
        samples: list[_Sample],
        means: list[float],
        scales: list[float],
    ) -> tuple[list[float], float]:
        width = len(FEATURE_NAMES)
        weights = [0.0] * width
        positive = sum(sample.label for sample in samples)
        negative = len(samples) - positive
        if positive == 0 or negative == 0:
            raise ValueError("training labels contain only one class")
        intercept = math.log(positive / negative)
        positive_weight = len(samples) / (2 * positive)
        negative_weight = len(samples) / (2 * negative)
        learning_rate = 0.04
        l2 = 0.002
        for epoch in range(24):
            rate = learning_rate / (1 + epoch * 0.08)
            for sample in samples:
                scaled = [
                    (sample.values[i] - means[i]) / scales[i] for i in range(width)
                ]
                score = max(-35.0, min(35.0, intercept + sum(
                    weights[i] * scaled[i] for i in range(width)
                )))
                probability = 1 / (1 + math.exp(-score))
                class_weight = positive_weight if sample.label else negative_weight
                error = (probability - sample.label) * class_weight
                intercept -= rate * error
                for i in range(width):
                    weights[i] -= rate * (error * scaled[i] + l2 * weights[i])
            weights = [max(-8.0, min(8.0, value)) for value in weights]
            intercept = max(-8.0, min(8.0, intercept))
        return weights, intercept

    @staticmethod
    def _metrics(
        samples: list[_Sample],
        weights: list[float],
        intercept: float,
        means: list[float],
        scales: list[float],
    ) -> dict[str, Any]:
        probabilities: list[float] = []
        for sample in samples:
            score = intercept + sum(
                weights[i] * ((sample.values[i] - means[i]) / scales[i])
                for i in range(len(weights))
            )
            score = max(-35.0, min(35.0, score))
            probabilities.append(1 / (1 + math.exp(-score)))
        brier = sum(
            (probability - sample.label) ** 2
            for probability, sample in zip(probabilities, samples, strict=True)
        ) / len(samples)
        positive_rate = sum(sample.label for sample in samples) / len(samples)
        baseline_brier = sum(
            (positive_rate - sample.label) ** 2 for sample in samples
        ) / len(samples)
        accuracy = sum(
            (probability >= 0.5) == bool(sample.label)
            for probability, sample in zip(probabilities, samples, strict=True)
        ) / len(samples)
        selected_returns = [
            sample.net_return
            for probability, sample in zip(probabilities, samples, strict=True)
            if probability >= 0.55
        ]
        return {
            "samples": len(samples),
            "positive_rate": round(positive_rate, 8),
            "brier": round(brier, 8),
            "baseline_brier": round(baseline_brier, 8),
            "accuracy": round(accuracy, 8),
            "selected": len(selected_returns),
            "selected_mean_net_return": round(
                sum(selected_returns) / len(selected_returns), 8
            ) if selected_returns else None,
        }
