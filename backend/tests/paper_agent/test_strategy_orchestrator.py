from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest

from app.paper_agent.strategy_orchestrator import (
    MarketRegime,
    MarketRegimeState,
    StrategyAllocation,
    StrategyProfile,
    StrategySource,
    classify_market_regime,
    matched_strategy_ids_by_symbol,
    plan_strategy_allocation,
    weighted_consensus_scores,
)

# 覆盖内置、专家 AI、普通 AI 和不兼容资产的策略目录。
_FULL_CATALOG: list[dict[str, object]] = [
    {
        "id": "trend_breakout",
        "name": "趋势突破",
        "source": "builtin",
        "asset_types": ["stock"],
        "timeframes": ["1d"],
        "tags": ["趋势"],
    },
    {
        "id": "oversold_bounce",
        "name": "超卖反弹",
        "source": "builtin",
        "asset_types": ["stock"],
        "timeframes": ["1d"],
        "tags": ["超卖"],
    },
    {
        "id": "low_volatility_leader",
        "name": "低波龙头",
        "source": "builtin",
        "asset_types": ["stock"],
        "timeframes": ["1d"],
        "tags": ["低波"],
    },
    {
        "id": "ai_expert_promoted",
        "name": "专家策略",
        "source": "ai",
        "asset_types": ["stock"],
        "timeframes": ["1d"],
    },
    {
        "id": "ai_user_strategy",
        "name": "普通 AI 策略",
        "source": "ai",
        "asset_types": ["stock"],
        "timeframes": ["1d"],
    },
    {
        "id": "etf_only",
        "name": "ETF",
        "source": "builtin",
        "asset_types": ["etf"],
        "timeframes": ["1d"],
    },
]

# 用于验证不同市场状态排序变化的最小策略目录。
_PROFILE_CATALOG: list[dict[str, object]] = _FULL_CATALOG[:3]


def _regime(state: MarketRegimeState) -> MarketRegime:
    """构造指定状态的最小市场快照。"""
    return MarketRegime(
        state=state,
        source_date="2026-08-25",
        score=0.5 if state == MarketRegimeState.RISK_ON else -0.5,
        advance_ratio=0.75 if state == MarketRegimeState.RISK_ON else 0.25,
        median_momentum_20d=0.08 if state == MarketRegimeState.RISK_ON else -0.08,
        median_volatility_20d=0.02,
        overnight_tilt=0.0,
        news_score=0.0,
        news_confidence=0.0,
    )


def test_regime_uses_only_supplied_point_in_time_snapshot() -> None:
    """市场状态只能使用显式传入的时点快照。"""
    latest = pl.DataFrame(
        {
            "close": [11.0, 10.5, 9.0],
            "_previous_close": [10.0, 10.0, 10.0],
            "_momentum": [0.20, 0.10, -0.02],
            "_volatility_20d": [0.02, 0.025, 0.03],
        }
    )

    regime = classify_market_regime(
        latest,
        source_date="2026-08-25",
        overnight_context={"available": True, "tilt": 0.8},
        news_context={"available": True, "score": 0.4, "confidence": 0.5},
    )

    assert regime.state == "risk_on"
    assert regime.source_date == "2026-08-25"
    assert regime.advance_ratio == pytest.approx(2 / 3, abs=1e-6)
    assert regime.overnight_tilt == 0.8


def test_all_compatible_builtins_are_considered_and_only_promoted_expert_ai_is_eligible() -> None:
    """仅兼容内置策略和已晋级专家 AI 策略可以参与编排。"""
    allocations, payload = plan_strategy_allocation(
        _FULL_CATALOG,
        _regime(MarketRegimeState.RISK_ON),
        promoted_ai_strategy_ids={"ai_expert_promoted"},
        max_active=10,
    )

    ids = {item.strategy_id for item in allocations}
    assert payload["considered_count"] == 4
    assert set(payload["considered_strategy_ids"]) == {
        "trend_breakout",
        "oversold_bounce",
        "low_volatility_leader",
        "ai_expert_promoted",
    }
    assert "trend_breakout" in ids
    assert "ai_user_strategy" not in ids
    assert "etf_only" not in ids


def test_regime_changes_active_strategy_priority() -> None:
    """不同市场状态必须改变活动策略优先级。"""
    risk_on, _ = plan_strategy_allocation(
        _PROFILE_CATALOG, _regime(MarketRegimeState.RISK_ON), max_active=3
    )
    risk_off, _ = plan_strategy_allocation(
        _PROFILE_CATALOG, _regime(MarketRegimeState.RISK_OFF), max_active=3
    )

    assert risk_on[0].strategy_id == "trend_breakout"
    assert risk_off[0].strategy_id == "low_volatility_leader"


def test_weighted_consensus_renormalizes_around_isolated_failures() -> None:
    """单策略失败时共识权重必须按成功策略重新归一化。"""
    allocations = [
        StrategyAllocation(
            "a", "A", StrategySource.BUILTIN, 0.6, StrategyProfile.MOMENTUM, 1.0, "risk_on"
        ),
        StrategyAllocation(
            "b", "B", StrategySource.BUILTIN, 0.3, StrategyProfile.MOMENTUM, 1.0, "risk_on"
        ),
        StrategyAllocation(
            "failed", "F", StrategySource.BUILTIN, 0.1, StrategyProfile.BALANCED, 1.0, "risk_on"
        ),
    ]
    results = {
        "a": SimpleNamespace(rows=[{"symbol": "SH.600000"}]),
        "b": SimpleNamespace(rows=[{"symbol": "SH.600000"}, {"symbol": "SZ.000001"}]),
    }

    scores, counts = weighted_consensus_scores(allocations, results)

    assert scores["SH.600000"] == 1.0
    assert scores["SZ.000001"] == round(0.3 / 0.9, 8)
    assert counts == {"a": 1, "b": 2}


def test_strategy_matches_are_preserved_per_symbol_for_intraday_execution() -> None:
    """分钟运行时必须能区分每只股票由哪些策略选中。"""
    results = {
        "late_day": SimpleNamespace(rows=[{"symbol": "SH.600000"}]),
        "trend": SimpleNamespace(rows=[{"symbol": "SH.600000"}, {"symbol": "SZ.000001"}]),
    }

    assert matched_strategy_ids_by_symbol(results) == {
        "SH.600000": ["late_day", "trend"],
        "SZ.000001": ["trend"],
    }
