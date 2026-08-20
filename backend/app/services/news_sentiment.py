"""Deterministic multi-source news sentiment for the investment expert."""
from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import math
import re
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.market_news import fetch_market_news

logger = logging.getLogger(__name__)
_CN_TZ = ZoneInfo("Asia/Shanghai")
_MAX_AGE_HOURS = 48.0
_POSITIVE_TERMS = (
    "超预期", "上调", "增持", "回购", "中标", "签约", "获批", "降息", "降准",
    "大涨", "上涨", "走强", "反弹", "突破", "创新高", "增长", "盈利", "扭亏",
    "扩产", "复苏", "支持", "利好", "beat", "upgrade", "surge", "rally", "record high",
    "rate cut", "stimulus", "approval", "buyback",
)
_NEGATIVE_TERMS = (
    "不及预期", "下调", "减持", "处罚", "调查", "召回", "违约", "破产", "加息",
    "暴跌", "下跌", "走弱", "跳水", "新低", "亏损", "裁员", "制裁", "禁令",
    "关税", "风险", "利空", "miss", "downgrade", "plunge", "selloff", "default",
    "bankruptcy", "layoff", "sanction", "tariff", "rate hike", "recall",
)
_STRONG_TERMS = (
    "大涨", "暴跌", "飙升", "跳水", "重大", "紧急", "超预期", "不及预期",
    "surge", "plunge", "emergency", "record high", "bankruptcy",
)
_GLOBAL_TERMS = (
    "美股", "美国", "美联储", "纳斯达克", "标普", "道琼斯", "英伟达", "特斯拉",
    "欧洲", "欧央行", "日本央行", "全球", "原油", "黄金", "美元", "fed", "nasdaq",
    "s&p", "dow jones", "nvidia", "tesla", "ecb", "wall street", "oil",
)
_DOMESTIC_TERMS = (
    "A股", "中国", "国务院", "央行", "证监会", "金融监管总局", "财政部", "商务部",
    "发改委", "沪指", "深成指", "创业板", "科创板", "人民币", "北交所", "沪深",
)
_NAME_SUFFIXES = (
    "股份有限公司", "有限责任公司", "股份", "集团", "控股", "科技", "发展", "实业",
)
_TAG_SPLIT_RE = re.compile(r"[,，;；/、|]+")  # noqa: RUF001

NewsFetcher = Callable[..., Awaitable[list[dict[str, str]]] | list[dict[str, str]]]


def _parse_published_at(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CN_TZ)
    return parsed.astimezone(_CN_TZ)


def _score_text(value: str) -> float:
    text = value.casefold()
    positive = sum(term.casefold() in text for term in _POSITIVE_TERMS)
    negative = sum(term.casefold() in text for term in _NEGATIVE_TERMS)
    total = positive + negative
    if total == 0:
        return 0.0
    score = (positive - negative) / total
    if any(term.casefold() in text for term in _STRONG_TERMS):
        score *= 1.25
    return max(-1.0, min(1.0, score))


def _region(value: str) -> str:
    folded = value.casefold()
    if any(term.casefold() in folded for term in _DOMESTIC_TERMS):
        return "domestic"
    if any(term.casefold() in folded for term in _GLOBAL_TERMS):
        return "global"
    return "market"


def _candidate_terms(symbol: str, metadata: Mapping[str, Any]) -> tuple[str, ...]:
    terms: list[str] = []
    code_match = re.search(r"(?<!\d)\d{6}(?!\d)", symbol)
    if code_match:
        terms.append(code_match.group(0))
    name = str(metadata.get("name") or "").strip()
    if len(name) >= 2:
        terms.append(name)
        for suffix in _NAME_SUFFIXES:
            if name.endswith(suffix) and len(name) - len(suffix) >= 2:
                terms.append(name[: -len(suffix)])
                break
    for key in (
        "industry", "industry_name", "行业", "sector", "sector_name", "行业名称",
        "concept", "concept_name", "概念", "题材",
    ):
        raw = metadata.get(key)
        if raw is None:
            continue
        for term in _TAG_SPLIT_RE.split(str(raw)):
            term = term.strip()
            if len(term) >= 2:
                terms.append(term)
    return tuple(dict.fromkeys(terms))


def score_candidate_news(
    context: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return point-in-time candidate sentiment from exact name/code/tag matches."""
    items = context.get("items")
    if not isinstance(items, list):
        items = []
    result: dict[str, dict[str, Any]] = {}
    for symbol, metadata in candidates.items():
        terms = _candidate_terms(symbol, metadata)
        weighted_sum = 0.0
        total_weight = 0.0
        headlines: list[str] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            text = f"{item.get('title') or ''} {item.get('snippet') or ''}".casefold()
            if not any(term.casefold() in text for term in terms):
                continue
            sentiment = float(item.get("sentiment") or 0.0)
            weight = float(item.get("recency_weight") or 0.0)
            if sentiment == 0 or weight <= 0:
                continue
            weighted_sum += sentiment * weight
            total_weight += weight
            if len(headlines) < 3:
                headlines.append(str(item.get("title") or ""))
        result[str(symbol)] = {
            "score": round(weighted_sum / total_weight, 8) if total_weight else 0.0,
            "matched_count": len(headlines),
            "headlines": headlines,
        }
    return result


class NewsSentimentService:
    """Fetch and score global, domestic and intraday market news."""

    refresh_seconds = 600

    def __init__(
        self,
        *,
        fetcher: NewsFetcher | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetcher = fetcher or fetch_market_news
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._cache: dict[str, Any] | None = None

    def get_context(self, as_of: datetime, *, force: bool = False) -> dict[str, Any]:
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=_CN_TZ)
        as_of = as_of.astimezone(_CN_TZ)
        with self._lock:
            now = self._monotonic()
            if (
                not force
                and self._cache is not None
                and now - self._cached_at < self.refresh_seconds
            ):
                return copy.deepcopy(self._cache)
            try:
                fetched = self._fetcher(as_of=as_of.date(), limit=60)
                rows = asyncio.run(fetched) if inspect.isawaitable(fetched) else fetched
                context = self._build_context(rows, as_of)
            except Exception as exc:
                logger.warning("news sentiment refresh failed: %s", type(exc).__name__)
                context = self.unavailable_context("unavailable", as_of)
            self._cache = context
            self._cached_at = now
            return copy.deepcopy(context)

    @staticmethod
    def unavailable_context(status: str, as_of: datetime) -> dict[str, Any]:
        return {
            "available": False,
            "status": status,
            "as_of": as_of.astimezone(_CN_TZ).isoformat(timespec="seconds"),
            "score": 0.0,
            "confidence": 0.0,
            "item_count": 0,
            "signal_count": 0,
            "source_count": 0,
            "regions": {"global": 0, "domestic": 0, "market": 0},
            "items": [],
        }

    @staticmethod
    def score_candidates(
        context: Mapping[str, Any],
        candidates: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        return score_candidate_news(context, candidates)

    @staticmethod
    def _build_context(rows: Sequence[Mapping[str, Any]], as_of: datetime) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        regions = {"global": 0, "domestic": 0, "market": 0}
        sources: set[str] = set()
        weighted_sum = 0.0
        total_weight = 0.0
        for row in rows:
            published_at = _parse_published_at(row.get("published_at"))
            if published_at is None:
                continue
            age_hours = (as_of - published_at).total_seconds() / 3600
            if age_hours < -5 / 60 or age_hours > _MAX_AGE_HOURS:
                continue
            title = str(row.get("title") or "").strip()
            snippet = str(row.get("snippet") or "").strip()
            if not title:
                continue
            sentiment = _score_text(f"{title} {snippet}")
            recency_weight = 0.5 ** (max(age_hours, 0.0) / 12.0)
            item_region = _region(f"{title} {snippet}")
            regions[item_region] += 1
            source = str(row.get("source") or row.get("provider") or "未知来源")
            sources.add(source)
            if sentiment:
                weighted_sum += sentiment * recency_weight
                total_weight += recency_weight
            items.append({
                "title": title,
                "snippet": snippet,
                "published_at": published_at.isoformat(timespec="seconds"),
                "source": source,
                "source_url": str(row.get("source_url") or ""),
                "region": item_region,
                "sentiment": round(sentiment, 8),
                "recency_weight": round(recency_weight, 8),
            })
        signal_count = sum(bool(item["sentiment"]) for item in items)
        score = weighted_sum / total_weight if total_weight else 0.0
        confidence = min(signal_count / 6, 1.0) * min(len(items) / 12, 1.0)
        return {
            "available": bool(items),
            "status": "live" if items else "no_data",
            "as_of": as_of.isoformat(timespec="seconds"),
            "score": round(max(-1.0, min(1.0, score)), 8),
            "confidence": round(math.sqrt(confidence), 8),
            "item_count": len(items),
            "signal_count": signal_count,
            "source_count": len(sources),
            "regions": regions,
            "items": items,
        }
