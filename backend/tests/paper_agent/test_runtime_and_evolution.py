from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.paper_agent.evolution import EvaluationMetrics, PolicyEvolutionEngine
from app.paper_agent.execution import StrictMinuteExecutor
from app.paper_agent.models import ExpertPolicy, MinuteBar, PositionLot, RiskConstitution
from app.paper_agent.runtime import InvestmentExpertRuntime


def _bar(minute: int, close: float, amount: float) -> MinuteBar:
    start = datetime(2026, 8, 18, 9, minute, tzinfo=UTC)
    return MinuteBar(
        symbol="A",
        datetime=start,
        received_at=start + timedelta(minutes=1, seconds=1),
        raw_open=close - 0.01,
        raw_high=close + 0.01,
        raw_low=close - 0.02,
        raw_close=close,
        volume=100,
        amount=amount,
    )


def test_runtime_submits_only_after_completed_bar_confirmation() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    policy = ExpertPolicy(
        id="p1",
        version=1,
        min_completed_bars=2,
        min_vwap_bias=0,
        min_breakout_pct=0,
    )
    runtime = InvestmentExpertRuntime(
        session_id="s1", policy=policy, candidates={"A"}, executor=executor
    )

    first = runtime.on_bar(_bar(31, 10.0, 100_000))
    second = runtime.on_bar(_bar(32, 10.2, 102_000))

    assert first.decision["action"] == "abstain"  # type: ignore[index]
    assert second.decision["action"] == "buy"  # type: ignore[index]
    assert second.submitted_event is not None
    assert executor.total_shares("A") == 0

    third = runtime.on_bar(_bar(33, 10.3, 103_000))
    assert third.execution_events[0].event_type in {"order_filled", "order_partially_filled"}


def test_runtime_applies_overnight_module_factor_to_entry_confirmation() -> None:
    policy = ExpertPolicy(
        id="module-entry",
        version=1,
        min_completed_bars=2,
        min_vwap_bias=0.001,
        min_breakout_pct=0.001,
        overnight_us_entry_weight=0.10,
    )
    positive = InvestmentExpertRuntime(
        session_id="positive-module",
        policy=policy,
        candidates={"A"},
        executor=StrictMinuteExecutor(RiskConstitution(slippage_bps=0)),
        candidate_context={"A": {"overnight_us_factor": 1.0}},
    )
    negative = InvestmentExpertRuntime(
        session_id="negative-module",
        policy=policy,
        candidates={"A"},
        executor=StrictMinuteExecutor(RiskConstitution(slippage_bps=0)),
        candidate_context={"A": {"overnight_us_factor": -1.0}},
    )

    positive.on_bar(_bar(31, 10.0, 100_000))
    positive_step = positive.on_bar(_bar(32, 10.016, 100_160))
    negative.on_bar(_bar(31, 10.0, 100_000))
    negative_step = negative.on_bar(_bar(32, 10.016, 100_160))

    assert positive_step.decision is not None
    assert positive_step.decision["action"] == "buy"
    assert negative_step.decision is not None
    assert negative_step.decision["action"] == "abstain"
    assert negative_step.decision["reason"] == "vwap_confirmation_missing"


def test_runtime_treats_unavailable_overnight_module_data_as_neutral() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    policy = ExpertPolicy(
        id="missing-us-data",
        version=1,
        min_completed_bars=2,
        min_vwap_bias=0,
        min_breakout_pct=0,
    )
    runtime = InvestmentExpertRuntime(
        session_id="missing-us-data-session",
        policy=policy,
        candidates={"A"},
        executor=executor,
        candidate_context={
            "A": {
                "overnight_us_available": False,
                "overnight_us_score": -1.0,
                "overnight_us_factor": 0.0,
            }
        },
    )

    runtime.on_bar(_bar(31, 10.0, 100_000))
    second = runtime.on_bar(_bar(32, 10.2, 102_000))

    assert second.decision is not None
    assert second.decision["action"] == "buy"
    assert second.submitted_event is not None


def test_runtime_applies_inverse_overnight_module_factor_to_soft_exit() -> None:
    policy = ExpertPolicy(
        id="module-exit",
        version=1,
        exit_vwap_bias=-0.0005,
        overnight_us_exit_weight=0.08,
    )

    def runtime(factor: float) -> InvestmentExpertRuntime:
        executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
        executor.lots.append(PositionLot(
            lot_id=f"lot-{factor}",
            symbol="A",
            acquired_date=date(2026, 8, 17),
            shares=100,
            remaining_shares=100,
            entry_price=10,
            entry_cost=5,
        ))
        return InvestmentExpertRuntime(
            session_id=f"module-exit-{factor}",
            policy=policy,
            candidates={"A"},
            executor=executor,
            candidate_context={"A": {"overnight_us_factor": factor}},
        )

    weak_module_step = runtime(-1.0).on_bar(_bar(31, 10.0, 100_000))
    strong_module_step = runtime(1.0).on_bar(_bar(31, 10.0, 100_000))

    assert weak_module_step.decision is not None
    assert weak_module_step.decision["action"] == "sell"
    assert weak_module_step.decision["reason"] == "settled_position_vwap_breakdown"
    assert strong_module_step.decision is not None
    assert strong_module_step.decision["action"] == "hold"


def test_positive_overnight_module_factor_never_weakens_hard_stop_loss() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    executor.lots.append(PositionLot(
        lot_id="hard-stop",
        symbol="A",
        acquired_date=date(2026, 8, 17),
        shares=100,
        remaining_shares=100,
        entry_price=10,
        entry_cost=5,
    ))
    runtime = InvestmentExpertRuntime(
        session_id="hard-stop",
        policy=ExpertPolicy(id="hard-stop", version=1),
        candidates={"A"},
        executor=executor,
        candidate_context={"A": {"overnight_us_factor": 1.0}},
    )

    step = runtime.on_bar(_bar(31, 9.4, 94_000))

    assert step.decision is not None
    assert step.decision["action"] == "sell"
    assert step.decision["reason"] == "settled_position_stop_loss"


def test_news_factor_softens_but_does_not_replace_price_confirmation() -> None:
    policy = ExpertPolicy(
        id="news-factor",
        version=1,
        min_completed_bars=2,
        min_vwap_bias=0.001,
        min_breakout_pct=0.001,
        news_candidate_weight=0.25,
    )
    positive_executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    positive = InvestmentExpertRuntime(
        session_id="positive-news",
        policy=policy,
        candidates={"A"},
        executor=positive_executor,
        candidate_context={"A": {"news_factor_score": 1.0}},
    )
    negative_executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    negative = InvestmentExpertRuntime(
        session_id="negative-news",
        policy=policy,
        candidates={"A"},
        executor=negative_executor,
        candidate_context={"A": {"news_factor_score": -1.0}},
    )

    positive.on_bar(_bar(31, 10.0, 100_000))
    positive_step = positive.on_bar(_bar(32, 10.01, 100_100))
    negative.on_bar(_bar(31, 10.0, 100_000))
    negative_step = negative.on_bar(_bar(32, 10.01, 100_100))

    assert positive_step.decision is not None
    assert positive_step.decision["action"] == "buy"
    assert negative_step.decision is not None
    assert negative_step.decision["action"] == "abstain"
    assert negative_step.decision["reason"] == "vwap_confirmation_missing"


def test_evolution_changes_exactly_one_policy_dimension() -> None:
    champion = ExpertPolicy(id="p1", version=1)
    candidate, field = PolicyEvolutionEngine().propose(champion, {"loss_rate": 0.8})

    changed = {
        key for key in PolicyEvolutionEngine.MUTATION_FIELDS
        if getattr(champion, key) != getattr(candidate, key)
    }
    assert field == "min_vwap_bias"
    assert changed == {"min_vwap_bias"}


def test_carryover_position_can_exit_when_symbol_is_not_a_new_candidate() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    executor.lots.append(PositionLot(
        lot_id="old",
        symbol="A",
        acquired_date=date(2026, 8, 17),
        shares=100,
        remaining_shares=100,
        entry_price=12,
        entry_cost=5,
    ))
    runtime = InvestmentExpertRuntime(
        session_id="s2",
        policy=ExpertPolicy(id="p2", version=2),
        candidates=set(),
        executor=executor,
    )

    step = runtime.on_bar(_bar(31, 10.0, 100_000))

    assert step.decision is not None
    assert step.decision["action"] == "sell"
    assert step.submitted_event is not None


def test_ratchet_requires_evidence_and_never_accepts_constraint_violation() -> None:
    base = EvaluationMetrics(0.05, -0.08, 40, 0.5, 0.001, 0, 200)
    unsafe = EvaluationMetrics(0.20, -0.04, 60, 0.7, 0.003, 1, 200)
    sparse = EvaluationMetrics(0.06, -0.07, 5, 0.6, 0.002, 0, 200)
    better = EvaluationMetrics(0.07, -0.07, 45, 0.6, 0.002, 0, 200)

    assert PolicyEvolutionEngine.gate(base, unsafe)[0] == "rejected"
    assert PolicyEvolutionEngine.gate(base, sparse) == (
        "inconclusive",
        "insufficient_closed_trades",
    )
    assert PolicyEvolutionEngine.gate(base, better)[0] == "promoted"
