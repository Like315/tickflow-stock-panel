from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pytest

from scripts.backfill_ai_training_minutes import (
    BACKFILL_FILENAME,
    VERIFIED_GAP_CLASSIFICATION,
    _merge_local_partition,
    _validate_missing_against_audit,
    build_candidate_pairs,
)


def _write_candidates(root, trade_date: date, symbols: list[str]) -> None:
    path = root / f"date={trade_date}" / "part.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame({"symbol": symbols}).write_parquet(path)


def test_candidate_pairs_include_previous_day_for_t_plus_one(tmp_path) -> None:
    _write_candidates(tmp_path, date(2024, 1, 2), ["600000.SH", "000001.SZ"])
    _write_candidates(tmp_path, date(2024, 1, 3), ["600000.SH", "510300.SH"])

    pairs = build_candidate_pairs(
        tmp_path,
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 3),
    )

    day_two = pairs.filter(pl.col("trade_date") == date(2024, 1, 3))
    assert day_two["symbol"].to_list() == ["000001.SZ", "510300.SH", "600000.SH"]


def test_local_merge_keeps_base_partition_and_deduplicates_backfill(tmp_path) -> None:
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    columns = {
        "symbol": ["600000.SH", "000001.SZ"],
        "datetime": [datetime(2024, 1, 2, 9, 30)] * 2,
        "open": [10.0, 9.0],
        "high": [10.1, 9.1],
        "low": [9.9, 8.9],
        "close": [10.0, 9.0],
        "volume": [100.0, 200.0],
        "amount": [1_000.0, 1_800.0],
    }
    pl.DataFrame(columns).write_parquet(source_dir / "incoming.parquet")
    pl.DataFrame(columns).head(1).write_parquet(target_dir / "part.parquet")

    rows = _merge_local_partition(source_dir, target_dir)

    assert rows == 1
    backfill = pl.read_parquet(target_dir / BACKFILL_FILENAME)
    assert backfill["symbol"].to_list() == ["000001.SZ"]
    assert pl.read_parquet(target_dir / "part.parquet")["symbol"].to_list() == [
        "600000.SH"
    ]


def test_missing_pairs_must_be_verified_by_upstream_audit(tmp_path) -> None:
    audit_path = tmp_path / "audit.parquet"
    pl.DataFrame(
        {
            "symbol": ["600000"],
            "exchange": ["SH"],
            "date": [date(2024, 1, 2)],
            "classification": [VERIFIED_GAP_CLASSIFICATION],
            "evidence": ["positive_daily_volume"],
        }
    ).write_parquet(audit_path)
    verified = pl.DataFrame(
        {"trade_date": [date(2024, 1, 2)], "symbol": ["600000.SH"]}
    )

    result = _validate_missing_against_audit(verified, audit_path=audit_path)

    assert result["classification"].to_list() == [VERIFIED_GAP_CLASSIFICATION]

    unexpected = pl.DataFrame(
        {"trade_date": [date(2024, 1, 3)], "symbol": ["000001.SZ"]}
    )
    with pytest.raises(RuntimeError, match="not verified"):
        _validate_missing_against_audit(unexpected, audit_path=audit_path)
