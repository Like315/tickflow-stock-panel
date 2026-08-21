"""美股全量基础档案、检索与按需历史行情服务。"""

from __future__ import annotations

import copy
import json
import logging
import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.data_providers.us_market_data import (
    AdjustType,
    TickFlowUsMarketDataProvider,
    UsMarketDataProvider,
    normalize_us_symbol,
)
from app.services.us_market_overview import build_market_statistics

logger = logging.getLogger(__name__)


class UsMarketInstrumentNotFoundError(KeyError):
    """指定的美股代码不在当前基础档案中。"""


class UsMarketHistoryUnavailableError(RuntimeError):
    """指定美股的历史行情当前不可用。"""


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _valid_symbol(value: Any) -> str:
    symbol = normalize_us_symbol(value)
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]*\.US", symbol):
        raise UsMarketInstrumentNotFoundError(str(value))
    return symbol


class UsMarketInstrumentStore:
    TTL_SECONDS = 24 * 60 * 60

    def __init__(
        self,
        data_dir: Path,
        *,
        provider: UsMarketDataProvider | None = None,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(data_dir) / "us_market" / "instruments.json"
        self._provider = provider or TickFlowUsMarketDataProvider()
        self._wall_time = wall_time
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None

    def get(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            disk = self._cache or self._read()
            if not force and disk is not None and self._is_fresh(disk):
                disk["status"] = "live" if self._cache is not None else "snapshot"
                self._cache = disk
                return copy.deepcopy(disk)
            try:
                payload = self._provider.get_instruments()
                payload["fetched_at"] = int(self._wall_time() * 1000)
                payload["status"] = "live"
                self._write(payload)
                self._cache = payload
                return copy.deepcopy(payload)
            except Exception as exc:
                logger.warning("美股全量基础档案刷新失败: %s", exc)
                if disk is not None:
                    disk["status"] = "snapshot"
                    disk["message"] = "全量基础档案刷新失败,使用最近快照"
                    self._cache = disk
                    return copy.deepcopy(disk)
                return {
                    "schema_version": 1,
                    "source": getattr(self._provider, "name", "us_market_provider"),
                    "status": "unavailable",
                    "fetched_at": 0,
                    "message": "美股全量基础档案当前不可用",
                    "rows": [],
                }

    def _is_fresh(self, payload: Mapping[str, Any]) -> bool:
        fetched_at = _finite(payload.get("fetched_at")) or 0
        return self._wall_time() - fetched_at / 1000 < self.TTL_SECONDS

    def _read(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return None
        return payload if isinstance(payload.get("rows"), list) else None

    def _write(self, payload: Mapping[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self._path)
        except OSError as exc:
            logger.warning("美股全量基础档案快照写入失败: %s", exc)


class UsMarketHistoryStore:
    TTL_SECONDS = 4 * 60 * 60

    def __init__(
        self,
        data_dir: Path,
        *,
        provider: UsMarketDataProvider | None = None,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self._root = Path(data_dir) / "us_market" / "history"
        self._provider = provider or TickFlowUsMarketDataProvider()
        self._wall_time = wall_time
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._cache: dict[str, dict[str, Any]] = {}

    def get(
        self,
        symbol: str,
        *,
        count: int,
        adjust: AdjustType,
        force: bool = False,
    ) -> dict[str, Any]:
        normalized = _valid_symbol(symbol)
        cache_key = f"{normalized}:{adjust}"
        with self._guard:
            lock = self._locks.setdefault(cache_key, threading.Lock())
        with lock:
            disk = self._cache.get(cache_key) or self._read(normalized, adjust)
            rows = disk.get("rows", []) if disk is not None else []
            if not force and disk is not None and self._is_fresh(disk) and len(rows) >= count:
                disk["status"] = "live" if cache_key in self._cache else "snapshot"
                self._cache[cache_key] = disk
                return self._tail(disk, count)
            try:
                payload = self._provider.get_daily(normalized, count=count, adjust=adjust)
                payload["fetched_at"] = int(self._wall_time() * 1000)
                payload["status"] = "live"
                self._write(normalized, adjust, payload)
                self._cache[cache_key] = payload
                return self._tail(payload, count)
            except Exception as exc:
                logger.warning("美股历史行情刷新失败 (%s): %s", normalized, exc)
                if disk is not None and rows:
                    disk["status"] = "snapshot"
                    disk["message"] = "历史行情刷新失败,使用最近快照"
                    self._cache[cache_key] = disk
                    return self._tail(disk, count)
                raise UsMarketHistoryUnavailableError(f"{normalized} 的历史行情当前不可用") from exc

    def _path(self, symbol: str, adjust: AdjustType) -> Path:
        safe_symbol = symbol.replace(".", "_")
        return self._root / f"{safe_symbol}-{adjust}.json"

    def _is_fresh(self, payload: Mapping[str, Any]) -> bool:
        fetched_at = _finite(payload.get("fetched_at")) or 0
        return self._wall_time() - fetched_at / 1000 < self.TTL_SECONDS

    def _read(self, symbol: str, adjust: AdjustType) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._path(symbol, adjust).read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return None
        return payload if isinstance(payload.get("rows"), list) else None

    def _write(
        self,
        symbol: str,
        adjust: AdjustType,
        payload: Mapping[str, Any],
    ) -> None:
        path = self._path(symbol, adjust)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as exc:
            logger.warning("美股历史行情快照写入失败 (%s): %s", symbol, exc)

    @staticmethod
    def _tail(payload: Mapping[str, Any], count: int) -> dict[str, Any]:
        result = copy.deepcopy(dict(payload))
        rows = result.get("rows")
        available = len(rows) if isinstance(rows, list) else 0
        result["requested_count"] = count
        result["available_count"] = min(available, count)
        result["rows"] = rows[-count:] if isinstance(rows, list) else []
        return result


class UsMarketInstrumentService:
    """合并全量代码、Nasdaq 分类和 TickFlow 行情。"""

    def __init__(
        self,
        data_dir: Path,
        overview_service: Any,
        classification_store: Any,
        *,
        instruments: UsMarketInstrumentStore | None = None,
        history: UsMarketHistoryStore | None = None,
        market_provider: UsMarketDataProvider | None = None,
    ) -> None:
        provider = market_provider or TickFlowUsMarketDataProvider()
        self._overview = overview_service
        self._classifications = classification_store
        self._instruments = instruments or UsMarketInstrumentStore(data_dir, provider=provider)
        self._history = history or UsMarketHistoryStore(data_dir, provider=provider)

    def list_instruments(
        self,
        *,
        query: str = "",
        sector: str = "",
        industry: str = "",
        country: str = "",
        limit: int = 50,
        offset: int = 0,
        force: bool = False,
    ) -> dict[str, Any]:
        rows, sources = self._merged_rows(force=force, include_quotes=True)
        total = len(rows)
        query_folded = query.strip().casefold()
        sector_folded = sector.strip().casefold()
        industry_folded = industry.strip().casefold()
        country_folded = country.strip().casefold()

        def matched(row: Mapping[str, Any]) -> bool:
            if query_folded and not any(
                query_folded in str(row.get(key) or "").casefold()
                for key in ("symbol", "code", "name", "name_en")
            ):
                return False
            if sector_folded and str(row.get("sector") or "").casefold() != sector_folded:
                return False
            if industry_folded and industry_folded not in str(row.get("industry") or "").casefold():
                return False
            return not country_folded or str(row.get("country") or "").casefold() == country_folded

        filtered = [row for row in rows if matched(row)]
        page = filtered[offset : offset + limit]
        return {
            "schema_version": 1,
            "total": total,
            "matched": len(filtered),
            "limit": limit,
            "offset": offset,
            "classified_count": sum(bool(row.get("sector")) for row in rows),
            "quote_coverage_count": sum(row.get("last_price") is not None for row in rows),
            "sources": sources,
            "rows": page,
        }

    def get_instrument(self, symbol: str, *, force: bool = False) -> dict[str, Any]:
        normalized = _valid_symbol(symbol)
        rows, sources = self._merged_rows(force=force, include_quotes=True)
        instrument = next((row for row in rows if row["symbol"] == normalized), None)
        if instrument is None:
            raise UsMarketInstrumentNotFoundError(normalized)
        return {"schema_version": 1, "sources": sources, "instrument": instrument}

    def get_daily(
        self,
        symbol: str,
        *,
        count: int,
        adjust: AdjustType,
        force: bool = False,
    ) -> dict[str, Any]:
        normalized = _valid_symbol(symbol)
        return self._history.get(
            normalized,
            count=count,
            adjust=adjust,
            force=force,
        )

    def get_rankings(self, *, limit: int = 10, force: bool = False) -> dict[str, Any]:
        """实时全市场不可用时,用 Nasdaq 快照生成排行榜。"""
        classifications = self._classifications.get(force=force)
        rows: dict[str, dict[str, Any]] = {
            str(row["symbol"]): dict(row)
            for row in classifications.get("rows", [])
            if isinstance(row, Mapping) and row.get("symbol")
        }
        live_symbols: set[str] = set()
        market_status = "unavailable"
        try:
            overview, quotes = self._overview.get_market_snapshot(force=False)
            market_status = str(overview.get("status") or "unavailable")
        except Exception as exc:
            logger.info("美股排行榜实时行情合并失败: %s", exc)
            quotes = []
        for quote in quotes:
            symbol = str(quote.get("symbol") or "")
            target = rows.get(symbol)
            if target is None:
                continue
            for key in (
                "last_price",
                "change_amount",
                "change_pct",
                "volume",
                "amount",
                "timestamp",
            ):
                if quote.get(key) is not None:
                    target[key] = quote.get(key)
            live_symbols.add(symbol)

        def public(row: Mapping[str, Any]) -> dict[str, Any]:
            last_price = _finite(row.get("last_price"))
            volume = _finite(row.get("volume"))
            amount = _finite(row.get("amount"))
            amount_estimated = False
            if amount is None and last_price is not None and volume is not None:
                amount = last_price * volume
                amount_estimated = True
            return {
                "symbol": row.get("symbol"),
                "name": row.get("name") or row.get("symbol"),
                "last_price": last_price,
                "change_amount": _finite(row.get("change_amount")),
                "change_pct": _finite(row.get("change_pct")),
                "volume": volume,
                "amount": amount,
                "amount_estimated": amount_estimated,
                "timestamp": row.get("timestamp"),
            }

        change_rows = [
            row
            for row in rows.values()
            if (_finite(row.get("last_price")) or 0) >= 1
            and _finite(row.get("change_pct")) is not None
        ]
        active_rows = [
            row
            for row in rows.values()
            if (_finite(row.get("last_price")) or 0) > 0 and (_finite(row.get("volume")) or 0) > 0
        ]
        breadth, distribution = build_market_statistics(list(rows.values()))
        live_count = sum(symbol in live_symbols for symbol in rows)
        if live_count == 0:
            status = "snapshot"
            source = "Nasdaq screener snapshot"
        elif live_count == len(rows):
            status = "live"
            source = "TickFlow US_Equity"
        else:
            status = "mixed"
            source = "TickFlow + Nasdaq snapshot"
        return {
            "schema_version": 1,
            "status": status,
            "source": source,
            "market_status": market_status,
            "as_of": classifications.get("as_of"),
            "sample_count": len(rows),
            "live_count": live_count,
            "breadth": breadth if breadth["total"] else None,
            "distribution": distribution if breadth["total"] else [],
            "rankings": {
                "gainers": [
                    public(row)
                    for row in sorted(
                        change_rows,
                        key=lambda row: float(row["change_pct"]),
                        reverse=True,
                    )[:limit]
                ],
                "losers": [
                    public(row)
                    for row in sorted(
                        change_rows,
                        key=lambda row: float(row["change_pct"]),
                    )[:limit]
                ],
                "active": [
                    public(row)
                    for row in sorted(
                        active_rows,
                        key=lambda row: (
                            _finite(row.get("amount"))
                            or (_finite(row.get("last_price")) or 0)
                            * (_finite(row.get("volume")) or 0)
                        ),
                        reverse=True,
                    )[:limit]
                ],
            },
        }

    def _merged_rows(
        self,
        *,
        force: bool,
        include_quotes: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        instruments = self._instruments.get(force=force)
        classifications = self._classifications.get(force=force)
        merged: dict[str, dict[str, Any]] = {}
        for row in instruments.get("rows", []):
            if isinstance(row, Mapping) and row.get("symbol"):
                merged[str(row["symbol"])] = dict(row)
        for row in classifications.get("rows", []):
            if not isinstance(row, Mapping) or not row.get("symbol"):
                continue
            symbol = str(row["symbol"])
            target = merged.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "code": symbol.removesuffix(".US"),
                    "exchange": "US",
                    "region": "US",
                    "name": str(row.get("name") or symbol),
                    "instrument_type": "stock",
                    "total_shares": None,
                    "float_shares": None,
                },
            )
            target["name_en"] = str(row.get("name") or "")
            for key in (
                "sector",
                "industry",
                "country",
                "ipo_year",
                "market_cap",
                "last_price",
                "change_amount",
                "change_pct",
                "volume",
                "profile_url",
            ):
                if row.get(key) not in (None, ""):
                    target[key] = row.get(key)
            if target.get("last_price") is not None:
                target["quote_source"] = "Nasdaq snapshot"

        if include_quotes:
            quote_rows: list[Mapping[str, Any]] = []
            market_status = "unavailable"
            try:
                overview, quote_rows = self._overview.get_market_snapshot(force=force)
                market_status = str(overview.get("status") or "unavailable")
            except Exception as exc:
                logger.info("美股档案实时行情合并失败: %s", exc)
            for quote in quote_rows:
                symbol = str(quote.get("symbol") or "")
                target = merged.get(symbol)
                if target is None:
                    continue
                for key in (
                    "last_price",
                    "change_amount",
                    "change_pct",
                    "volume",
                    "amount",
                    "timestamp",
                ):
                    target[key] = _finite(quote.get(key))
                target["quote_source"] = "TickFlow"
                if target.get("market_cap") is None:
                    price = _finite(target.get("last_price"))
                    shares = _finite(target.get("total_shares"))
                    if price is not None and shares is not None:
                        target["market_cap"] = price * shares
        else:
            market_status = "not_requested"

        rows = sorted(merged.values(), key=lambda row: row["symbol"])
        return rows, {
            "instruments": {
                key: instruments.get(key)
                for key in ("status", "source", "universe", "fetched_at", "message")
                if instruments.get(key) is not None
            },
            "classification": {
                key: classifications.get(key)
                for key in ("status", "source", "standard", "as_of", "fetched_at", "message")
                if classifications.get(key) is not None
            },
            "market_status": market_status,
        }
