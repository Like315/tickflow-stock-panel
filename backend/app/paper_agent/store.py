"""SQLite control plane and append-only audit ledger for the paper agent."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.paper_agent.exceptions import PaperAgentSchemaMigrationError
from app.paper_agent.models import (
    ExecutionEvent,
    ExpertPolicy,
    ExpertStrategyRecord,
    StrategyParameterCandidate,
    StrategyParameterEventRecord,
    TrainedDecisionModel,
)

# 当前投资专家数据库结构版本。
_SCHEMA_VERSION: int = 3


class PaperAgentStore:
    """投资专家策略、会话、执行事件与实验结果存储。"""

    def __init__(self, data_dir: Path) -> None:
        """初始化用户数据库路径并执行兼容迁移。"""
        self.path = data_dir / "user_data" / "investment_expert_agent.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._execution_statistics_cache: dict[str, Any] | None = None
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """创建启用外键、WAL 和忙等待的 SQLite 连接。"""
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @staticmethod
    def _strategy_parameter_event_columns(
        conn: sqlite3.Connection,
    ) -> dict[str, sqlite3.Row]:
        """返回策略参数事件表的字段元数据。"""
        return {
            str(row["name"]): row
            for row in conn.execute(
                "PRAGMA table_info(expert_strategy_parameter_events)"
            ).fetchall()
        }

    @staticmethod
    def _unresolved_strategy_parameter_event_count(conn: sqlite3.Connection) -> int:
        """统计无法从参数版本反查策略标识的旧事件。"""
        row = conn.execute("""
            SELECT count(*)
            FROM expert_strategy_parameter_events AS legacy
            LEFT JOIN expert_strategy_parameter_versions AS current_version
              ON current_version.id = legacy.parameter_version_id
            LEFT JOIN expert_strategy_parameter_versions AS previous_version
              ON previous_version.id = legacy.previous_parameter_version_id
            WHERE current_version.strategy_id IS NULL
              AND previous_version.strategy_id IS NULL
            """).fetchone()
        return int(row[0])

    @staticmethod
    def _rebuild_strategy_parameter_events(conn: sqlite3.Connection) -> None:
        """在同一事务内重建策略参数事件表并回填策略标识。"""
        conn.execute(
            "ALTER TABLE expert_strategy_parameter_events "
            "RENAME TO expert_strategy_parameter_events_legacy_v2"
        )
        conn.execute("""
            CREATE TABLE expert_strategy_parameter_events (
                id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                parameter_version_id TEXT
                    REFERENCES expert_strategy_parameter_versions(id) ON DELETE RESTRICT,
                previous_parameter_version_id TEXT
                    REFERENCES expert_strategy_parameter_versions(id) ON DELETE RESTRICT,
                decision TEXT NOT NULL CHECK(decision IN ('promote', 'rollback')),
                reason TEXT NOT NULL, metrics_json TEXT NOT NULL, created_at TEXT NOT NULL
            )
            """)
        PaperAgentStore._copy_strategy_parameter_events(conn)
        conn.execute("DROP TABLE expert_strategy_parameter_events_legacy_v2")

    @staticmethod
    def _copy_strategy_parameter_events(conn: sqlite3.Connection) -> None:
        """把旧参数事件复制到带策略标识的新表。"""
        conn.execute("""
            INSERT INTO expert_strategy_parameter_events(
                rowid, id, strategy_id, parameter_version_id,
                previous_parameter_version_id, decision, reason, metrics_json, created_at
            )
            SELECT legacy.rowid, legacy.id,
                   coalesce(current_version.strategy_id, previous_version.strategy_id),
                   legacy.parameter_version_id, legacy.previous_parameter_version_id,
                   legacy.decision, legacy.reason, legacy.metrics_json, legacy.created_at
            FROM expert_strategy_parameter_events_legacy_v2 AS legacy
            LEFT JOIN expert_strategy_parameter_versions AS current_version
              ON current_version.id = legacy.parameter_version_id
            LEFT JOIN expert_strategy_parameter_versions AS previous_version
              ON previous_version.id = legacy.previous_parameter_version_id
            ORDER BY legacy.rowid
            """)

    @classmethod
    def _migrate_strategy_parameter_events(cls, conn: sqlite3.Connection) -> None:
        """把 v2 参数事件表原子迁移到 v3。"""
        columns = cls._strategy_parameter_event_columns(conn)
        if "strategy_id" in columns and int(columns["parameter_version_id"]["notnull"]) == 0:
            return
        if cls._unresolved_strategy_parameter_event_count(conn):
            raise PaperAgentSchemaMigrationError("存在无法关联参数版本的策略事件，数据库未执行迁移")
        conn.execute("BEGIN IMMEDIATE")
        try:
            cls._rebuild_strategy_parameter_events(conn)
        except sqlite3.Error as exc:
            conn.rollback()
            raise PaperAgentSchemaMigrationError("策略参数事件表迁移失败") from exc
        else:
            conn.commit()

    def _initialize(self) -> None:
        """创建当前结构并把受支持的旧版本迁移到最新版。"""
        schema_path = Path(__file__).with_name("schema.sql")
        with self._lock, self._connect() as conn:
            conn.executescript(schema_path.read_text(encoding="utf-8"))
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            version = int(row["value"]) if row is not None else None
            if version is not None and version not in {1, 2, _SCHEMA_VERSION}:
                raise PaperAgentSchemaMigrationError(f"不支持的投资专家数据库版本：{row['value']}")
            self._migrate_strategy_parameter_events(conn)
            if version is None:
                conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
                    (str(_SCHEMA_VERSION),),
                )
            elif version != _SCHEMA_VERSION:
                conn.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                    (str(_SCHEMA_VERSION),),
                )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )

    @classmethod
    def _hash(cls, value: Any) -> str:
        import hashlib

        return hashlib.sha256(cls._json(value).encode("utf-8")).hexdigest()

    def ensure_baseline_policy(self) -> ExpertPolicy:
        existing = self.get_champion()
        if existing is not None:
            return existing
        policy = ExpertPolicy(id="expert_v1", version=1, status="champion")
        try:
            self.save_policy(policy)
        except ValueError:
            policy = self.list_policies(limit=1)[0]
        if self.get_champion() is None:
            self.promote(policy.id, reason="initial baseline", metrics={})
        return self.get_champion() or policy

    def save_policy(self, policy: ExpertPolicy) -> ExpertPolicy:
        payload = policy.model_dump(mode="json")
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO policy_versions(id, version, parent_id, payload_json, payload_hash, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            policy.id,
                            policy.version,
                            policy.parent_id,
                            self._json(payload),
                            self._hash(payload),
                            self._now(),
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"policy version conflict: {exc}") from exc
        return policy

    def get_policy(self, policy_id: str) -> ExpertPolicy | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM policy_versions WHERE id = ?", (policy_id,)
            ).fetchone()
        return ExpertPolicy.model_validate_json(row["payload_json"]) if row else None

    def list_policies(self, *, limit: int = 100) -> list[ExpertPolicy]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM policy_versions ORDER BY version DESC LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
        return [ExpertPolicy.model_validate_json(row["payload_json"]) for row in rows]

    def get_champion(self) -> ExpertPolicy | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("""
                SELECT policy.payload_json
                FROM promotion_events AS promotion
                JOIN policy_versions AS policy ON policy.id = promotion.policy_id
                ORDER BY promotion.created_at DESC, promotion.rowid DESC LIMIT 1
                """).fetchone()
        return ExpertPolicy.model_validate_json(row["payload_json"]) if row else None

    def promote(self, policy_id: str, *, reason: str, metrics: dict[str, Any]) -> dict[str, Any]:
        if self.get_policy(policy_id) is None:
            raise ValueError(f"unknown policy: {policy_id}")
        previous = self.get_champion()
        event = {
            "id": f"promotion_{uuid4().hex}",
            "policy_id": policy_id,
            "previous_policy_id": previous.id if previous else None,
            "decision": "promote",
            "reason": reason,
            "metrics": metrics,
            "created_at": self._now(),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO promotion_events(
                    id, policy_id, previous_policy_id, decision, reason, metrics_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["id"],
                    policy_id,
                    event["previous_policy_id"],
                    "promote",
                    reason,
                    self._json(metrics),
                    event["created_at"],
                ),
            )
        return event

    def rollback_last_promotion(
        self,
        *,
        reason: str,
        metrics: dict[str, Any],
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("""
                SELECT policy_id, previous_policy_id, decision
                FROM promotion_events
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """).fetchone()
            if row is None or row["decision"] == "rollback" or not row["previous_policy_id"]:
                return None
            event = {
                "id": f"promotion_{uuid4().hex}",
                "policy_id": str(row["previous_policy_id"]),
                "previous_policy_id": str(row["policy_id"]),
                "decision": "rollback",
                "reason": reason,
                "metrics": metrics,
                "created_at": self._now(),
            }
            conn.execute(
                """
                INSERT INTO promotion_events(
                    id, policy_id, previous_policy_id, decision, reason, metrics_json, created_at
                ) VALUES (?, ?, ?, 'rollback', ?, ?, ?)
                """,
                (
                    event["id"],
                    event["policy_id"],
                    event["previous_policy_id"],
                    reason,
                    self._json(metrics),
                    event["created_at"],
                ),
            )
        return event

    def start_session(
        self,
        trade_date: date,
        policy_id: str,
        *,
        mode: str,
        candidates: list[str],
    ) -> dict[str, Any]:
        session_id = f"session_{trade_date.isoformat()}_{policy_id}_{mode}"
        started_at = self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trading_sessions(
                    id, trade_date, policy_id, mode, status, candidate_json,
                    started_at, finished_at, summary_json
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, NULL, '{}')
                ON CONFLICT(trade_date, policy_id, mode) DO NOTHING
                """,
                (
                    session_id,
                    trade_date.isoformat(),
                    policy_id,
                    mode,
                    self._json(candidates),
                    started_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM trading_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row)

    def finish_session(
        self, session_id: str, summary: dict[str, Any], status: str = "succeeded"
    ) -> None:
        """完成尚未结束的交易会话并保存摘要。"""
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE trading_sessions
                SET status = ?, summary_json = ?, finished_at = ?
                WHERE id = ? AND finished_at IS NULL
                """,
                (status, self._json(summary), self._now(), session_id),
            )

    def save_strategy_orchestration(
        self,
        session_id: str,
        trade_date: date,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """幂等保存一次交易会话的策略编排快照。"""
        created_at = self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO strategy_orchestration_snapshots(
                    session_id, trade_date, payload_json, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO NOTHING
                """,
                (
                    session_id,
                    trade_date.isoformat(),
                    self._json(payload),
                    self._hash(payload),
                    created_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM strategy_orchestration_snapshots WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def latest_strategy_orchestration(self) -> dict[str, Any] | None:
        """返回最近一次持久化的策略编排快照。"""
        with self._lock, self._connect() as conn:
            row = conn.execute("""
                SELECT * FROM strategy_orchestration_snapshots
                ORDER BY trade_date DESC, created_at DESC, rowid DESC LIMIT 1
                """).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def record_expert_strategy(self, record: ExpertStrategyRecord) -> dict[str, Any]:
        """登记一个 AI 专家策略及其初始评估状态。"""
        row_id = f"expert_strategy_{uuid4().hex}"
        created_at = self._now()
        evaluated_at = created_at if record.status != "shadow" else None
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO expert_strategy_versions(
                    id, strategy_id, parent_strategy_id, regime, status,
                    metrics_json, reason, created_at, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    record.strategy_id,
                    record.parent_strategy_id,
                    record.regime,
                    record.status,
                    self._json(record.metrics),
                    record.reason,
                    created_at,
                    evaluated_at,
                ),
            )
        return {
            "id": row_id,
            "strategy_id": record.strategy_id,
            "parent_strategy_id": record.parent_strategy_id,
            "regime": record.regime,
            "status": record.status,
            "metrics": record.metrics,
            "reason": record.reason,
            "created_at": created_at,
            "evaluated_at": evaluated_at,
        }

    def finish_expert_strategy_evaluation(
        self,
        strategy_id: str,
        *,
        status: str,
        metrics: dict[str, Any],
        reason: str,
    ) -> None:
        """完成一个影子策略的保护集评估。"""
        if status not in {"promoted", "rejected"}:
            raise ValueError(f"invalid evaluated expert strategy status: {status}")
        with self._lock, self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE expert_strategy_versions
                SET status = ?, metrics_json = ?, reason = ?, evaluated_at = ?
                WHERE strategy_id = ? AND status = 'shadow'
                """,
                (status, self._json(metrics), reason, self._now(), strategy_id),
            ).rowcount
        if updated != 1:
            raise ValueError(f"unknown shadow expert strategy: {strategy_id}")

    def list_expert_strategies(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """按创建时间倒序返回 AI 专家策略实验。"""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM expert_strategy_versions
                ORDER BY created_at DESC, rowid DESC LIMIT ?
                """,
                (min(max(limit, 1), 500),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metrics"] = json.loads(item.pop("metrics_json"))
            result.append(item)
        return result

    def promoted_expert_strategy_ids(self) -> set[str]:
        """返回当前已晋级的 AI 专家策略标识。"""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT strategy_id FROM expert_strategy_versions WHERE status = 'promoted'"
            ).fetchall()
        return {str(row["strategy_id"]) for row in rows}

    @staticmethod
    def _next_strategy_parameter_version(
        conn: sqlite3.Connection,
        strategy_id: str,
    ) -> tuple[int, str | None]:
        """返回策略的下一个参数版本号和父版本标识。"""
        previous = conn.execute(
            """
            SELECT id, version FROM expert_strategy_parameter_versions
            WHERE strategy_id = ? ORDER BY version DESC LIMIT 1
            """,
            (strategy_id,),
        ).fetchone()
        if previous is None:
            return 1, None
        return int(previous["version"]) + 1, str(previous["id"])

    def save_strategy_parameter_version(
        self, candidate: StrategyParameterCandidate
    ) -> dict[str, Any]:
        """不可变保存一个策略参数候选版本。"""
        with self._lock, self._connect() as conn:
            version, parent_id = self._next_strategy_parameter_version(conn, candidate.strategy_id)
            version_id = f"expert_params_{candidate.strategy_id}_v{version}_{uuid4().hex[:8]}"
            conn.execute(
                """
                INSERT INTO expert_strategy_parameter_versions(
                    id, strategy_id, version, parent_id, params_json, metrics_json,
                    status, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    candidate.strategy_id,
                    version,
                    parent_id,
                    self._json(candidate.params),
                    self._json(candidate.metrics),
                    candidate.status,
                    candidate.reason,
                    self._now(),
                ),
            )
        return {
            "id": version_id,
            "strategy_id": candidate.strategy_id,
            "version": version,
            "parent_id": parent_id,
            "params": candidate.params,
            "metrics": candidate.metrics,
            "status": candidate.status,
            "reason": candidate.reason,
        }

    @classmethod
    def _insert_strategy_parameter_event(
        cls,
        conn: sqlite3.Connection,
        event: StrategyParameterEventRecord,
    ) -> None:
        """写入一个已经校验的策略参数事件。"""
        conn.execute(
            """
            INSERT INTO expert_strategy_parameter_events(
                id, strategy_id, parameter_version_id, previous_parameter_version_id,
                decision, reason, metrics_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.strategy_id,
                event.parameter_version_id,
                event.previous_parameter_version_id,
                event.decision,
                event.reason,
                cls._json(event.metrics),
                event.created_at,
            ),
        )

    def promote_strategy_parameters(
        self,
        version_id: str,
        *,
        reason: str,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """把参数版本设为策略当前活动版本并记录事件。"""
        with self._lock, self._connect() as conn:
            current = conn.execute(
                """
                SELECT id, strategy_id FROM expert_strategy_parameter_versions WHERE id = ?
                """,
                (version_id,),
            ).fetchone()
            if current is None:
                raise ValueError(f"unknown strategy parameter version: {version_id}")
            previous = conn.execute(
                """
                SELECT parameter_version_id AS id
                FROM expert_strategy_parameter_events
                WHERE strategy_id = ?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (str(current["strategy_id"]),),
            ).fetchone()
            event = StrategyParameterEventRecord(
                id=f"expert_params_promotion_{uuid4().hex}",
                strategy_id=str(current["strategy_id"]),
                parameter_version_id=version_id,
                previous_parameter_version_id=(
                    str(previous["id"]) if previous is not None and previous["id"] else None
                ),
                decision="promote",
                reason=reason,
                metrics=metrics,
                created_at=self._now(),
            )
            self._insert_strategy_parameter_event(conn, event)
        return event.model_dump(mode="json")

    def active_strategy_parameters(self) -> dict[str, dict[str, Any]]:
        """返回每个策略最近一次参数事件指向的活动版本。"""
        with self._lock, self._connect() as conn:
            rows = conn.execute("""
                SELECT version.strategy_id, version.id, version.version, version.params_json
                FROM expert_strategy_parameter_events AS event
                JOIN expert_strategy_parameter_versions AS version
                  ON version.id = event.parameter_version_id
                WHERE event.rowid IN (
                    SELECT max(event2.rowid)
                    FROM expert_strategy_parameter_events AS event2
                    GROUP BY event2.strategy_id
                )
                  AND event.parameter_version_id IS NOT NULL
                """).fetchall()
        return {
            str(row["strategy_id"]): {
                "version_id": str(row["id"]),
                "version": int(row["version"]),
                "params": json.loads(row["params_json"]),
            }
            for row in rows
        }

    def rollback_last_strategy_parameter_promotion(
        self,
        *,
        reason: str,
        metrics: dict[str, Any],
    ) -> dict[str, Any] | None:
        """回滚最近一次参数晋级，必要时恢复到策略默认值。"""
        with self._lock, self._connect() as conn:
            latest = conn.execute("""
                SELECT * FROM expert_strategy_parameter_events
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """).fetchone()
            if latest is None or str(latest["decision"]) != "promote":
                return None
            event = StrategyParameterEventRecord(
                id=f"expert_params_rollback_{uuid4().hex}",
                strategy_id=str(latest["strategy_id"]),
                parameter_version_id=(
                    str(latest["previous_parameter_version_id"])
                    if latest["previous_parameter_version_id"]
                    else None
                ),
                previous_parameter_version_id=str(latest["parameter_version_id"]),
                decision="rollback",
                reason=reason,
                metrics=metrics,
                created_at=self._now(),
            )
            self._insert_strategy_parameter_event(conn, event)
        return event.model_dump(mode="json")

    def rollback_latest_expert_strategy(
        self,
        *,
        reason: str,
        metrics: dict[str, Any],
    ) -> str | None:
        """风险触发后停用最近晋级的 AI 专家策略。"""
        with self._lock, self._connect() as conn:
            row = conn.execute("""
                SELECT strategy_id FROM expert_strategy_versions
                WHERE status = 'promoted'
                ORDER BY evaluated_at DESC, rowid DESC LIMIT 1
                """).fetchone()
            if row is None:
                return None
            strategy_id = str(row["strategy_id"])
            conn.execute(
                """
                UPDATE expert_strategy_versions
                SET status = 'rejected', metrics_json = ?, reason = ?, evaluated_at = ?
                WHERE strategy_id = ? AND status = 'promoted'
                """,
                (self._json(metrics), reason, self._now(), strategy_id),
            )
        return strategy_id

    def list_strategy_parameter_versions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """返回不可变的策略参数优化实验记录。"""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, strategy_id, version, parent_id, params_json, metrics_json,
                       status, reason, created_at
                FROM expert_strategy_parameter_versions
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "strategy_id": str(row["strategy_id"]),
                "version": int(row["version"]),
                "parent_id": str(row["parent_id"]) if row["parent_id"] else None,
                "params": json.loads(row["params_json"]),
                "metrics": json.loads(row["metrics_json"]),
                "status": str(row["status"]),
                "reason": str(row["reason"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def recover_interrupted_records(self, *, before_trade_date: date) -> dict[str, int]:
        """应用重启后关闭无法恢复的内存任务记录。"""
        finished_at = self._now()
        interruption = self._json({"reason": "interrupted_on_restart"})
        with self._lock, self._connect() as conn:
            sessions = conn.execute(
                """
                UPDATE trading_sessions
                SET status = 'interrupted', summary_json = ?, finished_at = ?
                WHERE status = 'running' AND finished_at IS NULL AND trade_date < ?
                """,
                (interruption, finished_at, before_trade_date.isoformat()),
            ).rowcount
            datasets = conn.execute(
                """
                UPDATE dataset_runs
                SET status = 'failed', error = 'interrupted_on_restart', finished_at = ?
                WHERE status = 'running' AND finished_at IS NULL
                """,
                (finished_at,),
            ).rowcount
        return {"sessions": sessions, "datasets": datasets}

    def save_decision(
        self,
        *,
        decision_id: str,
        session_id: str,
        symbol: str,
        decision_time: datetime,
        action: str,
        features: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        payload_hash = self._hash(features)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO decision_snapshots(
                    id, session_id, symbol, decision_time, action, feature_json,
                    feature_hash, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    session_id,
                    symbol,
                    decision_time.isoformat(),
                    action,
                    self._json(features),
                    payload_hash,
                    reason,
                    self._now(),
                ),
            )
        return {"id": decision_id, "feature_hash": payload_hash}

    def save_execution_events(self, session_id: str, events: list[ExecutionEvent]) -> int:
        if not events:
            return 0
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO execution_events(
                    id, session_id, event_type, occurred_at, order_id, symbol, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        event.id,
                        session_id,
                        event.event_type,
                        event.occurred_at.isoformat(),
                        event.order_id,
                        event.symbol,
                        self._json(event.model_dump(mode="json")),
                    )
                    for event in events
                ],
            )
            self._execution_statistics_cache = None
        return len(events)

    def save_portfolio_snapshot(
        self,
        session_id: str,
        *,
        as_of: datetime,
        cash: float,
        equity: float,
        payload: dict[str, Any],
    ) -> str:
        snapshot_id = f"snapshot_{uuid4().hex}"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO portfolio_snapshots(
                    id, session_id, as_of, cash, equity, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    session_id,
                    as_of.isoformat(),
                    cash,
                    equity,
                    self._json(payload),
                    self._now(),
                ),
            )
        return snapshot_id

    def latest_portfolio_snapshot(self) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM portfolio_snapshots ORDER BY as_of DESC, rowid DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def save_portfolio_sync(
        self,
        *,
        source: str,
        mode: str,
        cash: float,
        equity: float,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if mode != "replace":
            raise ValueError(f"unsupported portfolio sync mode: {mode}")
        event_id = f"portfolio_sync_{uuid4().hex}"
        created_at = self._now()
        payload_hash = self._hash(payload)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO portfolio_sync_events(
                    id, source, mode, cash, equity, payload_json, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    source,
                    mode,
                    float(cash),
                    float(equity),
                    self._json(payload),
                    payload_hash,
                    created_at,
                ),
            )
        return {
            "id": event_id,
            "source": source,
            "mode": mode,
            "cash": float(cash),
            "equity": float(equity),
            "payload": payload,
            "payload_hash": payload_hash,
            "created_at": created_at,
        }

    def latest_portfolio_sync(self) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM portfolio_sync_events ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def latest_portfolio_state(self) -> dict[str, Any] | None:
        snapshot = self.latest_portfolio_snapshot()
        synced = self.latest_portfolio_sync()
        if synced is None:
            return snapshot
        if snapshot is not None and snapshot["created_at"] > synced["created_at"]:
            return snapshot
        return {
            **synced,
            "as_of": synced["created_at"],
            "state_source": "stock_portfolio_sync",
        }

    def portfolio_peak_equity(self, *, since: str | None = None) -> float | None:
        query = "SELECT max(equity) AS peak FROM portfolio_snapshots"
        params: tuple[str, ...] = ()
        if since is not None:
            query += " WHERE created_at > ?"
            params = (since,)
        with self._lock, self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return float(row["peak"]) if row and row["peak"] is not None else None

    def list_sessions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trading_sessions ORDER BY trade_date DESC, started_at DESC LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["candidates"] = json.loads(item.pop("candidate_json"))
            item["summary"] = json.loads(item.pop("summary_json"))
            result.append(item)
        return result

    def list_execution_events(
        self, *, session_id: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        query = "SELECT payload_json FROM execution_events"
        params: list[Any] = []
        if session_id:
            query += " WHERE session_id = ?"
            params.append(session_id)
        query += " ORDER BY occurred_at DESC, rowid DESC LIMIT ?"
        params.append(min(max(limit, 1), 2000))
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def list_trade_history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return fills enriched with FIFO entry cost and after-cost P&L attribution."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    e.session_id,
                    s.trade_date,
                    e.event_type,
                    e.occurred_at,
                    e.order_id,
                    e.symbol,
                    e.payload_json,
                    d.id AS decision_id,
                    d.decision_time,
                    d.action AS decision_action,
                    d.reason AS decision_reason,
                    d.feature_json
                FROM execution_events AS e
                JOIN trading_sessions AS s ON s.id = e.session_id
                LEFT JOIN decision_snapshots AS d
                    ON e.order_id = 'order_' || d.id
                    AND d.session_id = e.session_id
                WHERE e.event_type IN ('order_filled', 'order_partially_filled')
                ORDER BY e.occurred_at ASC, e.rowid ASC
                """,
            ).fetchall()

        open_fills: dict[str, list[dict[str, Any]]] = {}
        history: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            decision_features = json.loads(row["feature_json"]) if row["feature_json"] else None
            side = payload.get("side")
            symbol = str(row["symbol"] or payload.get("symbol") or "")
            shares = int(payload.get("shares") or 0)
            fill_price = float(payload["price"]) if payload.get("price") is not None else None
            fees = float(payload.get("fees") or 0.0)
            realized_pnl = (
                float(payload["realized_pnl"]) if payload.get("realized_pnl") is not None else None
            )
            item = {
                "id": payload.get("id"),
                "session_id": row["session_id"],
                "trade_date": row["trade_date"],
                "order_id": row["order_id"],
                "symbol": symbol,
                "side": side,
                "occurred_at": row["occurred_at"],
                "fill_status": row["event_type"],
                "shares": shares,
                "price": fill_price,
                "fees": fees,
                "realized_pnl": realized_pnl,
                "execution_reason": payload.get("reason"),
                "decision_id": row["decision_id"],
                "decision_time": row["decision_time"],
                "decision_action": row["decision_action"],
                "decision_reason": row["decision_reason"],
                "decision_features": decision_features,
                "entry_time": row["occurred_at"] if side == "buy" else None,
                "entry_price": fill_price if side == "buy" else None,
                "exit_price": fill_price if side == "sell" else None,
                "entry_fees": fees if side == "buy" else None,
                "exit_fees": fees if side == "sell" else None,
                "total_fees": fees if side == "buy" else None,
                "gross_pnl": None,
                "price_change_pct": None,
                "realized_pnl_pct": None,
                "pnl_reason": None,
                "entry_decision_reason": row["decision_reason"] if side == "buy" else None,
                "entry_decision_features": decision_features if side == "buy" else None,
                "exit_decision_reason": row["decision_reason"] if side == "sell" else None,
            }

            if side == "buy" and shares > 0 and fill_price is not None:
                open_fills.setdefault(symbol, []).append(
                    {
                        "remaining_shares": shares,
                        "shares": shares,
                        "price": fill_price,
                        "fees": fees,
                        "occurred_at": row["occurred_at"],
                        "acquired_date": datetime.fromisoformat(row["occurred_at"]).date(),
                        "decision_reason": row["decision_reason"],
                        "decision_features": decision_features,
                    }
                )
            elif side == "sell" and shares > 0 and fill_price is not None:
                remaining = shares
                matched_shares = 0
                entry_notional = 0.0
                entry_fees = 0.0
                first_entry: dict[str, Any] | None = None
                sell_date = datetime.fromisoformat(row["occurred_at"]).date()
                queue = open_fills.setdefault(symbol, [])
                for entry in queue:
                    if remaining <= 0:
                        break
                    if entry["remaining_shares"] <= 0 or entry["acquired_date"] >= sell_date:
                        continue
                    taken = min(int(entry["remaining_shares"]), remaining)
                    if first_entry is None:
                        first_entry = entry
                    entry_notional += taken * float(entry["price"])
                    entry_fees += float(entry["fees"]) * taken / int(entry["shares"])
                    entry["remaining_shares"] -= taken
                    matched_shares += taken
                    remaining -= taken
                open_fills[symbol] = [entry for entry in queue if entry["remaining_shares"] > 0]

                if matched_shares == shares and first_entry is not None:
                    entry_price = entry_notional / shares
                    cost_basis = entry_notional + entry_fees
                    gross_pnl = (fill_price - entry_price) * shares
                    net_pnl = realized_pnl
                    if net_pnl is None:
                        net_pnl = gross_pnl - entry_fees - fees
                        item["realized_pnl"] = round(net_pnl, 2)
                    item.update(
                        {
                            "entry_time": first_entry["occurred_at"],
                            "entry_price": round(entry_price, 4),
                            "entry_fees": round(entry_fees, 2),
                            "total_fees": round(entry_fees + fees, 2),
                            "gross_pnl": round(gross_pnl, 2),
                            "price_change_pct": round(fill_price / entry_price - 1, 6),
                            "realized_pnl_pct": (
                                round(net_pnl / cost_basis, 6) if cost_basis > 0 else None
                            ),
                            "pnl_reason": self._pnl_reason(entry_price, fill_price, net_pnl),
                            "entry_decision_reason": first_entry["decision_reason"],
                            "entry_decision_features": first_entry["decision_features"],
                        }
                    )
                else:
                    item["pnl_reason"] = "missing_entry_match"
            history.append(item)
        return list(reversed(history))[: min(max(limit, 1), 500)]

    @staticmethod
    def _pnl_reason(entry_price: float, exit_price: float, net_pnl: float) -> str:
        if abs(net_pnl) < 0.005:
            return "breakeven_after_costs"
        if net_pnl > 0:
            return "price_gain_after_costs"
        if exit_price > entry_price:
            return "costs_exceeded_price_gain"
        if exit_price < entry_price:
            return "price_loss_and_costs"
        return "costs_caused_loss"

    def execution_statistics(self) -> dict[str, Any]:
        """Aggregate persisted fills without rescanning them on every status poll."""
        with self._lock:
            if self._execution_statistics_cache is not None:
                return dict(self._execution_statistics_cache)
            with self._connect() as conn:
                row = conn.execute("""
                    WITH fills AS (
                        SELECT
                            coalesce(order_id, id) AS trade_key,
                            occurred_at,
                            json_extract(payload_json, '$.side') AS side,
                            CAST(json_extract(payload_json, '$.realized_pnl') AS REAL)
                                AS realized_pnl
                        FROM execution_events
                        WHERE event_type IN ('order_filled', 'order_partially_filled')
                    ), orders AS (
                        SELECT
                            trade_key,
                            max(occurred_at) AS occurred_at,
                            max(side) AS side,
                            CASE
                                WHEN count(realized_pnl) > 0
                                THEN sum(coalesce(realized_pnl, 0.0))
                                ELSE NULL
                            END AS realized_pnl
                        FROM fills
                        GROUP BY trade_key
                    )
                    SELECT
                        count(*) AS filled_order_count,
                        sum(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) AS buy_order_count,
                        sum(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) AS sell_order_count,
                        sum(CASE WHEN realized_pnl IS NOT NULL THEN 1 ELSE 0 END)
                            AS closed_trade_count,
                        sum(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
                            AS winning_trade_count,
                        sum(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END)
                            AS losing_trade_count,
                        sum(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END)
                            AS breakeven_trade_count,
                        sum(coalesce(realized_pnl, 0.0)) AS realized_pnl,
                        avg(CASE WHEN realized_pnl > 0 THEN realized_pnl END)
                            AS average_win_pnl,
                        avg(CASE WHEN realized_pnl < 0 THEN -realized_pnl END)
                            AS average_loss_pnl,
                        max(occurred_at) AS latest_fill_at
                    FROM orders
                    """).fetchone()

            closed = int(row["closed_trade_count"] or 0)
            wins = int(row["winning_trade_count"] or 0)
            average_win = (
                float(row["average_win_pnl"]) if row["average_win_pnl"] is not None else None
            )
            average_loss = (
                float(row["average_loss_pnl"]) if row["average_loss_pnl"] is not None else None
            )
            result = {
                "filled_order_count": int(row["filled_order_count"] or 0),
                "buy_order_count": int(row["buy_order_count"] or 0),
                "sell_order_count": int(row["sell_order_count"] or 0),
                "closed_trade_count": closed,
                "winning_trade_count": wins,
                "losing_trade_count": int(row["losing_trade_count"] or 0),
                "breakeven_trade_count": int(row["breakeven_trade_count"] or 0),
                "realized_pnl": round(float(row["realized_pnl"] or 0.0), 2),
                "win_rate": round(wins / closed, 6) if closed else None,
                "average_win_pnl": (round(average_win, 2) if average_win is not None else None),
                "average_loss_pnl": (round(average_loss, 2) if average_loss is not None else None),
                "profit_loss_ratio": (
                    round(average_win / average_loss, 6)
                    if average_win is not None and average_loss is not None and average_loss != 0
                    else None
                ),
                "latest_fill_at": row["latest_fill_at"],
            }
            self._execution_statistics_cache = result
            return dict(result)

    def record_dataset_run(
        self,
        *,
        start_date: date,
        end_date: date,
        status: str,
        manifest: dict[str, Any],
        run_id: str | None = None,
        error: str | None = None,
        finished: bool = False,
    ) -> str:
        resolved_id = run_id or f"dataset_{uuid4().hex}"
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dataset_runs(
                    id, status, start_date, end_date, manifest_json, error, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    manifest_json = excluded.manifest_json,
                    error = excluded.error,
                    finished_at = excluded.finished_at
                """,
                (
                    resolved_id,
                    status,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    self._json(manifest),
                    error,
                    now,
                    now if finished else None,
                ),
            )
        return resolved_id

    def save_reflection(self, session_id: str, payload: dict[str, Any]) -> str:
        reflection_id = f"reflection_{uuid4().hex}"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_reflections(id, session_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET payload_json = excluded.payload_json
                """,
                (reflection_id, session_id, self._json(payload), self._now()),
            )
        return reflection_id

    def record_experiment(
        self,
        *,
        champion_policy_id: str,
        candidate_policy_id: str,
        mutation_field: str,
        status: str,
        champion_metrics: dict[str, Any],
        candidate_metrics: dict[str, Any],
        reason: str,
    ) -> str:
        experiment_id = f"experiment_{uuid4().hex}"
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO evolution_experiments(
                    id, champion_policy_id, candidate_policy_id, mutation_field, status,
                    champion_metrics_json, candidate_metrics_json, reason, created_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment_id,
                    champion_policy_id,
                    candidate_policy_id,
                    mutation_field,
                    status,
                    self._json(champion_metrics),
                    self._json(candidate_metrics),
                    reason,
                    now,
                    now,
                ),
            )
        return experiment_id

    def list_experiments(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM evolution_experiments
                ORDER BY created_at DESC, rowid DESC LIMIT ?
                """,
                (min(max(limit, 1), 500),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["champion_metrics"] = json.loads(item.pop("champion_metrics_json"))
            item["candidate_metrics"] = json.loads(item.pop("candidate_metrics_json"))
            result.append(item)
        return result

    def set_runtime_setting(self, key: str, value: Any) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_settings(key, value_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, self._json(value), self._now()),
            )

    def get_runtime_setting(self, key: str, default: Any = None) -> Any:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM runtime_settings WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row["value_json"]) if row else default

    def save_model(self, model: TrainedDecisionModel) -> TrainedDecisionModel:
        payload = model.model_dump(mode="json")
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO model_versions(
                        id, version, payload_json, payload_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        model.id,
                        model.version,
                        self._json(payload),
                        self._hash(payload),
                        self._now(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"model version conflict: {exc}") from exc
        return model

    def list_models(self, *, limit: int = 50) -> list[TrainedDecisionModel]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM model_versions ORDER BY version DESC LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
        return [TrainedDecisionModel.model_validate_json(row["payload_json"]) for row in rows]

    def get_active_model(self) -> TrainedDecisionModel | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("""
                SELECT model.payload_json
                FROM model_promotion_events AS promotion
                JOIN model_versions AS model ON model.id = promotion.model_id
                ORDER BY promotion.created_at DESC, promotion.rowid DESC LIMIT 1
                """).fetchone()
        if row is None:
            return None
        model = TrainedDecisionModel.model_validate_json(row["payload_json"])
        if self.get_runtime_setting("disabled_model_id") == model.id:
            return None
        return model

    def promote_model(
        self,
        model_id: str,
        *,
        reason: str,
        metrics: dict[str, Any],
    ) -> str:
        models = {model.id for model in self.list_models(limit=500)}
        if model_id not in models:
            raise ValueError(f"unknown model: {model_id}")
        previous = self.get_active_model()
        event_id = f"model_promotion_{uuid4().hex}"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_promotion_events(
                    id, model_id, previous_model_id, decision, reason, metrics_json, created_at
                ) VALUES (?, ?, ?, 'promote', ?, ?, ?)
                """,
                (
                    event_id,
                    model_id,
                    previous.id if previous else None,
                    reason,
                    self._json(metrics),
                    self._now(),
                ),
            )
        self.set_runtime_setting("disabled_model_id", None)
        return event_id

    def rollback_last_model_promotion(
        self,
        *,
        reason: str,
        metrics: dict[str, Any],
    ) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("""
                SELECT model_id, previous_model_id, decision
                FROM model_promotion_events
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """).fetchone()
            if row is None or row["decision"] == "rollback":
                return None
            if not row["previous_model_id"]:
                event_id = f"model_guard_{uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO model_guard_events(
                        id, model_id, action, reason, metrics_json, created_at
                    ) VALUES (?, ?, 'disable', ?, ?, ?)
                    """,
                    (
                        event_id,
                        str(row["model_id"]),
                        reason,
                        self._json(metrics),
                        self._now(),
                    ),
                )
                disabled_model_id = str(row["model_id"])
            else:
                disabled_model_id = None
                event_id = f"model_promotion_{uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO model_promotion_events(
                        id, model_id, previous_model_id, decision, reason, metrics_json, created_at
                    ) VALUES (?, ?, ?, 'rollback', ?, ?, ?)
                    """,
                    (
                        event_id,
                        str(row["previous_model_id"]),
                        str(row["model_id"]),
                        reason,
                        self._json(metrics),
                        self._now(),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO model_guard_events(
                        id, model_id, action, reason, metrics_json, created_at
                    ) VALUES (?, ?, 'rollback', ?, ?, ?)
                    """,
                    (
                        f"model_guard_{uuid4().hex}",
                        str(row["model_id"]),
                        reason,
                        self._json(metrics),
                        self._now(),
                    ),
                )
        if disabled_model_id is not None:
            self.set_runtime_setting("disabled_model_id", disabled_model_id)
        return event_id

    def status(self) -> dict[str, Any]:
        champion = self.get_champion()
        active_model = self.get_active_model()
        models = self.list_models(limit=1)
        latest_model = models[0] if models else None
        disabled_model_id = self.get_runtime_setting("disabled_model_id")
        if active_model is not None:
            model_runtime_status = "active"
        elif latest_model is None:
            model_runtime_status = "baseline"
        elif disabled_model_id == latest_model.id:
            model_runtime_status = "disabled"
        else:
            model_runtime_status = "not_activated"
        sessions = self.list_sessions(limit=1)
        with self._lock, self._connect() as conn:
            dataset = conn.execute(
                "SELECT * FROM dataset_runs ORDER BY started_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        dataset_status = None
        if dataset:
            dataset_status = dict(dataset)
            dataset_status["manifest"] = json.loads(dataset_status.pop("manifest_json"))
        return {
            "champion": champion.model_dump(mode="json") if champion else None,
            "latest_session": sessions[0] if sessions else None,
            "dataset": dataset_status,
            "enabled": bool(self.get_runtime_setting("enabled", False)),
            "active_model": (
                active_model.model_dump(mode="json") if active_model is not None else None
            ),
            "latest_model": (
                latest_model.model_dump(mode="json") if latest_model is not None else None
            ),
            "model_runtime_status": model_runtime_status,
        }
