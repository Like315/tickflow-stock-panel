from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.paper_agent.evolution import EvaluationMetrics, PolicyEvolutionEngine
from app.paper_agent.execution import StrictMinuteExecutor
from app.paper_agent.models import ExpertPolicy, MinuteBar, PositionLot, RiskConstitution
from app.paper_agent.runtime import InvestmentExpertRuntime, InvestmentExpertRuntimeConfig


def _bar(minute: int, close: float, amount: float, *, day: int = 18) -> MinuteBar:
    """构造指定交易日和分钟的完整分钟线。"""
    start = datetime(2026, 8, day, 9, minute, tzinfo=UTC)
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


def _late_day_bars(day: int = 18) -> list[MinuteBar]:
    """构造截至14:30且最后一个10分钟 MA5 刚拐头的分钟线。"""
    morning = datetime(2026, 8, day, 9, 31, tzinfo=UTC)
    afternoon = datetime(2026, 8, day, 13, 1, tzinfo=UTC)
    timestamps = [morning + timedelta(minutes=index) for index in range(120)] + [
        afternoon + timedelta(minutes=index) for index in range(90)
    ]
    chunk_closes = [10.0] * 14 + [10.0, 10.0, 10.0, 10.0, 9.8, 9.8, 10.4]
    result = []
    for index, timestamp in enumerate(timestamps):
        session_index = index if index < 120 else index - 120
        chunk_id = index // 10 if index < 120 else 12 + session_index // 10
        close = chunk_closes[chunk_id]
        open_price = 9.9 if index == 0 else close
        result.append(
            MinuteBar(
                symbol="A",
                datetime=timestamp,
                received_at=timestamp + timedelta(minutes=1, seconds=1),
                raw_open=open_price,
                raw_high=max(open_price, close) + 0.01,
                raw_low=min(open_price, close) - 0.01,
                raw_close=close,
                volume=100,
                amount=close * 10_000,
            )
        )
    return result


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
        InvestmentExpertRuntimeConfig(
            session_id="s1", policy=policy, candidates={"A"}, executor=executor
        )
    )

    first = runtime.on_bar(_bar(31, 10.0, 100_000))
    second = runtime.on_bar(_bar(32, 10.2, 102_000))

    assert first.decision["action"] == "abstain"  # type: ignore[index]
    assert second.decision["action"] == "buy"  # type: ignore[index]
    assert second.submitted_event is not None
    assert executor.total_shares("A") == 0

    third = runtime.on_bar(_bar(33, 10.3, 103_000))
    assert third.execution_events[0].event_type in {"order_filled", "order_partially_filled"}


def test_late_day_strategy_buys_after_1430_and_exits_next_morning() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    runtime = InvestmentExpertRuntime(
        InvestmentExpertRuntimeConfig(
            session_id="late-day-entry",
            policy=ExpertPolicy(id="late-day", version=1),
            candidates={"A"},
            executor=executor,
            candidate_context={
                "A": {
                    "previous_close": 10.0,
                    "intraday_change_rank": 1,
                    "strategy_ids": ["late_day_first_bullish_ma5_turn"],
                    "primary_strategy_id": "late_day_first_bullish_ma5_turn",
                    "strategy_params": {"late_day_first_bullish_ma5_turn": {}},
                }
            },
        )
    )

    last_step = None
    for bar in _late_day_bars():
        last_step = runtime.on_bar(bar)

    assert last_step is not None
    assert last_step.decision is not None
    assert last_step.decision["action"] == "buy"
    assert last_step.submitted_event is not None
    fill_time = datetime(2026, 8, 18, 14, 31, tzinfo=UTC)
    fill_step = runtime.on_bar(
        MinuteBar(
            symbol="A",
            datetime=fill_time,
            received_at=fill_time + timedelta(minutes=1, seconds=1),
            raw_open=10.41,
            raw_high=10.42,
            raw_low=10.40,
            raw_close=10.41,
            volume=100,
            amount=104_100,
        )
    )
    assert fill_step.execution_events[0].event_type in {"order_filled", "order_partially_filled"}
    assert executor.lots[0].strategy_id == "late_day_first_bullish_ma5_turn"

    next_runtime = InvestmentExpertRuntime(
        InvestmentExpertRuntimeConfig(
            session_id="late-day-exit",
            policy=ExpertPolicy(id="late-day", version=1),
            candidates=set(),
            executor=executor,
        )
    )
    exit_signal = next_runtime.on_bar(_bar(31, 10.8, 108_000, day=19))
    assert exit_signal.decision is not None
    assert exit_signal.decision["action"] == "sell"
    assert exit_signal.decision["reason"] == "late_day_next_morning_take_profit"
    exit_fill = next_runtime.on_bar(_bar(32, 10.79, 107_900, day=19))
    assert exit_fill.execution_events[0].event_type in {"order_filled", "order_partially_filled"}


def test_late_day_strategy_forces_exit_on_second_trading_day() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    executor.lots.append(
        PositionLot(
            lot_id="late-day-max-hold",
            symbol="A",
            acquired_date=date(2026, 8, 18),
            shares=100,
            remaining_shares=100,
            entry_price=10,
            entry_cost=5,
            strategy_id="late_day_first_bullish_ma5_turn",
        )
    )
    runtime = InvestmentExpertRuntime(
        InvestmentExpertRuntimeConfig(
            session_id="late-day-max-hold",
            policy=ExpertPolicy(id="late-day", version=1),
            candidates=set(),
            executor=executor,
        )
    )

    step = runtime.on_bar(_bar(31, 10.0, 100_000, day=20))

    assert step.decision is not None
    assert step.decision["action"] == "sell"
    assert step.decision["reason"] == "late_day_max_hold"
    assert step.submitted_event is not None


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
        InvestmentExpertRuntimeConfig(
            session_id="positive-module",
            policy=policy,
            candidates={"A"},
            executor=StrictMinuteExecutor(RiskConstitution(slippage_bps=0)),
            candidate_context={"A": {"overnight_us_factor": 1.0}},
        )
    )
    negative = InvestmentExpertRuntime(
        InvestmentExpertRuntimeConfig(
            session_id="negative-module",
            policy=policy,
            candidates={"A"},
            executor=StrictMinuteExecutor(RiskConstitution(slippage_bps=0)),
            candidate_context={"A": {"overnight_us_factor": -1.0}},
        )
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
        InvestmentExpertRuntimeConfig(
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
        executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0, min_hold_trading_days=1))
        executor.lots.append(
            PositionLot(
                lot_id=f"lot-{factor}",
                symbol="A",
                acquired_date=date(2026, 8, 17),
                shares=100,
                remaining_shares=100,
                entry_price=10,
                entry_cost=5,
            )
        )
        return InvestmentExpertRuntime(
            InvestmentExpertRuntimeConfig(
                session_id=f"module-exit-{factor}",
                policy=policy,
                candidates={"A"},
                executor=executor,
                candidate_context={"A": {"overnight_us_factor": factor}},
            )
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
    executor.lots.append(
        PositionLot(
            lot_id="hard-stop",
            symbol="A",
            acquired_date=date(2026, 8, 17),
            shares=100,
            remaining_shares=100,
            entry_price=10,
            entry_cost=5,
        )
    )
    runtime = InvestmentExpertRuntime(
        InvestmentExpertRuntimeConfig(
            session_id="hard-stop",
            policy=ExpertPolicy(id="hard-stop", version=1),
            candidates={"A"},
            executor=executor,
            candidate_context={"A": {"overnight_us_factor": 1.0}},
        )
    )

    step = runtime.on_bar(_bar(31, 9.4, 94_000))

    assert step.decision is not None
    assert step.decision["action"] == "sell"
    assert step.decision["reason"] == "settled_position_stop_loss"


def _minimum_hold_runtime() -> InvestmentExpertRuntime:
    """构造持有一手股票的最短持有期测试运行时。"""
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    executor.lots.append(
        PositionLot(
            lot_id="minimum-hold",
            symbol="A",
            acquired_date=date(2026, 8, 17),
            shares=100,
            remaining_shares=100,
            entry_price=10,
            entry_cost=5,
        )
    )
    return InvestmentExpertRuntime(
        InvestmentExpertRuntimeConfig(
            session_id="minimum-hold",
            policy=ExpertPolicy(id="minimum-hold", version=1),
            candidates={"A"},
            executor=executor,
        )
    )


def test_runtime_blocks_soft_exit_until_three_trading_days() -> None:
    """普通止盈和信号退出必须等待三个 A 股交易日。"""

    early = _minimum_hold_runtime().on_bar(_bar(31, 10.9, 109_000, day=18))
    eligible_runtime = _minimum_hold_runtime()
    eligible = eligible_runtime.on_bar(_bar(31, 10.9, 109_000, day=20))

    assert early.decision is not None
    assert early.decision["action"] == "hold"
    assert early.decision["reason"] == "position_min_hold_not_reached"
    assert early.decision["features"]["oldest_hold_trading_days"] == 1
    assert eligible.decision is not None
    assert eligible.decision["action"] == "sell"
    assert eligible.decision["reason"] == "settled_position_take_profit"
    assert eligible.submitted_event is not None
    assert eligible.submitted_event.order_id is not None
    assert (
        eligible_runtime.executor.pending[eligible.submitted_event.order_id].remaining_shares == 100
    )


def _runtime_with_mixed_age_lots() -> tuple[InvestmentExpertRuntime, StrictMinuteExecutor]:
    """构造同时包含到期和未到期批次的测试运行时。"""
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    executor.lots.extend(
        [
            PositionLot(
                lot_id="expired",
                symbol="A",
                acquired_date=date(2026, 8, 3),
                shares=100,
                remaining_shares=100,
                entry_price=10,
                entry_cost=5,
            ),
            PositionLot(
                lot_id="young",
                symbol="A",
                acquired_date=date(2026, 8, 20),
                shares=100,
                remaining_shares=100,
                entry_price=10,
                entry_cost=5,
            ),
        ]
    )
    runtime = InvestmentExpertRuntime(
        InvestmentExpertRuntimeConfig(
            session_id="maximum-hold",
            policy=ExpertPolicy(id="maximum-hold", version=1),
            candidates={"A"},
            executor=executor,
        )
    )
    return runtime, executor


def test_runtime_max_hold_sells_only_expired_lots_after_fifteen_trading_days() -> None:
    """持有满十五个交易日时只能卖出已经到期的批次。"""
    runtime, executor = _runtime_with_mixed_age_lots()

    step = runtime.on_bar(_bar(31, 10.0, 100_000, day=24))

    assert step.decision is not None
    assert step.decision["action"] == "sell"
    assert step.decision["reason"] == "settled_position_max_hold"
    assert step.decision["features"]["max_hold_expired_shares"] == 100
    assert step.submitted_event is not None
    assert step.submitted_event.order_id is not None
    assert executor.pending[step.submitted_event.order_id].remaining_shares == 100


def _news_factor_runtime(factor: float) -> InvestmentExpertRuntime:
    """构造指定新闻因子的入场确认测试运行时。"""
    policy = ExpertPolicy(
        id="news-factor",
        version=1,
        min_completed_bars=2,
        min_vwap_bias=0.001,
        min_breakout_pct=0.001,
        news_candidate_weight=0.25,
    )
    return InvestmentExpertRuntime(
        InvestmentExpertRuntimeConfig(
            session_id=f"news-{factor}",
            policy=policy,
            candidates={"A"},
            executor=StrictMinuteExecutor(RiskConstitution(slippage_bps=0)),
            candidate_context={"A": {"news_factor_score": factor}},
        )
    )


def test_news_factor_softens_but_does_not_replace_price_confirmation() -> None:
    """新闻因子只能软化阈值，不能替代价格确认。"""
    positive = _news_factor_runtime(1.0)
    negative = _news_factor_runtime(-1.0)
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
        key
        for key in PolicyEvolutionEngine.MUTATION_FIELDS
        if getattr(champion, key) != getattr(candidate, key)
    }
    assert field == "min_vwap_bias"
    assert changed == {"min_vwap_bias"}


def test_carryover_position_can_exit_when_symbol_is_not_a_new_candidate() -> None:
    executor = StrictMinuteExecutor(RiskConstitution(slippage_bps=0))
    executor.lots.append(
        PositionLot(
            lot_id="old",
            symbol="A",
            acquired_date=date(2026, 8, 17),
            shares=100,
            remaining_shares=100,
            entry_price=12,
            entry_cost=5,
        )
    )
    runtime = InvestmentExpertRuntime(
        InvestmentExpertRuntimeConfig(
            session_id="s2",
            policy=ExpertPolicy(id="p2", version=2),
            candidates=set(),
            executor=executor,
        )
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
