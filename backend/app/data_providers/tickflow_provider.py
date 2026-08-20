"""TickFlow provider implementation."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime

import polars as pl

from app.data_providers.base import AssetType, ProviderCapabilities
from app.data_providers.normalizer import (
    normalize_adj_factors,
    normalize_daily,
    normalize_instruments,
    normalize_minute,
)
from app.tickflow.client import get_client
from app.tickflow.rate_limits import sleep_between_batches

logger = logging.getLogger(__name__)

_EXCHANGES = ["SH", "SZ", "BJ"]


class TickFlowProvider:
    name = "tickflow"
    capabilities = ProviderCapabilities(
        instruments=True,
        daily=True,
        adj_factor=True,
        minute=True,
        realtime=True,
        financial=True,
    )

    def __init__(self) -> None:
        self._minute_batch_size = 50
        self._minute_rpm: int | None = 24
        self._minute_request_count = 0
        self._minute_request_lock = threading.Lock()

    def configure_minute_limits(
        self,
        *,
        batch_size: int | None,
        rpm: int | None,
    ) -> None:
        self._minute_batch_size = max(1, min(batch_size or 50, 50))
        self._minute_rpm = rpm

    def _pace_minute_request(self) -> None:
        with self._minute_request_lock:
            request_index = self._minute_request_count
            self._minute_request_count += 1
        sleep_between_batches(request_index, self._minute_rpm)

    def get_instruments(self, asset_type: AssetType) -> pl.DataFrame:
        tf = get_client()
        instrument_type = "stock" if asset_type == "stock" else asset_type
        rows: list[dict] = []
        for ex in _EXCHANGES:
            try:
                items = tf.exchanges.get_instruments(ex, instrument_type=instrument_type)
                rows.extend([it for it in (items or []) if isinstance(it, dict)])
            except Exception as e:  # noqa: BLE001
                logger.warning("TickFlow instruments %s/%s failed: %s", ex, instrument_type, e)
        return normalize_instruments(rows, asset_type=asset_type, source=self.name)

    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: AssetType,  # noqa: ARG002
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame()
        tf = get_client()
        kwargs = {
            "period": "1d",
            "adjust": "none",
            "count": 10000 if start_time and end_time else 250,
            "as_dataframe": True,
            "show_progress": False,
        }
        if start_time and end_time:
            from app.services.kline_sync import _datetime_to_ms
            kwargs["start_time"] = _datetime_to_ms(start_time)
            kwargs["end_time"] = _datetime_to_ms(end_time)
        raw = tf.klines.batch(symbols, **kwargs)
        frames: list[pl.DataFrame] = []
        if isinstance(raw, dict):
            for sym, sub in raw.items():
                normalized = normalize_daily(sub, default_symbol=sym, source=self.name)
                if not normalized.is_empty():
                    frames.append(normalized)
        else:
            normalized = normalize_daily(raw, source=self.name)
            if not normalized.is_empty():
                frames.append(normalized)
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: AssetType,  # noqa: ARG002
    ) -> pl.DataFrame:
        if not symbols:
            return pl.DataFrame()
        tf = get_client()
        kwargs = {"as_dataframe": False}
        if start_time or end_time:
            from app.services.kline_sync import _datetime_to_ms
            if start_time:
                kwargs["start_time"] = _datetime_to_ms(start_time)
            if end_time:
                kwargs["end_time"] = _datetime_to_ms(end_time)
        raw = tf.klines.ex_factors(symbols, **kwargs)
        return normalize_adj_factors(raw, source=self.name)

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
        if freq != "1m":
            raise ValueError("TickFlow minute provider currently supports freq='1m' only")
        tf = get_client()
        chunks = [
            symbols[index:index + self._minute_batch_size]
            for index in range(0, len(symbols), self._minute_batch_size)
        ]
        frames: list[pl.DataFrame] = []
        for index, chunk in enumerate(chunks, start=1):
            kwargs = {
                "period": "1m",
                "adjust": "none",
                "count": 10000,
                "as_dataframe": True,
                "show_progress": False,
            }
            if start_time is not None:
                kwargs["start_time"] = int(start_time.timestamp() * 1000)
            if end_time is not None:
                kwargs["end_time"] = int(end_time.timestamp() * 1000)
            self._pace_minute_request()
            raw = tf.klines.batch(chunk, **kwargs)
            if isinstance(raw, dict):
                for symbol, values in raw.items():
                    normalized = normalize_minute(
                        values,
                        default_symbol=str(symbol),
                        asset_type=asset_type,
                        source=self.name,
                        freq=freq,
                    )
                    if not normalized.is_empty():
                        frames.append(normalized)
            else:
                normalized = normalize_minute(
                    raw,
                    asset_type=asset_type,
                    source=self.name,
                    freq=freq,
                )
                if not normalized.is_empty():
                    frames.append(normalized)
            if on_chunk_done is not None:
                on_chunk_done(index, len(chunks))
        if not frames:
            return pl.DataFrame()
        return (
            pl.concat(frames, how="diagonal_relaxed")
            .unique(subset=["symbol", "datetime"], keep="last")
            .sort(["datetime", "symbol"])
        )

    def get_realtime(
        self,
        universes: list[str] | None = None,
        symbols: list[str] | None = None,
    ) -> pl.DataFrame:
        tf = get_client()
        if universes and symbols:
            raise ValueError("TickFlow realtime accepts either universes or symbols, not both")
        if universes:
            resp = tf.quotes.get_by_universes(universes=universes)
        elif symbols:
            resp = tf.quotes.get(symbols=symbols)
        else:
            return pl.DataFrame()
        return pl.DataFrame(resp or [])
