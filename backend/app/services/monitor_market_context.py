"""监控中心使用的新闻与隔夜美股内存快照。

网络刷新始终在独立后台线程执行, 行情评估线程只读取不可变快照。
"""
from __future__ import annotations

import copy
import logging
import math
import threading
from collections.abc import Callable, Iterable
from datetime import date, datetime
from typing import Any

from app.market_time import CN_TZ, cn_now
from app.services.news_sentiment import NewsSentimentService, score_candidate_news

logger = logging.getLogger(__name__)

_BENCHMARK_WEIGHTS = {
    "SPY.US": 0.40,
    "QQQ.US": 0.30,
    "DIA.US": 0.20,
    "IWM.US": 0.10,
}


def unavailable_overnight_us_context(
    status: str,
    *,
    market_date: date | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "status": status,
        "market_date": market_date.isoformat() if market_date else None,
        "score": 0.0,
        "tilt": 0.0,
        "benchmarks": {},
    }


def build_overnight_us_context(
    overview: dict[str, Any],
    trade_date: date,
) -> dict[str, Any]:
    """把美股概览归一化为监控可使用的上一交易日环境分数。"""
    try:
        market_time = datetime.fromisoformat(str(overview.get("market_time") or ""))
    except ValueError:
        return unavailable_overnight_us_context("invalid_market_time")
    market_date = market_time.date()
    if market_date >= trade_date or (trade_date - market_date).days > 7:
        return unavailable_overnight_us_context("stale", market_date=market_date)

    benchmark_returns: dict[str, float] = {}
    weighted_sum = 0.0
    available_weight = 0.0
    for row in overview.get("benchmarks") or []:
        symbol = str(row.get("symbol") or "").upper()
        weight = _BENCHMARK_WEIGHTS.get(symbol)
        value = row.get("change_pct")
        if weight is None or value is None:
            continue
        try:
            change_pct = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(change_pct):
            continue
        benchmark_returns[symbol] = change_pct
        weighted_sum += weight * change_pct
        available_weight += weight
    if available_weight < 0.60:
        return unavailable_overnight_us_context("incomplete", market_date=market_date)

    breadth = overview.get("breadth") or {}
    try:
        up_ratio = float(breadth.get("up_ratio") or 0.0)
        down_ratio = float(breadth.get("down_ratio") or 0.0)
    except (TypeError, ValueError):
        up_ratio = 0.0
        down_ratio = 0.0
    index_return = weighted_sum / available_weight
    breadth_return = max(-1.0, min(1.0, up_ratio - down_ratio)) * 0.01
    score = 0.8 * index_return + 0.2 * breadth_return
    return {
        "available": True,
        "status": str(overview.get("status") or "unknown"),
        "market_date": market_date.isoformat(),
        "as_of": overview.get("as_of"),
        "score": round(score, 8),
        "tilt": round(max(-1.0, min(1.0, score / 0.02)), 8),
        "benchmarks": benchmark_returns,
        "breadth": {
            "up_ratio": up_ratio,
            "down_ratio": down_ratio,
        },
    }


class MonitorMarketContextService:
    """按需维护监控规则使用的外部市场上下文。"""

    def __init__(
        self,
        repo,
        us_market_service,
        news_sentiment_service: NewsSentimentService,
        *,
        refresh_seconds: int = 600,
        now_fn: Callable[[], datetime] = cn_now,
    ) -> None:
        self.repo = repo
        self.us_market_service = us_market_service
        self.news_sentiment_service = news_sentiment_service
        self.refresh_seconds = max(60, int(refresh_seconds))
        self._now_fn = now_fn
        self._lock = threading.Lock()
        self._enabled = False
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._us_refresh_date: date | None = None
        self._instrument_map = self._load_instrument_map()
        now = self._aware_now()
        self._snapshot: dict[str, Any] = {
            "updated_at": None,
            "overnight_us": unavailable_overnight_us_context("starting"),
            "news": NewsSentimentService.unavailable_context("starting", now),
        }

    def _aware_now(self) -> datetime:
        now = self._now_fn()
        if now.tzinfo is None:
            return now.replace(tzinfo=CN_TZ)
        return now.astimezone(CN_TZ)

    def _load_instrument_map(self) -> dict[str, dict[str, Any]]:
        try:
            instruments = self.repo.get_instruments()
            if instruments is None or instruments.is_empty() or "symbol" not in instruments.columns:
                return {}
            return {
                str(row["symbol"]): dict(row)
                for row in instruments.iter_rows(named=True)
            }
        except Exception:
            logger.exception("monitor market context failed to load instruments")
            return {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="monitor-market-context",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            changed = self._enabled != bool(enabled)
            self._enabled = bool(enabled)
        if changed and enabled:
            self._wake_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                enabled = self._enabled
            if enabled:
                self.refresh_once()
                timeout = self.refresh_seconds
            else:
                timeout = 60
            self._wake_event.wait(timeout)
            self._wake_event.clear()

    def refresh_once(self, now: datetime | None = None) -> dict[str, Any]:
        """刷新一次上下文, 供后台线程和定向测试调用。"""
        current = now or self._aware_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=CN_TZ)
        current = current.astimezone(CN_TZ)
        trade_date = current.date()
        with self._lock:
            previous = copy.deepcopy(self._snapshot)

        overnight = previous["overnight_us"]
        if self._us_refresh_date != trade_date or not overnight.get("available"):
            try:
                overnight = build_overnight_us_context(
                    self.us_market_service.get_overview(),
                    trade_date,
                )
                if overnight.get("available"):
                    self._us_refresh_date = trade_date
            except Exception:
                logger.exception("monitor overnight US context refresh failed")
                overnight = unavailable_overnight_us_context("unavailable")

        try:
            news = self.news_sentiment_service.get_context(current)
        except Exception:
            logger.exception("monitor news context refresh failed")
            news = NewsSentimentService.unavailable_context("unavailable", current)

        snapshot = {
            "updated_at": current.isoformat(timespec="seconds"),
            "overnight_us": overnight,
            "news": news,
        }
        with self._lock:
            self._snapshot = snapshot
        return copy.deepcopy(snapshot)

    def snapshot_for(self, symbols: Iterable[str]) -> dict[str, Any]:
        """返回内存快照及指定股票的新闻情绪, 不触发任何网络请求。"""
        with self._lock:
            snapshot = copy.deepcopy(self._snapshot)
        candidates = {
            str(symbol): self._instrument_map.get(str(symbol), {})
            for symbol in symbols
            if symbol
        }
        snapshot["candidate_news"] = score_candidate_news(
            snapshot.get("news") or {},
            candidates,
        )
        return snapshot

    def runtime_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "updated_at": self._snapshot.get("updated_at"),
                "overnight_us": copy.deepcopy(self._snapshot.get("overnight_us") or {}),
                "news": copy.deepcopy(self._snapshot.get("news") or {}),
            }
