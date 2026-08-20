"""美股全量基础档案与历史行情的标准化 Provider。"""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from app.tickflow.client import get_client

logger = logging.getLogger(__name__)

US_UNIVERSE = "US_Equity"
NEW_YORK_TZ = ZoneInfo("America/New_York")
AdjustType = Literal["none", "forward", "backward"]


class UsMarketDataProvider(Protocol):
    """供应商无关的美股基础档案与日 K 契约。"""

    name: str

    def get_instruments(self) -> dict[str, Any]:
        """返回标准化的全量基础档案。"""

    def get_daily(
        self,
        symbol: str,
        *,
        count: int,
        adjust: AdjustType,
    ) -> dict[str, Any]:
        """返回按日期升序排列的标准化日 K。"""


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_us_symbol(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw.endswith(".US"):
        return raw
    return f"{raw}.US" if raw else ""


def parse_tickflow_instruments(
    universe: Mapping[str, Any],
    raw_instruments: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """将 Universe 与 Instrument 响应合并成稳定的全量档案。"""
    raw_symbols = universe.get("symbols")
    if not isinstance(raw_symbols, list) or not raw_symbols:
        raise ValueError("TickFlow US_Equity universe 缺少 symbols")

    metadata: dict[str, Mapping[str, Any]] = {}
    for raw in raw_instruments:
        if not isinstance(raw, Mapping):
            continue
        symbol = normalize_us_symbol(raw.get("symbol"))
        if symbol:
            metadata[symbol] = raw

    rows = []
    for raw_symbol in raw_symbols:
        symbol = normalize_us_symbol(raw_symbol)
        if not symbol:
            continue
        raw = metadata.get(symbol, {})
        ext = raw.get("ext") if isinstance(raw.get("ext"), Mapping) else {}
        rows.append({
            "symbol": symbol,
            "code": str(raw.get("code") or symbol.removesuffix(".US")),
            "exchange": str(raw.get("exchange") or "US"),
            "region": str(raw.get("region") or "US"),
            "name": str(raw.get("name") or symbol),
            "instrument_type": str(raw.get("type") or "stock"),
            "total_shares": _finite(ext.get("total_shares")),
            "float_shares": _finite(ext.get("float_shares")),
        })
    if not rows:
        raise ValueError("TickFlow US_Equity universe 没有有效代码")
    rows.sort(key=lambda row: row["symbol"])
    return {
        "schema_version": 1,
        "source": "TickFlow US_Equity",
        "universe": str(universe.get("id") or US_UNIVERSE),
        "declared_count": int(universe.get("symbol_count") or len(raw_symbols)),
        "metadata_count": len(metadata),
        "rows": rows,
    }


def parse_tickflow_daily(
    symbol: str,
    compact: Mapping[str, Any],
    *,
    adjust: AdjustType,
) -> dict[str, Any]:
    """将 TickFlow compact columnar K 线标准化为逐行日 K。"""
    timestamps = compact.get("timestamp")
    if not isinstance(timestamps, list):
        raise ValueError("TickFlow 日 K 响应缺少 timestamp")
    columns = {
        name: compact.get(name) if isinstance(compact.get(name), list) else []
        for name in ("open", "high", "low", "close", "volume", "amount")
    }
    rows = []
    for index, raw_timestamp in enumerate(timestamps):
        timestamp = _finite(raw_timestamp)
        values = {
            name: _finite(column[index]) if index < len(column) else None
            for name, column in columns.items()
        }
        if (
            timestamp is None
            or any((values[name] or 0) <= 0 for name in ("open", "high", "low", "close"))
        ):
            continue
        date_value = datetime.fromtimestamp(
            timestamp / 1000, tz=UTC
        ).astimezone(NEW_YORK_TZ).date().isoformat()
        rows.append({
            "date": date_value,
            "timestamp": int(timestamp),
            "open": values["open"],
            "high": values["high"],
            "low": values["low"],
            "close": values["close"],
            "volume": values["volume"],
            "amount": values["amount"],
            "change_pct": None,
        })
    rows.sort(key=lambda row: row["timestamp"])
    for index in range(1, len(rows)):
        previous = rows[index - 1]["close"]
        rows[index]["change_pct"] = rows[index]["close"] / previous - 1
    if not rows:
        raise ValueError(f"TickFlow 未返回 {symbol} 的有效日 K")
    return {
        "schema_version": 1,
        "source": "TickFlow",
        "symbol": normalize_us_symbol(symbol),
        "adjust": adjust,
        "rows": rows,
    }


class TickFlowUsMarketDataProvider:
    """TickFlow 美股全量档案与历史行情适配器。"""

    name = "tickflow"

    def __init__(self, *, client_factory: Callable[[], Any] = get_client) -> None:
        self._client_factory = client_factory

    def get_instruments(self) -> dict[str, Any]:
        client = self._client_factory()
        universe = client.universes.get(US_UNIVERSE)
        symbols = list(universe.get("symbols") or [])
        instruments: list[Mapping[str, Any]] = []
        for start in range(0, len(symbols), 500):
            chunk = symbols[start : start + 500]
            try:
                instruments.extend(client.instruments.batch(chunk) or [])
            except Exception as exc:
                logger.warning("美股基础档案批次读取失败 (%d-%d): %s", start, start + len(chunk), exc)
                for retry_start in range(0, len(chunk), 100):
                    retry_chunk = chunk[retry_start : retry_start + 100]
                    try:
                        instruments.extend(client.instruments.batch(retry_chunk) or [])
                    except Exception as retry_exc:
                        logger.warning(
                            "美股基础档案子批次读取失败 (%d-%d): %s",
                            start + retry_start,
                            start + retry_start + len(retry_chunk),
                            retry_exc,
                        )
        result = parse_tickflow_instruments(universe, instruments)
        result["fetched_at"] = int(time.time() * 1000)
        return result

    def get_daily(
        self,
        symbol: str,
        *,
        count: int,
        adjust: AdjustType,
    ) -> dict[str, Any]:
        compact = self._client_factory().klines.get(
            normalize_us_symbol(symbol),
            period="1d",
            count=count,
            adjust=adjust,
            as_dataframe=False,
        )
        if not isinstance(compact, Mapping):
            raise ValueError("TickFlow 日 K 响应不是列式对象")
        result = parse_tickflow_daily(symbol, compact, adjust=adjust)
        result["fetched_at"] = int(time.time() * 1000)
        return result
