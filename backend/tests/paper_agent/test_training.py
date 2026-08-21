from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from app.paper_agent.models import TrainedDecisionModel
from app.paper_agent.store import PaperAgentStore
from app.paper_agent.training import ExpertModelTrainer


def _write_training_fixture(root: Path) -> None:
    start = date(2024, 1, 2)
    symbols = [f"SH.{600000 + index}" for index in range(10)]
    for day_index in range(40):
        trade_date = start + timedelta(days=day_index)
        candidate_path = root / "candidates" / f"date={trade_date}" / "part.parquet"
        minute_path = root / "minute" / f"date={trade_date}" / "part.parquet"
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        minute_path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({
            "trade_date": [trade_date] * len(symbols),
            "source_date": [trade_date - timedelta(days=1)] * len(symbols),
            "symbol": symbols,
            "score": [0.8 if index % 2 == 0 else 0.2 for index in range(len(symbols))],
            "_momentum_20d": [
                0.12 if index % 2 == 0 else -0.12 for index in range(len(symbols))
            ],
            "_amount": [1_000_000.0] * len(symbols),
        }).write_parquet(candidate_path)
        rows = []
        for symbol_index, symbol in enumerate(symbols):
            direction = 1.03 if symbol_index % 2 == 0 else 0.97
            base = 10.0 * direction**day_index
            for clock, multiplier in (
                ("09:30", 0.998),
                ("09:31", 1.0),
                ("10:00", 1.004 if symbol_index % 2 == 0 else 0.996),
                ("10:01", 1.005 if symbol_index % 2 == 0 else 0.995),
            ):
                bar_time = datetime.fromisoformat(f"{trade_date}T{clock}:00")
                price = base * multiplier
                rows.append({
                    "symbol": symbol,
                    "datetime": bar_time,
                    "open": price,
                    "high": price * 1.001,
                    "low": price * 0.999,
                    "close": price,
                    "raw_open": price,
                    "raw_high": price * 1.001,
                    "raw_low": price * 0.999,
                    "raw_close": price,
                    "volume": 10_000.0,
                    "amount": price * 10_000.0 * 100,
                })
        pl.DataFrame(rows).write_parquet(minute_path)
    (root / "manifest.json").write_text(
        json.dumps({"manifest_hash": "fixture-hash"}), encoding="utf-8"
    )


def test_chronological_model_training_and_immutable_promotion(tmp_path: Path) -> None:
    dataset_root = tmp_path / "training"
    _write_training_fixture(dataset_root)
    model = ExpertModelTrainer(dataset_root).train(version=1)

    assert model.sample_count == 390
    assert model.metrics["split"] == "chronological_70_15_15_by_trade_date"
    assert model.metrics["protected_test"]["samples"] > 0
    assert model.dataset_manifest_hash == "fixture-hash"
    high = model.predict_probability({
        "vwap_bias": 0.01,
        "breakout_pct": 0.01,
        "daily_momentum_20d": 0.12,
        "candidate_score": 0.8,
    })
    low = model.predict_probability({
        "vwap_bias": -0.01,
        "breakout_pct": -0.01,
        "daily_momentum_20d": -0.12,
        "candidate_score": 0.2,
    })
    assert high is not None and low is not None and high > low

    store = PaperAgentStore(tmp_path)
    store.save_model(model)
    assert store.status()["model_runtime_status"] == "not_activated"
    store.promote_model(model.id, reason="test gate", metrics=model.metrics)
    assert store.get_active_model() == model
    assert store.status()["model_runtime_status"] == "active"
    assert store.rollback_last_model_promotion(reason="first model loss", metrics={}) is not None
    assert store.get_active_model() is None
    assert store.status()["model_runtime_status"] == "disabled"
    store.promote_model(model.id, reason="revalidated", metrics=model.metrics)
    assert store.status()["model_runtime_status"] == "active"
    second = model.model_copy(update={"id": "model_v2", "version": 2})
    store.save_model(second)
    store.promote_model(second.id, reason="better", metrics=second.metrics)
    assert store.get_active_model() == second
    assert store.rollback_last_model_promotion(reason="paper loss", metrics={}) is not None
    assert store.get_active_model() == model


def test_training_uses_only_the_manifest_date_window(tmp_path: Path) -> None:
    dataset_root = tmp_path / "training"
    _write_training_fixture(dataset_root)
    window_start = date(2024, 1, 7)
    window_end = date(2024, 2, 6)
    (dataset_root / "manifest.json").write_text(
        json.dumps({
            "manifest_hash": "windowed-fixture",
            "start_date": window_start.isoformat(),
            "end_date": window_end.isoformat(),
        }),
        encoding="utf-8",
    )

    model = ExpertModelTrainer(dataset_root).train(version=1)

    assert model.trained_start == window_start
    assert model.trained_end == window_end - timedelta(days=1)
    assert model.sample_count == 300
    assert model.dataset_manifest_hash == "windowed-fixture"


def test_model_rejects_missing_features() -> None:
    model = TrainedDecisionModel(
        id="m1",
        version=1,
        feature_names=["a"],
        weights=[1.0],
        intercept=0.0,
        means=[0.0],
        scales=[1.0],
        trained_start=date(2024, 1, 1),
        trained_end=date(2024, 2, 1),
        sample_count=1,
        dataset_manifest_hash="x",
        metrics={},
    )
    assert model.predict_probability({}) is None
