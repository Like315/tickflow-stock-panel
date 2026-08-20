"""美股行业分类与主题 ETF 持仓的标准化参考数据 Provider。"""
from __future__ import annotations

import io
import math
import re
from collections.abc import Mapping
from typing import Any, Protocol

import httpx
import polars as pl

NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
STATE_STREET_HOLDINGS_URL = (
    "https://www.ssga.com/library-content/products/fund-data/etfs/us/"
    "holdings-daily-us-en-{ticker}.xlsx"
)

NASDAQ_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
    "User-Agent": "Mozilla/5.0 (compatible; TickFlowStockPanel/1.0)",
}


class UsMarketReferenceProvider(Protocol):
    """供应商无关的美股板块参考数据契约。"""

    name: str

    def get_sector_classifications(self) -> dict[str, Any]:
        """返回 symbol/name/sector/industry 标准字段。"""

    def get_theme_holdings(self, ticker: str) -> dict[str, Any]:
        """返回 symbol/name/weight_pct/sector 标准字段。"""


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalize_symbol(value: Any) -> str:
    raw = str(value or "").strip().upper().replace("/", ".")
    if not raw or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]*", raw):
        return ""
    return raw if raw.endswith(".US") else f"{raw}.US"


def parse_nasdaq_classifications(payload: Mapping[str, Any]) -> dict[str, Any]:
    """把 Nasdaq screener 响应标准化为美股档案与行业分类快照。"""
    data = payload.get("data")
    data = data if isinstance(data, Mapping) else {}
    raw_rows = data.get("rows")
    if not isinstance(raw_rows, list):
        raise ValueError("Nasdaq 行业分类响应缺少 rows")

    rows: dict[str, dict[str, Any]] = {}
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            continue
        symbol = _normalize_symbol(raw.get("symbol"))
        sector = str(raw.get("sector") or "").strip()
        industry = str(raw.get("industry") or "").strip()
        if not symbol:
            continue
        raw_change_pct = str(raw.get("pctchange") or "")
        change_pct = _finite(raw_change_pct)
        if change_pct is not None and "%" in raw_change_pct:
            change_pct /= 100
        ipo_year = _finite(raw.get("ipoyear"))
        profile_url = str(raw.get("url") or "").strip()
        rows[symbol] = {
            "symbol": symbol,
            "name": str(raw.get("name") or symbol).strip(),
            "sector": "" if sector.upper() == "N/A" else sector,
            "industry": "" if industry.upper() == "N/A" else industry,
            "country": str(raw.get("country") or "").strip(),
            "ipo_year": int(ipo_year) if ipo_year is not None else None,
            "market_cap": _finite(raw.get("marketCap")),
            "last_price": _finite(str(raw.get("lastsale") or "").replace("$", "")),
            "change_amount": _finite(raw.get("netchange")),
            "change_pct": change_pct,
            "volume": _finite(raw.get("volume")),
            "profile_url": (
                f"https://www.nasdaq.com{profile_url}"
                if profile_url.startswith("/")
                else profile_url
            ),
        }

    if not rows:
        raise ValueError("Nasdaq 行业分类响应没有有效记录")
    return {
        "schema_version": 1,
        "source": "Nasdaq / Quotemedia SIC mapped sector and industry",
        "standard": "sic_mapped",
        "as_of": str(data.get("asOf") or ""),
        "classified_count": sum(bool(row["sector"]) for row in rows.values()),
        "rows": sorted(rows.values(), key=lambda row: row["symbol"]),
    }


def parse_state_street_holdings(frame: pl.DataFrame) -> dict[str, Any]:
    """把 State Street 每日持仓表标准化为主题成分与百分比权重。"""
    if frame.is_empty():
        raise ValueError("State Street 持仓表为空")

    table = frame.rows()
    as_of = ""
    header_index = -1
    header: list[str] = []
    for index, row in enumerate(table):
        values = [str(value or "").strip() for value in row]
        first = values[0].lower() if values else ""
        if first.rstrip(":") == "holdings" and len(values) > 1:
            as_of = values[1]
        if "name" in {value.lower() for value in values} and "ticker" in {
            value.lower() for value in values
        }:
            header_index = index
            header = values
            break
    if header_index < 0:
        raise ValueError("State Street 持仓表缺少 Name/Ticker 表头")

    lowered = [value.lower() for value in header]
    name_index = lowered.index("name")
    ticker_index = lowered.index("ticker")
    weight_index = lowered.index("weight") if "weight" in lowered else -1
    sector_index = lowered.index("sector") if "sector" in lowered else -1

    members: dict[str, dict[str, Any]] = {}
    for row in table[header_index + 1 :]:
        if max(name_index, ticker_index) >= len(row):
            continue
        name = str(row[name_index] or "").strip()
        raw_ticker = str(row[ticker_index] or "").strip().upper()
        symbol = _normalize_symbol(raw_ticker)
        weight = _finite(row[weight_index]) if 0 <= weight_index < len(row) else None
        if (
            not name
            or not symbol
            or raw_ticker in {"USD", "CASH_USD", "-"}
            or weight is None
            or weight <= 0
        ):
            continue
        member = {
            "symbol": symbol,
            "name": name,
            "weight_pct": weight,
            "sector": (
                str(row[sector_index] or "").strip()
                if 0 <= sector_index < len(row)
                else ""
            ),
        }
        previous = members.get(symbol)
        if previous is None or weight > previous["weight_pct"]:
            members[symbol] = member

    if not members:
        raise ValueError("State Street 持仓表没有有效证券")
    return {
        "schema_version": 1,
        "source": "State Street daily fund holdings",
        "as_of": as_of,
        "members": sorted(
            members.values(), key=lambda member: member["weight_pct"], reverse=True
        ),
    }


class NasdaqStateStreetUsMarketReferenceProvider:
    """内置参考数据源: Nasdaq 分类 + State Street 官方 ETF 持仓。"""

    name = "nasdaq_state_street"

    def get_sector_classifications(self) -> dict[str, Any]:
        response = httpx.get(
            NASDAQ_SCREENER_URL,
            params={"tableonly": "true", "limit": 25, "offset": 0, "download": "true"},
            headers=NASDAQ_HEADERS,
            timeout=20,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("Nasdaq 行业分类响应不是 JSON 对象")
        return parse_nasdaq_classifications(payload)

    def get_theme_holdings(self, ticker: str) -> dict[str, Any]:
        source_url = STATE_STREET_HOLDINGS_URL.format(ticker=ticker.lower())
        response = httpx.get(
            source_url,
            headers={"User-Agent": NASDAQ_HEADERS["User-Agent"]},
            timeout=20,
            follow_redirects=True,
        )
        response.raise_for_status()
        if not response.content.startswith(b"PK"):
            raise ValueError("State Street 持仓下载不是有效 xlsx 文件")
        sheets = pl.read_excel(io.BytesIO(response.content), sheet_id=0, has_header=False)
        frame = next(iter(sheets.values())) if isinstance(sheets, dict) else sheets
        result = parse_state_street_holdings(frame)
        result["source_url"] = source_url
        return result
