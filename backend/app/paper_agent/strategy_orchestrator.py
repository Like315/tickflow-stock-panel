"""基于时点数据为 AI 投资专家编排当日策略。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from statistics import median
from typing import Any, TypeAlias

import polars as pl


class MarketRegimeState(StrEnum):
    """投资专家支持的有限市场状态。"""

    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    VOLATILE = "volatile"
    BALANCED = "balanced"


class StrategyProfile(StrEnum):
    """策略按交易行为划分的有限画像。"""

    MOMENTUM = "momentum"
    REVERSAL = "reversal"
    DEFENSIVE = "defensive"
    BALANCED = "balanced"


class StrategySource(StrEnum):
    """投资专家允许参与编排的策略来源。"""

    BUILTIN = "builtin"
    AI = "ai"


# 策略关键词只用于行为画像，不参与交易信号计算。
_MOMENTUM_TERMS: tuple[str, ...] = (
    "trend",
    "momentum",
    "breakout",
    "leader",
    "surge",
    "golden",
    "bullish",
    "趋势",
    "动量",
    "突破",
    "龙头",
    "强势",
    "涨停",
    "放量",
    "金叉",
    "多头",
)
_REVERSAL_TERMS: tuple[str, ...] = (
    "reversal",
    "recovery",
    "bounce",
    "pullback",
    "oversold",
    "反转",
    "反包",
    "反弹",
    "回踩",
    "超卖",
    "低点",
)
_DEFENSIVE_TERMS: tuple[str, ...] = (
    "defensive",
    "low_volatility",
    "support",
    "低波",
    "防御",
    "支撑",
)

# 不同市场状态下各策略画像的相对适配度。
_REGIME_FITS: Mapping[MarketRegimeState, Mapping[StrategyProfile, float]] = {
    MarketRegimeState.RISK_ON: {
        StrategyProfile.MOMENTUM: 1.35,
        StrategyProfile.BALANCED: 1.00,
        StrategyProfile.REVERSAL: 0.80,
        StrategyProfile.DEFENSIVE: 0.75,
    },
    MarketRegimeState.RISK_OFF: {
        StrategyProfile.DEFENSIVE: 1.35,
        StrategyProfile.REVERSAL: 1.20,
        StrategyProfile.BALANCED: 0.90,
        StrategyProfile.MOMENTUM: 0.65,
    },
    MarketRegimeState.VOLATILE: {
        StrategyProfile.DEFENSIVE: 1.25,
        StrategyProfile.REVERSAL: 1.20,
        StrategyProfile.BALANCED: 0.90,
        StrategyProfile.MOMENTUM: 0.75,
    },
    MarketRegimeState.BALANCED: {
        StrategyProfile.BALANCED: 1.10,
        StrategyProfile.MOMENTUM: 1.00,
        StrategyProfile.REVERSAL: 1.00,
        StrategyProfile.DEFENSIVE: 1.00,
    },
}

StrategyCatalogItem: TypeAlias = Mapping[str, Any]
StrategyCandidate: TypeAlias = tuple[StrategyCatalogItem, StrategyProfile, float]


@dataclass(frozen=True, slots=True)
class MarketRegime:
    """仅使用交易日前已知数据生成的可审计市场状态快照。"""

    state: MarketRegimeState
    source_date: str | None
    score: float
    advance_ratio: float | None
    median_momentum_20d: float | None
    median_volatility_20d: float | None
    overnight_tilt: float
    news_score: float
    news_confidence: float

    def as_dict(self) -> dict[str, Any]:
        """返回适合持久化和接口输出的普通字典。"""
        return {
            "state": MarketRegimeState(self.state).value,
            "source_date": self.source_date,
            "score": self.score,
            "advance_ratio": self.advance_ratio,
            "median_momentum_20d": self.median_momentum_20d,
            "median_volatility_20d": self.median_volatility_20d,
            "overnight_tilt": self.overnight_tilt,
            "news_score": self.news_score,
            "news_confidence": self.news_confidence,
        }


@dataclass(frozen=True, slots=True)
class StrategyAllocation:
    """单个策略在当日组合中的权重与审计信息。"""

    strategy_id: str
    name: str
    source: StrategySource
    weight: float
    profile: StrategyProfile
    fit_score: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        """返回适合持久化和接口输出的普通字典。"""
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "source": self.source.value,
            "weight": self.weight,
            "profile": self.profile.value,
            "fit_score": self.fit_score,
            "reason": self.reason,
        }


def _finite_values(frame: pl.DataFrame, column: str) -> list[float]:
    """提取指定列中的有限浮点值。"""
    if column not in frame.columns:
        return []
    values: list[float] = []
    for raw in frame[column].to_list():
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value == value and value not in (float("inf"), float("-inf")):
            values.append(value)
    return values


def _daily_returns(latest: pl.DataFrame) -> list[float]:
    """计算截面中可用标的的单日收益率。"""
    if not {"close", "_previous_close"}.issubset(latest.columns):
        return []
    values: list[float] = []
    for close, previous in latest.select("close", "_previous_close").iter_rows():
        try:
            close_value, previous_value = float(close), float(previous)
        except (TypeError, ValueError):
            continue
        if previous_value > 0:
            values.append(close_value / previous_value - 1)
    return values


def _external_metrics(
    overnight: Mapping[str, Any], news: Mapping[str, Any]
) -> tuple[float, float, float]:
    """规范化隔夜行情和新闻上下文指标。"""
    tilt = float(overnight.get("tilt") or 0.0) if overnight.get("available") else 0.0
    score = float(news.get("score") or 0.0) if news.get("available") else 0.0
    confidence = (
        min(max(float(news.get("confidence") or 0.0), 0.0), 1.0) if news.get("available") else 0.0
    )
    return tilt, score, confidence


def _market_score(
    advance_ratio: float | None,
    median_momentum: float | None,
    external: tuple[float, float, float],
) -> float:
    """合成市场宽度、动量和外部信息得分。"""
    tilt, news_score, news_confidence = external
    breadth_score = (advance_ratio * 2 - 1) if advance_ratio is not None else 0.0
    momentum_score = max(-1.0, min(1.0, (median_momentum or 0.0) / 0.08))
    return (
        0.50 * breadth_score
        + 0.25 * momentum_score
        + 0.15 * max(-1.0, min(1.0, tilt))
        + 0.10 * max(-1.0, min(1.0, news_score)) * news_confidence
    )


def _regime_state(score: float, volatility: float | None) -> MarketRegimeState:
    """根据综合得分和波动率确定有限市场状态。"""
    if volatility is not None and volatility >= 0.035 and abs(score) < 0.40:
        return MarketRegimeState.VOLATILE
    if score >= 0.20:
        return MarketRegimeState.RISK_ON
    if score <= -0.20:
        return MarketRegimeState.RISK_OFF
    return MarketRegimeState.BALANCED


def classify_market_regime(
    latest: pl.DataFrame,
    *,
    source_date: date | datetime | str | None = None,
    overnight_context: Mapping[str, Any] | None = None,
    news_context: Mapping[str, Any] | None = None,
) -> MarketRegime:
    """在不读取当日行情的前提下识别下一交易日市场状态。"""
    returns = _daily_returns(latest)
    advance_ratio = sum(value > 0 for value in returns) / len(returns) if returns else None
    momentums, volatilities = (
        _finite_values(latest, "_momentum"),
        _finite_values(latest, "_volatility_20d"),
    )
    median_momentum = median(momentums) if momentums else None
    median_volatility = median(volatilities) if volatilities else None
    external = _external_metrics(overnight_context or {}, news_context or {})
    score = _market_score(advance_ratio, median_momentum, external)
    return MarketRegime(
        state=_regime_state(score, median_volatility),
        source_date=str(source_date) if source_date is not None else None,
        score=round(score, 6),
        advance_ratio=round(advance_ratio, 6) if advance_ratio is not None else None,
        median_momentum_20d=round(median_momentum, 6) if median_momentum is not None else None,
        median_volatility_20d=(
            round(median_volatility, 6) if median_volatility is not None else None
        ),
        overnight_tilt=round(external[0], 6),
        news_score=round(external[1], 6),
        news_confidence=round(external[2], 6),
    )


def _strategy_profile(item: StrategyCatalogItem) -> StrategyProfile:
    """根据元数据关键词识别策略画像。"""
    text = " ".join(
        [
            str(item.get("id") or ""),
            str(item.get("name") or ""),
            str(item.get("description") or ""),
            *[str(tag) for tag in item.get("tags") or []],
        ]
    ).lower()
    if any(term in text for term in _DEFENSIVE_TERMS):
        return StrategyProfile.DEFENSIVE
    if any(term in text for term in _REVERSAL_TERMS):
        return StrategyProfile.REVERSAL
    if any(term in text for term in _MOMENTUM_TERMS):
        return StrategyProfile.MOMENTUM
    return StrategyProfile.BALANCED


def _regime_fit(state: MarketRegimeState, profile: StrategyProfile) -> float:
    """返回市场状态与策略画像的相对适配度。"""
    return _REGIME_FITS.get(state, _REGIME_FITS[MarketRegimeState.BALANCED]).get(profile, 1.0)


def _exclusion_reason(item: StrategyCatalogItem, promoted: set[str]) -> str | None:
    """返回策略不能进入专家策略池的原因。"""
    strategy_id, source = str(item.get("id") or ""), str(item.get("source") or "")
    assets = {str(value) for value in item.get("asset_types") or ["stock"]}
    timeframes = {str(value) for value in item.get("timeframes") or ["1d"]}
    if "stock" not in assets or "1d" not in timeframes:
        return "incompatible_contract"
    if source == StrategySource.BUILTIN or (
        source == StrategySource.AI and strategy_id in promoted
    ):
        return None
    return "outside_expert_pool"


def _eligible_candidates(
    catalog: Iterable[StrategyCatalogItem], regime: MarketRegime, promoted: set[str]
) -> tuple[list[StrategyCandidate], list[dict[str, str]]]:
    """划分可参与策略和被排除策略。"""
    candidates: list[StrategyCandidate] = []
    excluded: list[dict[str, str]] = []
    for item in catalog:
        reason = _exclusion_reason(item, promoted)
        if reason is not None:
            excluded.append({"strategy_id": str(item.get("id") or ""), "reason": reason})
            continue
        profile = _strategy_profile(item)
        candidates.append((item, profile, _regime_fit(regime.state, profile)))
    candidates.sort(key=lambda row: (-row[2], str(row[0].get("id") or "")))
    return candidates, excluded


def _selected_candidates(
    candidates: list[StrategyCandidate], max_active: int
) -> list[StrategyCandidate]:
    """按适配度和数量上限选择当日启用策略。"""
    if not candidates:
        return []
    limit = max(1, int(max_active))
    selected = [row for row in candidates if row[2] >= candidates[0][2] * 0.78][:limit]
    minimum = min(3, len(candidates), limit)
    return selected if len(selected) >= minimum else candidates[:minimum]


def _build_allocations(
    selected: list[StrategyCandidate], regime: MarketRegime
) -> list[StrategyAllocation]:
    """将选中策略的适配度归一化为组合权重。"""
    total_fit = sum(row[2] for row in selected) or 1.0
    return [
        StrategyAllocation(
            strategy_id=str(item.get("id") or ""),
            name=str(item.get("name") or item.get("id") or ""),
            source=StrategySource(str(item.get("source") or "builtin")),
            weight=round(fit / total_fit, 8),
            profile=profile,
            fit_score=round(fit, 6),
            reason=f"{MarketRegimeState(regime.state).value}:{profile.value}",
        )
        for item, profile, fit in selected
    ]


def plan_strategy_allocation(
    catalog: Iterable[StrategyCatalogItem],
    regime: MarketRegime,
    *,
    promoted_ai_strategy_ids: Iterable[str] = (),
    max_active: int = 8,
) -> tuple[list[StrategyAllocation], dict[str, Any]]:
    """从兼容内置策略和已晋级 AI 策略中生成当日分配。"""
    promoted = {str(value) for value in promoted_ai_strategy_ids}
    candidates, excluded = _eligible_candidates(catalog, regime, promoted)
    allocations = _build_allocations(_selected_candidates(candidates, max_active), regime)
    payload = {
        "regime": regime.as_dict(),
        "considered_count": len(candidates),
        "considered_strategy_ids": [str(row[0].get("id") or "") for row in candidates],
        "active_count": len(allocations),
        "excluded": excluded,
        "allocations": [allocation.as_dict() for allocation in allocations],
    }
    return allocations, payload


def _matched_symbols(result: Any) -> set[str]:
    """从单策略筛选结果中提取唯一股票代码。"""
    symbols: set[str] = set()
    for row in list(getattr(result, "rows", []) or []):
        symbol = str(row.get("symbol") or "")
        if symbol:
            symbols.add(symbol)
    return symbols


def weighted_consensus_scores(
    allocations: Iterable[StrategyAllocation], results: Mapping[str, Any]
) -> tuple[dict[str, float], dict[str, int]]:
    """计算成功策略的归一化加权投票和命中数。"""
    allocation_list = list(allocations)
    successful_weight = sum(
        allocation.weight for allocation in allocation_list if allocation.strategy_id in results
    )
    if successful_weight <= 0:
        return {}, {}
    votes: dict[str, float] = {}
    match_counts: dict[str, int] = {}
    for allocation in allocation_list:
        if allocation.strategy_id not in results:
            continue
        symbols = _matched_symbols(results[allocation.strategy_id])
        match_counts[allocation.strategy_id] = len(symbols)
        for symbol in symbols:
            votes[symbol] = votes.get(symbol, 0.0) + allocation.weight
    normalized = {symbol: round(weight / successful_weight, 8) for symbol, weight in votes.items()}
    return normalized, match_counts
