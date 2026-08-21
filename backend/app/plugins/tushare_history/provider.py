"""Tushare research-only provider for historical A-share minute bars."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import polars as pl

from app.data_providers.base import AssetType
from app.data_providers.normalizer import normalize_minute
from app.plugins.tushare_history import bridge
from app.tickflow.rate_limits import sleep_between_batches

logger = logging.getLogger(__name__)

_DATASETS = ("minute",)
_SAFE_RPM = 400
_MAX_SEGMENT_DAYS = 20


@dataclass
class _TushareHistoryConfig:
    name: str = "tushare_history"
    display_name: str = "Tushare 历史分钟(需独立权限)"
    datasets: dict[str, Any] = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


class TushareHistoricalMinuteProvider:
    name = "tushare_history"
    builtin = True

    def __init__(self) -> None:
        self.config = _TushareHistoryConfig()
        self._request_count = 0

    def close(self) -> None:
        pass

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: AssetType = "stock",
        freq: str = "1m",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame()
        if asset_type != "stock":
            raise ValueError("Tushare historical minute provider supports A-share stocks only")
        if freq != "1m":
            raise ValueError("Tushare historical minute provider currently requires freq='1m'")
        if start_time is None or end_time is None:
            raise ValueError("historical minute requests require start_time and end_time")

        frames: list[pl.DataFrame] = []
        total = len(symbols)
        for index, symbol in enumerate(symbols, start=1):
            for segment_start, segment_end in self._segments(start_time, end_time):
                sleep_between_batches(self._request_count, _SAFE_RPM)
                self._request_count += 1
                raw = self._fetch_with_retry(
                    symbol,
                    start_time=segment_start,
                    end_time=segment_end,
                )
                frame = self._normalize(raw, symbol)
                if not frame.is_empty():
                    frames.append(frame)
            if on_chunk_done is not None:
                on_chunk_done(index, total)
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    @staticmethod
    def _segments(start_time: datetime, end_time: datetime) -> Iterator[tuple[datetime, datetime]]:
        cursor = start_time
        while cursor <= end_time:
            segment_end = min(cursor + timedelta(days=_MAX_SEGMENT_DAYS), end_time)
            yield cursor, segment_end
            if segment_end >= end_time:
                break
            cursor = segment_end + timedelta(microseconds=1)

    @staticmethod
    def _fetch_with_retry(
        symbol: str,
        *,
        start_time: datetime,
        end_time: datetime,
    ):
        for attempt in range(3):
            try:
                return bridge.fetch_minutes(
                    symbol,
                    start_time=start_time,
                    end_time=end_time,
                    freq="1min",
                )
            except bridge.TushareHistoryError:
                if attempt >= 2:
                    raise
                delay = float(2**attempt)
                logger.warning(
                    "Tushare minute request failed for %s; retrying in %.1fs (%d/3)",
                    symbol,
                    delay,
                    attempt + 1,
                )
                time.sleep(delay)
        raise RuntimeError(f"unreachable retry state for {symbol}")

    @staticmethod
    def _normalize(raw, symbol: str) -> pl.DataFrame:
        if raw is None:
            return pl.DataFrame()
        if isinstance(raw, pl.DataFrame):
            frame = raw
        else:
            try:
                if len(raw) == 0:
                    return pl.DataFrame()
            except TypeError:
                return pl.DataFrame()
            frame = pl.from_pandas(raw, include_index=False)
        rename = {
            source: target
            for source, target in {
                "ts_code": "symbol",
                "trade_time": "datetime",
                "vol": "volume",
            }.items()
            if source in frame.columns and target not in frame.columns
        }
        if rename:
            frame = frame.rename(rename)
        if "symbol" not in frame.columns:
            frame = frame.with_columns(pl.lit(symbol).alias("symbol"))
        if "datetime" not in frame.columns:
            return pl.DataFrame()
        frame = frame.with_columns(
            pl.col("datetime").cast(pl.String).str.to_datetime(strict=False).alias("datetime"),
            # Tushare stock minute volume is shares; the panel's minute contract is lots.
            (pl.col("volume").cast(pl.Float64, strict=False) / 100).alias("volume"),
        )
        return normalize_minute(
            frame,
            default_symbol=symbol,
            asset_type="stock",
            source="tushare_history",
            freq="1m",
        )

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset != "minute":
            raise ValueError(f"Tushare historical provider does not support {dataset}")
        end = (datetime.now() - timedelta(days=1)).replace(
            hour=15,
            minute=5,
            second=0,
            microsecond=0,
        )
        start = (end - timedelta(days=7)).replace(hour=9, minute=15)
        frame = self.get_minute(symbols or ["600000.SH"], start, end)
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": frame.height,
            "columns": frame.columns,
            "preview": frame.head(5).to_dicts() if not frame.is_empty() else [],
        }
