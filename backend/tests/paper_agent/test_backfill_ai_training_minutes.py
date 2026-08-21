from __future__ import annotations

from datetime import date, datetime

import duckdb
import polars as pl
import pytest

from app.data_providers.huggingface_archive import (
    BACKFILL_FILENAME,
    VERIFIED_GAP_CLASSIFICATION,
    ArchiveBackfillRequest,
    ArchiveBatchQuery,
    _merge_local_partition,
    _query_batch,
    _validate_missing_against_audit,
    backfill,
    build_candidate_pairs,
    parse_archive_coverage,
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


def test_candidate_pairs_seed_previous_day_before_requested_window(tmp_path) -> None:
    """窗口首日也必须包含窗口外前一交易日的候选股。"""
    _write_candidates(tmp_path, date(2024, 1, 2), ["000001.SZ"])
    _write_candidates(tmp_path, date(2024, 1, 3), ["600000.SH"])

    pairs = build_candidate_pairs(
        tmp_path,
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
    )

    assert pairs["symbol"].to_list() == ["000001.SZ", "600000.SH"]


def test_backfill_rejects_invalid_batch_size_before_external_io(tmp_path) -> None:
    """非法批次大小必须在候选读取和外部下载前被拒绝。"""
    with pytest.raises(ValueError, match="batch_size"):
        backfill(
            ArchiveBackfillRequest(
                data_dir=tmp_path / "data",
                candidate_dir=tmp_path / "candidates",
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 3),
                batch_size=0,
            )
        )


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
    assert pl.read_parquet(target_dir / "part.parquet")["symbol"].to_list() == ["600000.SH"]


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
    verified = pl.DataFrame({"trade_date": [date(2024, 1, 2)], "symbol": ["600000.SH"]})

    result = _validate_missing_against_audit(verified, audit_path=audit_path)

    assert result["classification"].to_list() == [VERIFIED_GAP_CLASSIFICATION]

    unexpected = pl.DataFrame({"trade_date": [date(2024, 1, 3)], "symbol": ["000001.SZ"]})
    with pytest.raises(RuntimeError, match="not verified"):
        _validate_missing_against_audit(unexpected, audit_path=audit_path)


def test_archive_query_converts_source_volume_shares_to_canonical_lots(
    tmp_path,
) -> None:
    raw_dir = tmp_path / "raw"
    source_path = raw_dir / "data" / "stock_1m" / "SH" / "600000.parquet"
    source_path.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600000"],
            "exchange": ["SH"],
            "timestamp": [datetime(2024, 1, 2, 9, 30)],
            "open": [10.0],
            "high": [10.1],
            "low": [9.9],
            "close": [10.0],
            "volume": [12_300],
            "turnover": [123_000.0],
        }
    ).write_parquet(source_path)
    pairs = pl.DataFrame(
        {
            "trade_date": [date(2024, 1, 2)],
            "symbol": ["600000.SH"],
        }
    )
    output = tmp_path / "batch.parquet"
    connection = duckdb.connect(database=":memory:")
    try:
        _query_batch(
            connection,
            ArchiveBatchQuery(
                pairs=pairs,
                symbols=["600000.SH"],
                output_path=output,
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 2),
                raw_dir=raw_dir,
            ),
        )
    finally:
        connection.close()

    result = pl.read_parquet(output)
    assert result["volume"].to_list() == [123.0]
    assert result["amount"].to_list() == [123_000.0]


def test_archive_coverage_uses_published_snapshot_timestamps() -> None:
    coverage = parse_archive_coverage(
        {
            "first_timestamp": "2010-01-04 09:30:00",
            "last_timestamp": "2026-08-07 10:21:00",
            "built_at": "2026-08-21T14:31:54+08:00",
        },
        revision="abc123",
    )

    assert coverage.first_date == date(2010, 1, 4)
    assert coverage.last_date == date(2026, 8, 7)
    assert coverage.revision == "abc123"
