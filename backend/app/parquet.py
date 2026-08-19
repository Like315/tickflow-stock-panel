"""Polars parquet helpers."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import polars as pl

DAILY_STORAGE_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "quote_ts": pl.Int64,
}

ENRICHED_STORAGE_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "raw_close": pl.Float64,
    "raw_high": pl.Float64,
    "raw_low": pl.Float64,
    "turnover_rate": pl.Float64,
    "consecutive_limit_ups": pl.UInt32,
    "consecutive_limit_downs": pl.UInt32,
    "quote_ts": pl.Int64,
}

# Windows 允许并发读取者短暂占用 Parquet; 原子替换在占用解除后重试。
_ATOMIC_REPLACE_ATTEMPTS: int = 8
# 首次重试等待秒数, 后续按指数退避, 最长总等待约 6.35 秒。
_ATOMIC_REPLACE_INITIAL_DELAY_SECONDS: float = 0.05


def atomic_write_parquet(df: pl.DataFrame, target: Path) -> None:
    """通过同目录临时文件原子写入 Parquet, 并兼容 Windows 短暂文件占用。"""
    temporary = target.with_name(f"{target.name}.tmp")
    df.write_parquet(temporary)
    _replace_with_retry(temporary, target)


def _replace_with_retry(source: Path, target: Path) -> None:
    """有限重试原子替换, 耗尽后保留源文件并重新抛出权限异常。"""
    for attempt in range(_ATOMIC_REPLACE_ATTEMPTS):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == _ATOMIC_REPLACE_ATTEMPTS - 1:
                raise
            delay = _ATOMIC_REPLACE_INITIAL_DELAY_SECONDS * (2**attempt)
            time.sleep(delay)


def scan_parquet_compat(source: Any, **kwargs: Any) -> pl.LazyFrame:
    """Scan partitioned parquet while tolerating additive schema changes."""
    kwargs.setdefault("missing_columns", "insert")
    kwargs.setdefault("extra_columns", "ignore")
    return pl.scan_parquet(source, **kwargs)


def scan_daily_parquet(source: Any, **kwargs: Any) -> pl.LazyFrame:
    kwargs.setdefault("schema", DAILY_STORAGE_SCHEMA)
    kwargs.setdefault("cast_options", pl.ScanCastOptions(integer_cast="allow-float"))
    return scan_parquet_compat(source, **kwargs)


def scan_enriched_parquet(source: Any, **kwargs: Any) -> pl.LazyFrame:
    kwargs.setdefault("schema", ENRICHED_STORAGE_SCHEMA)
    kwargs.setdefault("cast_options", pl.ScanCastOptions(integer_cast="allow-float"))
    return scan_parquet_compat(source, **kwargs)
