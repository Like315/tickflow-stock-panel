from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.paper_agent.exceptions import PaperAgentSchemaMigrationError
from app.paper_agent.models import ExpertStrategyRecord, StrategyParameterCandidate
from app.paper_agent.store import PaperAgentStore


def _replace_with_v2_event_table(store: PaperAgentStore, version_id: str) -> None:
    """把当前事件表替换为带一条晋级记录的 v2 结构。"""
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'")
        conn.execute("DROP TABLE expert_strategy_parameter_events")
        conn.executescript("""
            CREATE TABLE expert_strategy_parameter_events (
                id TEXT PRIMARY KEY,
                parameter_version_id TEXT NOT NULL
                    REFERENCES expert_strategy_parameter_versions(id) ON DELETE RESTRICT,
                previous_parameter_version_id TEXT
                    REFERENCES expert_strategy_parameter_versions(id) ON DELETE RESTRICT,
                decision TEXT NOT NULL CHECK(decision IN ('promote', 'rollback')),
                reason TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """)
        conn.execute(
            """
            INSERT INTO expert_strategy_parameter_events(
                id, parameter_version_id, previous_parameter_version_id,
                decision, reason, metrics_json, created_at
            ) VALUES ('legacy-promotion', ?, NULL, 'promote', 'passed', '{}', '2026-08-26')
            """,
            (version_id,),
        )


def _assert_v3_event_schema(store: PaperAgentStore) -> None:
    """断言参数事件表已经安全升级到 v3。"""
    with sqlite3.connect(store.path) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        columns = {
            row[1]: row
            for row in conn.execute(
                "PRAGMA table_info(expert_strategy_parameter_events)"
            ).fetchall()
        }
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert version == "3"
    assert columns["strategy_id"][3] == 1
    assert columns["parameter_version_id"][3] == 0
    assert violations == []


def _promote_parameter_candidate(
    store: PaperAgentStore,
    breakout_days: int,
    expected_return: float,
) -> dict[str, Any]:
    """保存并晋级一个测试参数候选。"""
    candidate = store.save_strategy_parameter_version(
        StrategyParameterCandidate(
            strategy_id="trend_breakout",
            params={"breakout_days": breakout_days},
            metrics={"return": expected_return},
            status="promoted",
            reason="passed",
        )
    )
    store.promote_strategy_parameters(candidate["id"], reason="passed", metrics={})
    return candidate


def test_orchestration_and_expert_strategy_state_are_auditable(tmp_path: Path) -> None:
    """策略编排和 AI 策略评估必须可持久化审计。"""
    store = PaperAgentStore(tmp_path)
    policy = store.ensure_baseline_policy()
    session = store.start_session(
        date(2026, 8, 26),
        policy.id,
        mode="paper",
        candidates=["SH.600000"],
    )
    snapshot = {
        "regime": {"state": "risk_on", "source_date": "2026-08-25"},
        "allocations": [{"strategy_id": "trend_breakout", "weight": 1.0}],
    }

    store.save_strategy_orchestration(session["id"], date(2026, 8, 26), snapshot)
    store.record_expert_strategy(
        ExpertStrategyRecord(
            strategy_id="ai_expert_test",
            regime="risk_on",
            status="shadow",
            metrics={},
            reason="awaiting_protected_backtest",
        )
    )
    store.finish_expert_strategy_evaluation(
        "ai_expert_test",
        status="promoted",
        metrics={"total_return": 0.12},
        reason="protected_generated_strategy_passed",
    )

    assert store.latest_strategy_orchestration()["payload"] == snapshot
    assert store.promoted_expert_strategy_ids() == {"ai_expert_test"}
    assert store.list_expert_strategies()[0]["metrics"] == {"total_return": 0.12}


def test_rejected_parameter_candidate_does_not_replace_promoted_version(tmp_path: Path) -> None:
    """被拒绝的参数候选不能替换已晋级版本。"""
    store = PaperAgentStore(tmp_path)
    promoted = store.save_strategy_parameter_version(
        StrategyParameterCandidate(
            strategy_id="trend_breakout",
            params={"breakout_days": 20},
            metrics={"protected": {"total_return": 0.10}},
            status="promoted",
            reason="protected_strategy_optimization_passed",
        )
    )
    store.promote_strategy_parameters(
        promoted["id"],
        reason="protected_strategy_optimization_passed",
        metrics=promoted["metrics"],
    )
    store.save_strategy_parameter_version(
        StrategyParameterCandidate(
            strategy_id="trend_breakout",
            params={"breakout_days": 30},
            metrics={"protected": {"total_return": -0.05}},
            status="rejected",
            reason="protected_return_regressed",
        )
    )

    active = store.active_strategy_parameters()

    assert active["trend_breakout"]["version_id"] == promoted["id"]
    assert active["trend_breakout"]["params"] == {"breakout_days": 20}
    experiments = store.list_strategy_parameter_versions()
    assert [row["status"] for row in experiments] == ["rejected", "promoted"]
    assert experiments[0]["reason"] == "protected_return_regressed"


def test_schema_v1_is_migrated_without_losing_existing_state(tmp_path: Path) -> None:
    """v1 数据库升级时必须保留既有策略状态。"""
    store = PaperAgentStore(tmp_path)
    policy = store.ensure_baseline_policy()
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'")
        conn.execute("DROP TABLE expert_strategy_versions")

    migrated = PaperAgentStore(tmp_path)

    assert migrated.get_champion().id == policy.id
    with sqlite3.connect(migrated.path) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'expert_strategy_versions'"
        ).fetchone()
    assert version == "3"
    assert table is not None


def test_schema_v2_parameter_events_are_rebuilt_and_backfilled(tmp_path: Path) -> None:
    """v2 参数事件升级时必须回填策略标识并保持外键完整。"""
    store = PaperAgentStore(tmp_path)
    parameter_version = store.save_strategy_parameter_version(
        StrategyParameterCandidate(
            strategy_id="trend_breakout",
            params={"breakout_days": 20},
            metrics={},
            status="promoted",
            reason="passed",
        )
    )
    _replace_with_v2_event_table(store, parameter_version["id"])

    migrated = PaperAgentStore(tmp_path)

    assert migrated.active_strategy_parameters()["trend_breakout"]["params"] == {
        "breakout_days": 20
    }
    rollback = migrated.rollback_last_strategy_parameter_promotion(
        reason="risk",
        metrics={},
    )
    assert rollback is not None
    assert rollback["parameter_version_id"] is None
    assert migrated.active_strategy_parameters() == {}
    _assert_v3_event_schema(migrated)


def test_schema_v2_migration_rejects_orphan_events_without_mutation(tmp_path: Path) -> None:
    """旧事件无法关联参数版本时保留原表并拒绝升级。"""
    store = PaperAgentStore(tmp_path)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'")
        conn.execute("DROP TABLE expert_strategy_parameter_events")
        conn.executescript("""
            CREATE TABLE expert_strategy_parameter_events (
                id TEXT PRIMARY KEY,
                parameter_version_id TEXT NOT NULL,
                previous_parameter_version_id TEXT,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO expert_strategy_parameter_events VALUES (
                'orphan', 'missing-version', NULL, 'promote', 'legacy', '{}', '2026-08-26'
            );
            """)

    with pytest.raises(PaperAgentSchemaMigrationError):
        PaperAgentStore(tmp_path)

    with sqlite3.connect(store.path) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(expert_strategy_parameter_events)"
            ).fetchall()
        }
        orphan_count = conn.execute(
            "SELECT count(*) FROM expert_strategy_parameter_events WHERE id = 'orphan'"
        ).fetchone()[0]
    assert version == "2"
    assert "strategy_id" not in columns
    assert orphan_count == 1


def test_risk_rollback_restores_previous_parameters_and_disables_expert(tmp_path: Path) -> None:
    """风险触发时必须回滚参数并停用最近的 AI 策略。"""
    store = PaperAgentStore(tmp_path)
    first = _promote_parameter_candidate(store, 20, 0.03)
    _promote_parameter_candidate(store, 25, 0.05)
    store.record_expert_strategy(
        ExpertStrategyRecord(
            strategy_id="ai_expert_risk_test",
            regime="risk_on",
            status="promoted",
            metrics={"return": 0.08},
            reason="passed",
        )
    )

    event = store.rollback_last_strategy_parameter_promotion(
        reason="paper_runtime_drawdown",
        metrics={"drawdown": -0.16},
    )
    strategy_id = store.rollback_latest_expert_strategy(
        reason="paper_runtime_drawdown",
        metrics={"drawdown": -0.16},
    )

    assert event["parameter_version_id"] == first["id"]
    assert store.active_strategy_parameters()["trend_breakout"]["params"] == {"breakout_days": 20}
    assert strategy_id == "ai_expert_risk_test"
    assert store.promoted_expert_strategy_ids() == set()
    assert store.list_expert_strategies()[0]["reason"] == "paper_runtime_drawdown"


def test_parameter_rollback_to_defaults_does_not_reactivate_old_version(tmp_path: Path) -> None:
    """回滚到默认参数后不能重新激活更早的参数版本。"""
    store = PaperAgentStore(tmp_path)
    first = store.save_strategy_parameter_version(
        StrategyParameterCandidate(
            strategy_id="trend_breakout",
            params={"breakout_days": 20},
            metrics={},
            status="promoted",
            reason="passed",
        )
    )
    store.promote_strategy_parameters(first["id"], reason="passed", metrics={})
    store.rollback_last_strategy_parameter_promotion(reason="risk", metrics={})
    assert store.active_strategy_parameters() == {}

    second = store.save_strategy_parameter_version(
        StrategyParameterCandidate(
            strategy_id="trend_breakout",
            params={"breakout_days": 25},
            metrics={},
            status="promoted",
            reason="passed",
        )
    )
    store.promote_strategy_parameters(second["id"], reason="passed", metrics={})
    event = store.rollback_last_strategy_parameter_promotion(reason="risk", metrics={})

    assert event["parameter_version_id"] is None
    assert store.active_strategy_parameters() == {}
