"""SQLite control plane and append-only audit ledger for the paper agent."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.paper_agent.models import ExecutionEvent, ExpertPolicy, TrainedDecisionModel

_SCHEMA_VERSION = 1


class PaperAgentStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "user_data" / "investment_expert_agent.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS policy_versions (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    parent_id TEXT REFERENCES policy_versions(id) ON DELETE RESTRICT,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(version)
                );
                CREATE TABLE IF NOT EXISTS promotion_events (
                    id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL REFERENCES policy_versions(id) ON DELETE RESTRICT,
                    previous_policy_id TEXT REFERENCES policy_versions(id) ON DELETE RESTRICT,
                    decision TEXT NOT NULL CHECK(decision IN ('promote', 'rollback')),
                    reason TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trading_sessions (
                    id TEXT PRIMARY KEY,
                    trade_date TEXT NOT NULL,
                    policy_id TEXT NOT NULL REFERENCES policy_versions(id) ON DELETE RESTRICT,
                    mode TEXT NOT NULL CHECK(mode IN ('paper', 'replay', 'shadow')),
                    status TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    summary_json TEXT NOT NULL,
                    UNIQUE(trade_date, policy_id, mode)
                );
                CREATE TABLE IF NOT EXISTS decision_snapshots (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES trading_sessions(id) ON DELETE RESTRICT,
                    symbol TEXT NOT NULL,
                    decision_time TEXT NOT NULL,
                    action TEXT NOT NULL,
                    feature_json TEXT NOT NULL,
                    feature_hash TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES trading_sessions(id) ON DELETE RESTRICT,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    order_id TEXT,
                    symbol TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_execution_session_time
                    ON execution_events(session_id, occurred_at);
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES trading_sessions(id) ON DELETE RESTRICT,
                    as_of TEXT NOT NULL,
                    cash REAL NOT NULL,
                    equity REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_reflections (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL UNIQUE REFERENCES trading_sessions(id) ON DELETE RESTRICT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evolution_experiments (
                    id TEXT PRIMARY KEY,
                    champion_policy_id TEXT NOT NULL REFERENCES policy_versions(id) ON DELETE RESTRICT,
                    candidate_policy_id TEXT NOT NULL REFERENCES policy_versions(id) ON DELETE RESTRICT,
                    mutation_field TEXT NOT NULL,
                    status TEXT NOT NULL,
                    champion_metrics_json TEXT NOT NULL,
                    candidate_metrics_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS dataset_runs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_versions (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_promotion_events (
                    id TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
                    previous_model_id TEXT REFERENCES model_versions(id) ON DELETE RESTRICT,
                    decision TEXT NOT NULL CHECK(decision IN ('promote', 'rollback')),
                    reason TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_guard_events (
                    id TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
                    action TEXT NOT NULL CHECK(action IN ('disable', 'rollback')),
                    reason TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
                    (str(_SCHEMA_VERSION),),
                )
            elif int(row["value"]) != _SCHEMA_VERSION:
                raise RuntimeError(f"unsupported investment expert schema: {row['value']}")

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

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
                            policy.id, policy.version, policy.parent_id, self._json(payload),
                            self._hash(payload), self._now(),
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
            row = conn.execute(
                """
                SELECT policy.payload_json
                FROM promotion_events AS promotion
                JOIN policy_versions AS policy ON policy.id = promotion.policy_id
                ORDER BY promotion.created_at DESC, promotion.rowid DESC LIMIT 1
                """
            ).fetchone()
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
                    event["id"], policy_id, event["previous_policy_id"], "promote", reason,
                    self._json(metrics), event["created_at"],
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
            row = conn.execute(
                """
                SELECT policy_id, previous_policy_id, decision
                FROM promotion_events
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """
            ).fetchone()
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
                (session_id, trade_date.isoformat(), policy_id, mode, self._json(candidates), started_at),
            )
            row = conn.execute("SELECT * FROM trading_sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row)

    def finish_session(self, session_id: str, summary: dict[str, Any], status: str = "succeeded") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE trading_sessions
                SET status = ?, summary_json = ?, finished_at = ?
                WHERE id = ? AND finished_at IS NULL
                """,
                (status, self._json(summary), self._now(), session_id),
            )

    def recover_interrupted_records(self, *, before_trade_date: date) -> dict[str, int]:
        """Close records whose in-memory worker cannot survive an application restart."""
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
                    decision_id, session_id, symbol, decision_time.isoformat(), action,
                    self._json(features), payload_hash, reason, self._now(),
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
                        event.id, session_id, event.event_type, event.occurred_at.isoformat(),
                        event.order_id, event.symbol, self._json(event.model_dump(mode="json")),
                    )
                    for event in events
                ],
            )
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
                (snapshot_id, session_id, as_of.isoformat(), cash, equity, self._json(payload), self._now()),
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

    def portfolio_peak_equity(self) -> float | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT max(equity) AS peak FROM portfolio_snapshots").fetchone()
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

    def list_execution_events(self, *, session_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
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
                    resolved_id, status, start_date.isoformat(), end_date.isoformat(),
                    self._json(manifest), error, now, now if finished else None,
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
                    experiment_id, champion_policy_id, candidate_policy_id, mutation_field, status,
                    self._json(champion_metrics), self._json(candidate_metrics), reason, now, now,
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
            row = conn.execute(
                """
                SELECT model.payload_json
                FROM model_promotion_events AS promotion
                JOIN model_versions AS model ON model.id = promotion.model_id
                ORDER BY promotion.created_at DESC, promotion.rowid DESC LIMIT 1
                """
            ).fetchone()
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
            row = conn.execute(
                """
                SELECT model_id, previous_model_id, decision
                FROM model_promotion_events
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """
            ).fetchone()
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
