from __future__ import annotations

from types import SimpleNamespace

from app.api.data import _compute_storage, _safe_aggregate_minute


def test_minute_status_reads_stock_and_etf_partitions_separately(tmp_path):
    for directory, dates in {
        "kline_minute": ["2026-08-18", "2026-08-19"],
        "kline_index_minute": ["2026-08-16", "2026-08-19"],
        "kline_etf_minute": ["2026-08-17", "2026-08-19"],
    }.items():
        for day in dates:
            (tmp_path / directory / f"date={day}").mkdir(parents=True)

    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))

    stock = _safe_aggregate_minute(repo)
    index = _safe_aggregate_minute(repo, "index")
    etf = _safe_aggregate_minute(repo, "etf")

    assert stock["trading_days"] == 2
    assert stock["earliest_date"] == "2026-08-18"
    assert index["trading_days"] == 2
    assert index["earliest_date"] == "2026-08-16"
    assert etf["trading_days"] == 2
    assert etf["earliest_date"] == "2026-08-17"


def test_storage_total_includes_etf_minute_without_double_counting(tmp_path):
    payloads = {
        "kline_minute/date=2026-08-19/part.parquet": 1,
        "kline_index_minute/date=2026-08-19/part.parquet": 2,
        "kline_etf_minute/date=2026-08-19/part.parquet": 3,
        "financials/metrics/part.parquet": 4,
        "ai_cache/cache.bin": 5,
    }
    for relative, size_mb in payloads.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size_mb * 1024 * 1024)

    stats = _compute_storage(tmp_path)

    assert stats["minute_size_mb"] == 1.0
    assert stats["index_minute_size_mb"] == 2.0
    assert stats["etf_minute_size_mb"] == 3.0
    assert stats["financials_size_mb"] == 4.0
    assert stats["total_size_mb"] == 15.0
