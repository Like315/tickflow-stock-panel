"""美股市场数据服务 — 通过 Yahoo Finance API 获取实时行情。

不依赖 TickFlow SDK，独立使用 httpx 直接请求 Yahoo Finance 公开接口。
"""
from __future__ import annotations

import asyncio
import math
import threading
import time
from typing import Any

import httpx

# ── 常量 ──────────────────────────────────────────────────────────────

_YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

# 美股核心指数
US_INDICES = {
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ",
    "^DJI": "Dow Jones",
    "^RUT": "Russell 2000",
    "^VIX": "VIX",
}

# 精选美股列表 (覆盖各板块龙头, 用于涨跌排行和广度计算)
US_STOCKS = {
    # 科技
    "AAPL": ("Apple", "科技"),
    "MSFT": ("Microsoft", "科技"),
    "GOOGL": ("Alphabet", "科技"),
    "AMZN": ("Amazon", "可选消费"),
    "NVDA": ("NVIDIA", "科技"),
    "META": ("Meta Platforms", "通信"),
    "TSLA": ("Tesla", "可选消费"),
    "AVGO": ("Broadcom", "科技"),
    "ORCL": ("Oracle", "科技"),
    "ADBE": ("Adobe", "科技"),
    "CRM": ("Salesforce", "科技"),
    "AMD": ("AMD", "科技"),
    "INTC": ("Intel", "科技"),
    "QCOM": ("Qualcomm", "科技"),
    "CSCO": ("Cisco", "科技"),
    "TXN": ("Texas Instruments", "科技"),
    "IBM": ("IBM", "科技"),
    "NOW": ("ServiceNow", "科技"),
    "INTU": ("Intuit", "科技"),
    "AMAT": ("Applied Materials", "科技"),
    # 金融
    "JPM": ("JPMorgan Chase", "金融"),
    "BAC": ("Bank of America", "金融"),
    "WFC": ("Wells Fargo", "金融"),
    "GS": ("Goldman Sachs", "金融"),
    "MS": ("Morgan Stanley", "金融"),
    "V": ("Visa", "金融"),
    "MA": ("Mastercard", "金融"),
    "BRK-B": ("Berkshire Hathaway", "金融"),
    "AXP": ("American Express", "金融"),
    "C": ("Citigroup", "金融"),
    # 医疗
    "JNJ": ("Johnson & Johnson", "医疗"),
    "UNH": ("UnitedHealth", "医疗"),
    "LLY": ("Eli Lilly", "医疗"),
    "PFE": ("Pfizer", "医疗"),
    "ABBV": ("AbbVie", "医疗"),
    "MRK": ("Merck", "医疗"),
    "TMO": ("Thermo Fisher", "医疗"),
    "ABT": ("Abbott", "医疗"),
    # 消费
    "WMT": ("Walmart", "必需消费"),
    "PG": ("Procter & Gamble", "必需消费"),
    "KO": ("Coca-Cola", "必需消费"),
    "PEP": ("PepsiCo", "必需消费"),
    "MCD": ("McDonald's", "可选消费"),
    "SBUX": ("Starbucks", "可选消费"),
    "NKE": ("Nike", "可选消费"),
    "COST": ("Costco", "必需消费"),
    "DIS": ("Disney", "通信"),
    "HD": ("Home Depot", "可选消费"),
    # 能源
    "XOM": ("Exxon Mobil", "能源"),
    "CVX": ("Chevron", "能源"),
    "COP": ("ConocoPhillips", "能源"),
    "SLB": ("Schlumberger", "能源"),
    # 工业
    "BA": ("Boeing", "工业"),
    "CAT": ("Caterpillar", "工业"),
    "GE": ("GE", "工业"),
    "HON": ("Honeywell", "工业"),
    "UPS": ("UPS", "工业"),
    # 通信
    "T": ("AT&T", "通信"),
    "VZ": ("Verizon", "通信"),
    "TMUS": ("T-Mobile", "通信"),
    # 公用事业 & 材料
    "NEE": ("NextEra Energy", "公用事业"),
    "DUK": ("Duke Energy", "公用事业"),
    "LIN": ("Linde", "基础材料"),
}

# 板块列表 (用于板块表现汇总)
US_SECTORS = [
    "科技", "金融", "医疗", "可选消费",
    "必需消费", "能源", "工业", "通信",
    "公用事业", "基础材料",
]

# 缓存
_CACHE_TTL = 60.0  # 60 秒缓存 (避免频繁请求 Yahoo Finance 被限流)
_cache: dict[str, Any] | None = None
_cache_ts: float = 0.0
_cache_lock = threading.Lock()


def _finite(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _is_us_market_open() -> bool:
    """简单判断美股交易时间 (美东 9:30-16:00, 周一至周五)。"""
    import datetime as dt
    import zoneinfo
    try:
        et = dt.datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        return False
    if et.weekday() >= 5:
        return False
    t = et.time()
    return dt.time(9, 30) <= t <= dt.time(16, 0)


async def _fetch_quotes(symbols: list[str]) -> list[dict]:
    """从 Yahoo Finance v8 chart API 获取行情 (v7 quote API 已需认证)。

    对每个符号请求 5 日日K, 从 meta 中提取最新价/前收/涨跌幅等。
    """
    if not symbols:
        return []
    headers = {"User-Agent": _USER_AGENT}

    async def _fetch_one(client: httpx.AsyncClient, sym: str) -> dict | None:
        url = f"{_YAHOO_CHART_URL}/{sym}"
        params = {"interval": "1d", "range": "5d"}
        try:
            resp = await client.get(url, params=params, headers=headers, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("chart", {}).get("result")
            if not result:
                return None
            meta = result[0].get("meta", {})
            if not meta:
                return None
            price = _finite(meta.get("regularMarketPrice"))
            prev_close = _finite(meta.get("previousClose")) or _finite(meta.get("chartPreviousClose"))
            change = None
            change_pct = None
            if price is not None and prev_close not in (None, 0):
                change = price - prev_close
                change_pct = change / prev_close * 100
            return {
                "symbol": sym,
                "regularMarketPrice": price,
                "regularMarketPreviousClose": prev_close,
                "regularMarketChange": change,
                "regularMarketChangePercent": change_pct,
                "regularMarketVolume": _finite(meta.get("regularMarketVolume")),
                "marketCap": _finite(meta.get("marketCap")),
                "regularMarketDayHigh": _finite(meta.get("regularMarketDayHigh")),
                "regularMarketDayLow": _finite(meta.get("regularMarketDayLow")),
                "fiftyTwoWeekHigh": _finite(meta.get("fiftyTwoWeek", {}).get("high")) if isinstance(meta.get("fiftyTwoWeek"), dict) else None,
                "fiftyTwoWeekLow": _finite(meta.get("fiftyTwoWeek", {}).get("low")) if isinstance(meta.get("fiftyTwoWeek"), dict) else None,
                "averageVolume": _finite(meta.get("averageVolume")),
            }
        except Exception:  # noqa: BLE001
            return None

    async with httpx.AsyncClient(timeout=12) as client:
        # 并发请求, 但限制并发数避免被限流
        sem = asyncio.Semaphore(10)
        async def _limited(sym: str):
            async with sem:
                return await _fetch_one(client, sym)

        results = await asyncio.gather(*[_limited(s) for s in symbols])
    return [r for r in results if r is not None]


def _parse_quote(raw: dict, name_map: dict[str, str], sector_map: dict[str, str]) -> dict:
    """将 Yahoo Finance quote 响应解析为统一格式。"""
    symbol = raw.get("symbol", "")
    return {
        "symbol": symbol,
        "name": name_map.get(symbol, raw.get("shortName", raw.get("longName", symbol))),
        "sector": sector_map.get(symbol, ""),
        "price": _finite(raw.get("regularMarketPrice")),
        "prev_close": _finite(raw.get("regularMarketPreviousClose")),
        "change": _finite(raw.get("regularMarketChange")),
        "change_pct": _finite(raw.get("regularMarketChangePercent")),
        "volume": _finite(raw.get("regularMarketVolume")),
        "market_cap": _finite(raw.get("marketCap")),
        "day_high": _finite(raw.get("regularMarketDayHigh")),
        "day_low": _finite(raw.get("regularMarketDayLow")),
        "year_high": _finite(raw.get("fiftyTwoWeekHigh")),
        "year_low": _finite(raw.get("fiftyTwoWeekLow")),
        "avg_volume": _finite(raw.get("averageVolume")),
    }


async def build_us_market_overview() -> dict:
    """装配美股市场总览。"""
    global _cache, _cache_ts

    # 缓存检查
    now = time.time()
    with _cache_lock:
        if _cache is not None and (now - _cache_ts) < _CACHE_TTL:
            return _cache

    # 准备符号列表
    index_symbols = list(US_INDICES.keys())
    stock_symbols = list(US_STOCKS.keys())
    all_symbols = index_symbols + stock_symbols

    name_map = {**US_INDICES, **{k: v[0] for k, v in US_STOCKS.items()}}
    sector_map = {k: v[1] for k, v in US_STOCKS.items()}

    # 拉取行情
    raw_quotes = await _fetch_quotes(all_symbols)

    if not raw_quotes:
        # Yahoo API 可能被限流, 返回空骨架
        result = _json_safe({
            "as_of": None,
            "market_open": False,
            "indices": [],
            "breadth": {"total": 0, "up": 0, "down": 0, "flat": 0, "up_pct": 0, "down_pct": 0, "avg_pct": 0},
            "sectors": [],
            "top_gainers": [],
            "top_losers": [],
            "most_active": [],
            "market_cap_leaders": [],
            "distribution": [],
        })
        with _cache_lock:
            _cache = result
            _cache_ts = now
        return result

    # 解析行情
    parsed = [_parse_quote(q, name_map, sector_map) for q in raw_quotes]
    by_symbol = {p["symbol"]: p for p in parsed}

    # 指数
    indices = []
    for sym in index_symbols:
        q = by_symbol.get(sym, {})
        if q:
            indices.append({
                "symbol": sym,
                "name": US_INDICES.get(sym, sym),
                "price": q.get("price"),
                "change": q.get("change"),
                "change_pct": q.get("change_pct"),
            })

    # 股票 (排除指数)
    stocks = [by_symbol[s] for s in stock_symbols if s in by_symbol]

    # 广度统计
    changes = [s["change_pct"] for s in stocks if s.get("change_pct") is not None]
    total = len(changes) or 1
    up = sum(1 for v in changes if v > 0)
    down = sum(1 for v in changes if v < 0)
    flat = total - up - down
    avg_pct = sum(changes) / total if changes else 0

    # 板块表现
    sector_data: dict[str, list[float]] = {}
    for s in stocks:
        sector = s.get("sector", "")
        if not sector:
            continue
        pct = s.get("change_pct")
        if pct is not None:
            sector_data.setdefault(sector, []).append(pct)

    sectors = []
    for sector in US_SECTORS:
        pcts = sector_data.get(sector, [])
        if not pcts:
            continue
        avg = sum(pcts) / len(pcts)
        up_count = sum(1 for v in pcts if v > 0)
        down_count = sum(1 for v in pcts if v < 0)
        # 找板块龙头
        sector_stocks = [s for s in stocks if s.get("sector") == sector]
        leader = max(sector_stocks, key=lambda x: x.get("change_pct") or -999) if sector_stocks else None
        sectors.append({
            "name": sector,
            "avg_pct": avg,
            "count": len(pcts),
            "up_count": up_count,
            "down_count": down_count,
            "leader": {
                "symbol": leader["symbol"],
                "name": leader["name"],
                "change_pct": leader.get("change_pct"),
            } if leader else None,
        })
    sectors.sort(key=lambda x: x["avg_pct"], reverse=True)

    # 涨跌幅排行
    def _top(key: str, desc: bool, limit: int = 10) -> list[dict]:
        filtered = [s for s in stocks if s.get(key) is not None]
        filtered.sort(key=lambda x: x[key] or 0, reverse=desc)
        return [
            {
                "symbol": s["symbol"],
                "name": s["name"],
                "sector": s.get("sector", ""),
                "price": s.get("price"),
                "change_pct": s.get("change_pct"),
                "volume": s.get("volume"),
                "market_cap": s.get("market_cap"),
            }
            for s in filtered[:limit]
        ]

    top_gainers = _top("change_pct", True)
    top_losers = _top("change_pct", False)
    most_active = _top("volume", True)
    market_cap_leaders = _top("market_cap", True)

    # 涨跌分布
    distribution = _pct_band_distribution(changes)

    result = _json_safe({
        "as_of": _now_et_str(),
        "market_open": _is_us_market_open(),
        "indices": indices,
        "breadth": {
            "total": total,
            "up": up,
            "down": down,
            "flat": flat,
            "up_pct": up / total * 100,
            "down_pct": down / total * 100,
            "avg_pct": avg_pct,
        },
        "sectors": sectors,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "most_active": most_active,
        "market_cap_leaders": market_cap_leaders,
        "distribution": distribution,
    })

    with _cache_lock:
        _cache = result
        _cache_ts = now
    return result


def _now_et_str() -> str:
    """当前美东时间字符串。"""
    import datetime as dt
    import zoneinfo
    try:
        et = dt.datetime.now(zoneinfo.ZoneInfo("America/New_York"))
        return et.strftime("%Y-%m-%d %H:%M:%S ET")
    except Exception:  # noqa: BLE001
        return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _pct_band_distribution(values: list[float]) -> list[dict]:
    """涨跌幅分布。"""
    bands = [
        ("<-3%", None, -0.03),
        ("-3~-1%", -0.03, -0.01),
        ("-1~0%", -0.01, 0),
        ("0~1%", 0, 0.01),
        ("1~3%", 0.01, 0.03),
        (">3%", 0.03, None),
    ]
    total = len(values) or 1
    out = []
    for label, low, high in bands:
        count = 0
        for v in values:
            if low is None and v < high:
                count += 1
            elif high is None and v >= low:
                count += 1
            elif low is not None and high is not None and low <= v < high:
                count += 1
        out.append({"label": label, "count": count, "pct": count / total * 100})
    return out


def invalidate_us_overview_cache() -> None:
    """清空美股总览缓存。"""
    global _cache, _cache_ts
    with _cache_lock:
        _cache = None
        _cache_ts = 0.0
