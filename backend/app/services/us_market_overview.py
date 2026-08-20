"""美股市场聚合看板服务。

实时全市场报价只在内存中用于聚合;磁盘仅保存聚合后的快照。
"""
from __future__ import annotations

import copy
import json
import logging
import math
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from statistics import fmean, median
from typing import Any
from zoneinfo import ZoneInfo

from app.tickflow.client import get_client, get_paid_realtime_client

logger = logging.getLogger(__name__)

US_UNIVERSE = "US_Equity"
NEW_YORK_TZ = ZoneInfo("America/New_York")
BEIJING_TZ = ZoneInfo("Asia/Shanghai")

BENCHMARKS: dict[str, str] = {
    "SPY.US": "标普 500 ETF",
    "QQQ.US": "纳斯达克 100 ETF",
    "DIA.US": "道琼斯 ETF",
    "IWM.US": "罗素 2000 ETF",
}

SECTORS: dict[str, str] = {
    "XLK.US": "信息技术",
    "XLC.US": "通信服务",
    "XLY.US": "可选消费",
    "XLP.US": "日常消费",
    "XLF.US": "金融",
    "XLV.US": "医疗保健",
    "XLI.US": "工业",
    "XLE.US": "能源",
    "XLB.US": "原材料",
    "XLRE.US": "房地产",
    "XLU.US": "公用事业",
}

PROXY_SYMBOLS = [*BENCHMARKS, *SECTORS]

# 全市场池不可用时,用一次有限的按标的实时请求保留可观察的美股实时路径。
# 该样本只用于实时市场温度和排行,响应会明确标记为样本,不冒充全市场统计。
REALTIME_SAMPLE: dict[str, str] = {
    "AAPL.US": "苹果",
    "MSFT.US": "微软",
    "NVDA.US": "英伟达",
    "AMZN.US": "亚马逊",
    "GOOGL.US": "谷歌",
    "META.US": "Meta",
    "TSLA.US": "特斯拉",
    "AVGO.US": "博通",
    "AMD.US": "AMD",
    "NFLX.US": "奈飞",
    "JPM.US": "摩根大通",
    "BAC.US": "美国银行",
    "WMT.US": "沃尔玛",
    "COST.US": "开市客",
    "LLY.US": "礼来",
    "UNH.US": "联合健康",
    "XOM.US": "埃克森美孚",
    "CVX.US": "雪佛龙",
    "CAT.US": "卡特彼勒",
    "GE.US": "GE 航空",
}


def _data_path(*steps: tuple[str, str, str]) -> list[dict[str, str]]:
    return [
        {"label": label, "detail": detail, "status": status}
        for label, detail, status in steps
    ]


class UsMarketUnavailableError(RuntimeError):
    """实时、快照和日线代理均不可用。"""


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _timestamp_ms(value: Any) -> int | None:
    if value is None:
        return None
    if hasattr(value, "timestamp"):
        try:
            return int(value.timestamp() * 1000)
        except (OSError, TypeError, ValueError):
            return None
    number = _finite(value)
    if number is not None:
        return int(number * 1000) if number < 100_000_000_000 else int(number)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NEW_YORK_TZ)
    return int(parsed.timestamp() * 1000)


def _iso_time(timestamp_ms: int | None, timezone: ZoneInfo) -> str | None:
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone).isoformat()


def _session_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "unknown")


def normalize_us_quote(quote: Mapping[str, Any]) -> dict[str, Any] | None:
    """把 TickFlow quote 规范为看板内部结构,涨跌幅保持小数制。"""
    symbol = str(quote.get("symbol") or "").strip().upper()
    if not symbol:
        return None

    ext = quote.get("ext")
    ext = ext if isinstance(ext, Mapping) else {}
    last_price = _finite(quote.get("last_price"))
    prev_close = _finite(quote.get("prev_close"))
    open_price = _finite(quote.get("open"))
    high = _finite(quote.get("high"))
    low = _finite(quote.get("low"))
    change_amount = _finite(ext.get("change_amount"))
    change_pct = _finite(ext.get("change_pct"))
    amplitude = _finite(quote.get("amplitude"))

    if change_amount is None:
        change_amount = _finite(quote.get("change_amount"))
    if change_pct is None:
        change_pct = _finite(quote.get("change_pct"))
    if amplitude is None:
        amplitude = _finite(ext.get("amplitude"))
    if amplitude is not None and amplitude < 0:
        amplitude = None

    if change_amount is None and last_price is not None and prev_close is not None:
        change_amount = last_price - prev_close
    if change_pct is None and change_amount is not None and prev_close not in (None, 0):
        change_pct = change_amount / prev_close
    if (
        amplitude is None
        and high is not None
        and low is not None
        and high >= low
        and (prev_close or 0) > 0
    ):
        amplitude = (high - low) / prev_close

    volume = _finite(quote.get("volume"))
    amount = _finite(quote.get("amount"))
    amount_estimated = False
    if (amount is None or amount <= 0) and last_price is not None and volume is not None:
        amount = last_price * volume
        amount_estimated = True

    return {
        "symbol": symbol,
        "name": str(quote.get("name") or ext.get("name") or symbol),
        "last_price": last_price,
        "prev_close": prev_close,
        "open": open_price,
        "high": high,
        "low": low,
        "change_amount": change_amount,
        "change_pct": change_pct,
        "amplitude": amplitude,
        "volume": volume,
        "amount": amount,
        "amount_estimated": amount_estimated,
        "timestamp": _timestamp_ms(quote.get("timestamp")),
        "session": _session_value(quote.get("session")),
    }


def _valid_market_row(row: Mapping[str, Any]) -> bool:
    return (
        _finite(row.get("last_price")) is not None
        and _finite(row.get("last_price")) > 0
        and _finite(row.get("prev_close")) is not None
        and _finite(row.get("prev_close")) > 0
        and _finite(row.get("change_pct")) is not None
    )


def _public_quote(row: Mapping[str, Any], *, label: str | None = None) -> dict[str, Any]:
    return {
        "symbol": row["symbol"],
        "name": label or row["name"],
        "last_price": row["last_price"],
        "prev_close": row["prev_close"],
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "change_amount": row["change_amount"],
        "change_pct": row["change_pct"],
        "amplitude": row["amplitude"],
        "volume": row["volume"],
        "amount": row["amount"],
        "amount_estimated": row["amount_estimated"],
        "timestamp": row["timestamp"],
    }


def _distribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bands = (
        ("< -5%", None, -0.05),
        ("-5% ~ -2%", -0.05, -0.02),
        ("-2% ~ 0%", -0.02, 0.0),
        ("0% ~ 2%", 0.0, 0.02),
        ("2% ~ 5%", 0.02, 0.05),
        ("≥ 5%", 0.05, None),
    )
    total = len(rows)
    output: list[dict[str, Any]] = []
    for label, lower, upper in bands:
        count = sum(
            1
            for row in rows
            if (lower is None or row["change_pct"] >= lower)
            and (upper is None or row["change_pct"] < upper)
        )
        output.append({"label": label, "count": count, "ratio": count / total if total else 0})
    return output


def build_live_overview(
    market_quotes: list[Mapping[str, Any]],
    proxy_quotes: list[Mapping[str, Any]] | None = None,
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """从全市场报价构建不含原始行情的聚合响应。"""
    normalized: dict[str, dict[str, Any]] = {}
    for quote in [*market_quotes, *(proxy_quotes or [])]:
        row = normalize_us_quote(quote)
        if row is not None:
            normalized[row["symbol"]] = row

    valid_rows = [row for row in normalized.values() if _valid_market_row(row)]
    market_rows = [row for row in valid_rows if row["symbol"] not in PROXY_SYMBOLS]
    if not market_rows:
        raise UsMarketUnavailableError("TickFlow 未返回有效的美股全市场行情")

    up = sum(row["change_pct"] >= 0.00005 for row in market_rows)
    down = sum(row["change_pct"] <= -0.00005 for row in market_rows)
    flat = len(market_rows) - up - down
    positive = sum(row["change_pct"] >= 0.02 for row in market_rows)
    weak = sum(row["change_pct"] <= -0.02 for row in market_rows)
    changes = [row["change_pct"] for row in market_rows]

    ranking_rows = [
        row
        for row in market_rows
        if row["last_price"] >= 1
        and (row["volume"] or 0) > 0
        and row["name"]
        and row["symbol"]
    ]
    active_rows = [row for row in market_rows if (row["volume"] or 0) > 0]

    latest_ms = max(
        (row["timestamp"] for row in normalized.values() if row["timestamp"] is not None),
        default=now_ms or int(time.time() * 1000),
    )
    sessions = Counter(
        row["session"] for row in normalized.values() if row["session"] != "unknown"
    )
    session = sessions.most_common(1)[0][0] if sessions else "unknown"

    def proxies(labels: Mapping[str, str]) -> list[dict[str, Any]]:
        return [
            _public_quote(normalized[symbol], label=label)
            for symbol, label in labels.items()
            if symbol in normalized and _valid_market_row(normalized[symbol])
        ]

    total = len(market_rows)
    return {
        "schema_version": 1,
        "status": "live",
        "source": "TickFlow",
        "message": "TickFlow 美股全市场实时聚合",
        "as_of": latest_ms,
        "market_timezone": str(NEW_YORK_TZ),
        "market_time": _iso_time(latest_ms, NEW_YORK_TZ),
        "beijing_time": _iso_time(latest_ms, BEIJING_TZ),
        "session": session,
        "stale": False,
        "realtime": True,
        "coverage": "full_market",
        "coverage_label": f"全市场 {total:,} 只有效样本",
        "data_path": _data_path(
            ("TickFlow", "实时行情", "ok"),
            ("US_Equity", "全市场池", "ok"),
            ("看板", "实时聚合", "ok"),
        ),
        "breadth": {
            "total": total,
            "up": up,
            "down": down,
            "flat": flat,
            "strong": positive,
            "weak": weak,
            "up_ratio": up / total,
            "down_ratio": down / total,
            "average_change_pct": fmean(changes),
            "median_change_pct": median(changes),
            "advance_decline_ratio": up / down if down else None,
            "net_advance_ratio": (up - down) / total,
        },
        "distribution": _distribution(market_rows),
        "benchmarks": proxies(BENCHMARKS),
        "sectors": sorted(
            proxies(SECTORS),
            key=lambda row: row["change_pct"],
            reverse=True,
        ),
        "rankings": {
            "gainers": [
                _public_quote(row)
                for row in sorted(ranking_rows, key=lambda item: item["change_pct"], reverse=True)[:10]
            ],
            "losers": [
                _public_quote(row)
                for row in sorted(ranking_rows, key=lambda item: item["change_pct"])[:10]
            ],
            "active": [
                _public_quote(row)
                for row in sorted(active_rows, key=lambda item: item["amount"] or 0, reverse=True)[:10]
            ],
            "volatile": [
                _public_quote(row)
                for row in sorted(
                    (item for item in ranking_rows if item["amplitude"] is not None),
                    key=lambda item: item["amplitude"],
                    reverse=True,
                )[:10]
            ],
        },
    }


def _frame_records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if isinstance(frame, Mapping):
        columns = {key: value for key, value in frame.items() if isinstance(value, list)}
        row_count = max((len(value) for value in columns.values()), default=0)
        return [
            {key: values[index] for key, values in columns.items() if index < len(values)}
            for index in range(row_count)
        ]
    if hasattr(frame, "to_dicts"):
        return list(frame.to_dicts())
    if hasattr(frame, "to_dict"):
        try:
            records = frame.to_dict("records")
            return list(records) if isinstance(records, list) else []
        except TypeError:
            pass
    if isinstance(frame, list):
        return [dict(row) for row in frame if isinstance(row, Mapping)]
    return []


def _daily_proxy_quote(symbol: str, frame: Any) -> dict[str, Any] | None:
    rows = _frame_records(frame)
    rows.sort(key=lambda row: _timestamp_ms(row.get("timestamp") or row.get("date")) or 0)
    valid = [row for row in rows if (_finite(row.get("close")) or 0) > 0]
    if not valid:
        return None

    latest = valid[-1]
    previous = valid[-2] if len(valid) > 1 else None
    last_price = _finite(latest.get("close"))
    prev_close = _finite(previous.get("close")) if previous else _finite(latest.get("prev_close"))
    timestamp = _timestamp_ms(latest.get("timestamp") or latest.get("date"))
    return {
        "symbol": symbol,
        "name": BENCHMARKS.get(symbol) or SECTORS.get(symbol) or symbol,
        "last_price": last_price,
        "prev_close": prev_close,
        "open": _finite(latest.get("open")),
        "high": _finite(latest.get("high")),
        "low": _finite(latest.get("low")),
        "volume": _finite(latest.get("volume")),
        "amount": _finite(latest.get("amount")),
        "timestamp": timestamp,
        "session": "closed",
        "ext": {},
    }


def build_proxy_overview(
    proxy_quotes: list[Mapping[str, Any]],
    *,
    message: str,
    now_ms: int | None = None,
    stale: bool = False,
    realtime: bool = True,
) -> dict[str, Any]:
    """从有限的 ETF 报价构建不含全市场统计的降级响应。"""
    normalized: dict[str, dict[str, Any]] = {}
    for quote in proxy_quotes:
        row = normalize_us_quote(quote)
        if row is not None and row["symbol"] in PROXY_SYMBOLS and _valid_market_row(row):
            normalized[row["symbol"]] = row
    if not normalized:
        raise UsMarketUnavailableError("TickFlow 未返回有效的美股 ETF 行情")

    latest_ms = max(
        (row["timestamp"] for row in normalized.values() if row["timestamp"] is not None),
        default=now_ms or int(time.time() * 1000),
    )
    sessions = Counter(
        row["session"] for row in normalized.values() if row["session"] != "unknown"
    )
    session = sessions.most_common(1)[0][0] if sessions else "unknown"

    def proxies(labels: Mapping[str, str]) -> list[dict[str, Any]]:
        return [
            _public_quote(normalized[symbol], label=label)
            for symbol, label in labels.items()
            if symbol in normalized
        ]

    return {
        "schema_version": 1,
        "status": "partial",
        "source": "TickFlow",
        "message": message,
        "as_of": latest_ms,
        "market_timezone": str(NEW_YORK_TZ),
        "market_time": _iso_time(latest_ms, NEW_YORK_TZ),
        "beijing_time": _iso_time(latest_ms, BEIJING_TZ),
        "session": session,
        "stale": stale,
        "realtime": realtime,
        "coverage": "etf_realtime" if realtime else "etf_daily",
        "coverage_label": "指数与行业 ETF 实时行情" if realtime else "指数与行业 ETF 最新日线",
        "data_path": _data_path(
            ("TickFlow", "实时行情" if realtime else "历史日线", "ok"),
            ("ETF", "指数与行业代理", "ok"),
            ("看板", "有限展示", "limited"),
        ),
        "breadth": None,
        "distribution": [],
        "benchmarks": proxies(BENCHMARKS),
        "sectors": sorted(
            proxies(SECTORS),
            key=lambda row: row["change_pct"],
            reverse=True,
        ),
        "rankings": {"gainers": [], "losers": [], "active": [], "volatile": []},
    }


def build_partial_overview(raw: Any, *, now_ms: int | None = None) -> dict[str, Any]:
    """从 ETF 最近两个日线收盘构建首次启动降级响应。"""
    frames = raw if isinstance(raw, Mapping) else {}
    proxy_quotes: list[Mapping[str, Any]] = []
    for symbol in PROXY_SYMBOLS:
        quote = _daily_proxy_quote(symbol, frames.get(symbol))
        if quote is not None:
            proxy_quotes.append(quote)
    return build_proxy_overview(
        proxy_quotes,
        message="当前无美股实时行情权限。显示 ETF 最新日线代理行情",
        now_ms=now_ms,
        stale=True,
        realtime=False,
    )


class UsMarketOverviewService:
    LIVE_TTL_SECONDS = 15.0
    SAMPLE_TTL_SECONDS = 60.0
    FALLBACK_TTL_SECONDS = 300.0

    def __init__(
        self,
        data_dir: Path,
        *,
        realtime_client_factory: Callable[[], Any | None] = get_paid_realtime_client,
        history_client_factory: Callable[[], Any] = get_client,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._snapshot_path = Path(data_dir) / "us_market" / "overview_snapshot.json"
        self._realtime_client_factory = realtime_client_factory
        self._history_client_factory = history_client_factory
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._refreshing = False
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0

    def get_overview(self, *, force: bool = False) -> dict[str, Any]:
        with self._condition:
            if not force and self._cache_is_fresh():
                return copy.deepcopy(self._cache)
            if self._refreshing:
                if self._cache is not None:
                    return copy.deepcopy(self._cache)
                completed = self._condition.wait_for(lambda: not self._refreshing, timeout=130)
                if self._cache is not None:
                    return copy.deepcopy(self._cache)
                if not completed:
                    raise UsMarketUnavailableError("美股行情正在刷新。请稍后重试")
            self._refreshing = True

        try:
            result = self._refresh()
        except Exception:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._cache = result
            self._cache_at = self._monotonic()
            self._refreshing = False
            self._condition.notify_all()
            return copy.deepcopy(result)

    def _cache_is_fresh(self) -> bool:
        if self._cache is None:
            return False
        coverage = self._cache.get("coverage")
        if coverage == "full_market":
            ttl = self.LIVE_TTL_SECONDS
        elif coverage == "sample":
            ttl = self.SAMPLE_TTL_SECONDS
        else:
            ttl = self.FALLBACK_TTL_SECONDS
        return self._monotonic() - self._cache_at < ttl

    def _refresh(self) -> dict[str, Any]:
        try:
            realtime = self._fetch_realtime()
        except Exception as exc:
            logger.info("美股全市场实时行情不可用,尝试降级: %s", exc)
        else:
            self._write_snapshot(realtime)
            return realtime

        try:
            return self._fetch_realtime_sample()
        except Exception as exc:
            logger.info("美股核心样本实时行情不可用,尝试 ETF: %s", exc)

        try:
            return self._fetch_realtime_proxies()
        except Exception as exc:
            logger.info("美股 ETF 实时报价不可用,尝试快照: %s", exc)

        snapshot = self._read_snapshot()
        if snapshot is not None:
            snapshot["status"] = "snapshot"
            snapshot["message"] = "实时行情不可用。显示最近一次美股聚合快照"
            snapshot["stale"] = True
            snapshot["realtime"] = False
            snapshot["coverage"] = "snapshot"
            snapshot["coverage_label"] = "最近一次全市场聚合快照"
            snapshot["data_path"] = _data_path(
                ("本地快照", "历史聚合", "cached"),
                ("看板", "只读展示", "limited"),
            )
            return snapshot

        try:
            return self._fetch_daily_proxies()
        except Exception as exc:
            logger.warning("美股 ETF 日线降级不可用: %s", exc)
            raise UsMarketUnavailableError("美股行情暂时不可用。请稍后重试") from exc

    def _fetch_realtime(self) -> dict[str, Any]:
        client = self._realtime_client_factory()
        if client is None:
            raise UsMarketUnavailableError("未配置 TickFlow 实时行情客户端")
        market_quotes = client.quotes.get_by_universes(universes=[US_UNIVERSE]) or []
        proxy_quotes = self._fetch_proxy_quote_rows(client)
        return build_live_overview(market_quotes, proxy_quotes)

    def _fetch_realtime_proxies(self) -> dict[str, Any]:
        client = self._realtime_client_factory()
        if client is None:
            raise UsMarketUnavailableError("未配置 TickFlow 实时行情客户端")
        return build_proxy_overview(
            self._fetch_proxy_quote_rows(client),
            message="当前无美股全市场实时权限。显示 ETF 实时行情",
        )

    def _fetch_realtime_sample(self) -> dict[str, Any]:
        client = self._realtime_client_factory()
        if client is None:
            raise UsMarketUnavailableError("未配置 TickFlow 实时行情客户端")
        rows = self._fetch_quote_rows(client, [*PROXY_SYMBOLS, *REALTIME_SAMPLE])
        market_quotes: list[Mapping[str, Any]] = []
        proxy_quotes: list[Mapping[str, Any]] = []
        for row in rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol in REALTIME_SAMPLE:
                labeled = dict(row)
                labeled["name"] = REALTIME_SAMPLE[symbol]
                market_quotes.append(labeled)
            elif symbol in PROXY_SYMBOLS:
                proxy_quotes.append(row)

        result = build_live_overview(market_quotes, proxy_quotes)
        sample_total = result["breadth"]["total"]
        result.update(
            status="partial",
            message="全市场池权限不可用。显示核心样本与 ETF 实时行情",
            coverage="sample",
            coverage_label=f"核心样本 {sample_total}/{len(REALTIME_SAMPLE)} 只",
            data_path=_data_path(
                ("TickFlow", "实时行情", "ok"),
                ("全市场池", "当前权限不可用", "unavailable"),
                ("核心样本", f"{sample_total} 只实时", "ok"),
                ("看板", "样本聚合", "limited"),
            ),
        )
        return result

    @staticmethod
    def _fetch_proxy_quote_rows(client: Any) -> list[Mapping[str, Any]]:
        return UsMarketOverviewService._fetch_quote_rows(client, PROXY_SYMBOLS)

    @staticmethod
    def _fetch_quote_rows(client: Any, requested: list[str]) -> list[Mapping[str, Any]]:
        rows: list[Mapping[str, Any]] = []
        batch_get = getattr(client.quotes, "get_by_symbols", None)
        for start in range(0, len(requested), 5):
            symbols = requested[start : start + 5]
            try:
                if callable(batch_get):
                    rows.extend(batch_get(symbols, as_dataframe=False) or [])
                else:
                    rows.extend(client.quotes.get(symbols=symbols) or [])
            except Exception as batch_exc:
                if not callable(batch_get):
                    logger.info(
                        "美股按标的实时报价读取失败 (%s): %s",
                        ",".join(symbols),
                        batch_exc,
                    )
                    continue
                try:
                    rows.extend(client.quotes.get(symbols=symbols) or [])
                except Exception as symbol_exc:
                    logger.info(
                        "美股批量与按标的实时报价均失败 (%s): %s; %s",
                        ",".join(symbols),
                        batch_exc,
                        symbol_exc,
                    )
        return rows

    def _fetch_daily_proxies(self) -> dict[str, Any]:
        client = self._history_client_factory()
        raw: dict[str, Any] = {}
        for symbol in PROXY_SYMBOLS:
            try:
                raw[symbol] = client.klines.get(
                    symbol,
                    period="1d",
                    adjust="none",
                    count=2,
                    as_dataframe=False,
                )
            except Exception as exc:
                logger.info("美股 ETF 日线读取失败 (%s): %s", symbol, exc)
        return build_partial_overview(raw)

    def _read_snapshot(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("美股聚合快照读取失败: %s", exc)
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            logger.warning("美股聚合快照格式无效")
            return None
        payload.setdefault("market_timezone", str(NEW_YORK_TZ))
        payload.setdefault("stale", True)
        payload.setdefault("realtime", False)
        payload.setdefault("coverage", "snapshot")
        payload.setdefault("coverage_label", "最近一次全市场聚合快照")
        payload.setdefault(
            "data_path",
            _data_path(
                ("本地快照", "历史聚合", "cached"),
                ("看板", "只读展示", "limited"),
            ),
        )
        rankings = payload.get("rankings")
        if isinstance(rankings, dict):
            rankings.setdefault("volatile", [])
        return payload

    def _write_snapshot(self, payload: Mapping[str, Any]) -> None:
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._snapshot_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self._snapshot_path)
        except OSError as exc:
            logger.warning("美股聚合快照写入失败,不影响本次响应: %s", exc)
