from __future__ import annotations

import threading
from datetime import date
from types import SimpleNamespace
from typing import ClassVar

import pytest

from app.backtest import optimizer as optimizer_module
from app.backtest.optimizer import OptimizeConfig
from app.paper_agent.models import StrategyParameterCandidate
from app.services.investment_expert import InvestmentExpertService
from app.strategy.ai_generator import AIStrategyGenerator, normalize_strategy_meta_fields


def _result(**stats: object) -> SimpleNamespace:
    """构造最小回测结果替身。"""
    return SimpleNamespace(error=None, stats=stats)


class _IsolationStrategyEngine:
    """提供批次隔离测试所需的内置策略定义。"""

    @staticmethod
    def get(strategy_id: str) -> SimpleNamespace:
        """返回带一个数值参数的内置策略。"""
        return SimpleNamespace(
            id=strategy_id,
            source="builtin",
            meta={
                "params": [
                    {
                        "id": "window",
                        "type": "int",
                        "default": 20,
                        "min": 5,
                        "max": 60,
                        "step": 5,
                    }
                ]
            },
        )


class _IsolationStore:
    """记录批次隔离测试中的参数版本与晋级结果。"""

    saved: ClassVar[list[str]] = []

    @staticmethod
    def active_strategy_parameters() -> dict[str, dict[str, object]]:
        """返回空的活动参数集。"""
        return {}

    @classmethod
    def save_strategy_parameter_version(
        cls, candidate: StrategyParameterCandidate
    ) -> dict[str, str]:
        """记录成功处理的策略并返回版本。"""
        cls.saved.append(candidate.strategy_id)
        return {"id": f"version-{candidate.strategy_id}"}

    @staticmethod
    def promote_strategy_parameters(
        version_id: str,
        *,
        reason: str,
        metrics: dict[str, object],
    ) -> None:
        """模拟参数晋级。"""


class _IsolationOptimizer:
    """让第一个策略失败、第二个策略成功。"""

    def __init__(self, service: object, strategy_engine: object) -> None:
        """忽略真实优化器依赖。"""

    @staticmethod
    def optimize(config: OptimizeConfig) -> dict[str, object]:
        """按策略标识返回失败或成功结果。"""
        if config.strategy_id == "broken_strategy":
            raise ValueError("broken strategy")
        return {
            "best_params": {"window": 30},
            "objective": "sortino",
            "best_score": 1.0,
            "n_combinations": 2,
        }


def test_generated_strategy_meta_is_owned_by_investment_expert() -> None:
    """生成策略必须覆盖为投资专家所有的元数据。"""
    code = """import polars as pl

META = {
    "id": "wrong_id",
    "name": "候选",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [],
    "scoring": {},
}

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    return pl.col("close") > pl.col("open")
"""

    normalized = normalize_strategy_meta_fields(
        code,
        {
            "id": "ai_expert_test",
            "expert_owned": True,
            "expert_regime": "balanced",
        },
    )
    validation = AIStrategyGenerator().validate_code(normalized)

    assert validation["valid"] is True
    assert validation["meta"]["id"] == "ai_expert_test"
    assert validation["meta"]["expert_owned"] is True


def test_generated_strategy_requires_positive_protected_evidence() -> None:
    """AI 策略只有保护集证据为正时才允许晋级。"""
    assert InvestmentExpertService._generated_strategy_gate(
        _result(n_trades=40, avg_pnl=10.0, total_return=0.08, max_drawdown=-0.10)
    ) == ("promoted", "protected_generated_strategy_passed")
    assert InvestmentExpertService._generated_strategy_gate(
        _result(n_trades=40, avg_pnl=-1.0, total_return=0.08, max_drawdown=-0.10)
    ) == ("rejected", "non_positive_protected_expectancy")


def test_builtin_parameter_candidate_must_beat_protected_baseline() -> None:
    """内置策略参数候选必须优于保护集基线。"""
    baseline = _result(n_trades=20, avg_pnl=5.0, total_return=0.04, max_drawdown=-0.08)
    improved = _result(n_trades=20, avg_pnl=6.0, total_return=0.05, max_drawdown=-0.08)
    regressed = _result(n_trades=20, avg_pnl=6.0, total_return=0.03, max_drawdown=-0.08)

    assert InvestmentExpertService._parameter_optimization_gate(baseline, improved) == (
        "promoted",
        "protected_strategy_optimization_passed",
    )
    assert InvestmentExpertService._parameter_optimization_gate(baseline, regressed) == (
        "rejected",
        "protected_return_regressed",
    )
    failed_baseline = SimpleNamespace(error="missing data", stats=None)
    assert InvestmentExpertService._parameter_optimization_gate(failed_baseline, improved) == (
        "rejected",
        "protected_baseline_backtest_failed",
    )


def test_generation_is_deferred_when_ai_provider_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未配置 AI 服务时生成任务必须明确延后。"""
    from app.services import ai_provider

    monkeypatch.setattr(ai_provider, "ai_configured", lambda: False)
    service = InvestmentExpertService.__new__(InvestmentExpertService)
    service.strategy_engine = object()

    assert service.submit_strategy_generation() == {
        "status": "deferred",
        "task": "strategy_generation",
        "reason": "ai_not_configured",
    }


def test_builtin_optimization_isolates_one_strategy_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单个内置策略优化失败时继续处理后续策略。"""
    _IsolationStore.saved.clear()
    monkeypatch.setattr(optimizer_module, "StrategyOptimizer", _IsolationOptimizer)
    service = InvestmentExpertService.__new__(InvestmentExpertService)
    service._operation_lock = threading.Lock()
    service.strategy_engine = _IsolationStrategyEngine()
    service.repo = SimpleNamespace(latest_daily_date=lambda: date(2026, 8, 25))
    service.store = _IsolationStore()
    service.constitution = SimpleNamespace(
        max_hold_trading_days=15,
        min_hold_trading_days=3,
        max_positions=5,
        max_exposure_pct=0.8,
        initial_capital=1_000_000,
    )
    service._strategy_backtest_service = lambda: object()
    service._run_strategy_backtest = lambda *_args, **_kwargs: _result(
        n_trades=20,
        avg_pnl=10,
        total_return=0.1,
        max_drawdown=-0.05,
    )
    service._parameter_optimization_gate = lambda *_args: ("promoted", "passed")

    result = service._run_strategy_optimization(["broken_strategy", "healthy_strategy"])

    assert result["status"] == "succeeded"
    assert [(row["strategy_id"], row["status"]) for row in result["results"]] == [
        ("broken_strategy", "failed"),
        ("healthy_strategy", "promoted"),
    ]
    assert _IsolationStore.saved == ["healthy_strategy"]
