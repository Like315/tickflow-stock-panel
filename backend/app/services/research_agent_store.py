"""AI 研究 Agent 的 SQLite 持久化。

推荐批次写入后不可变。复盘按批次、股票和交易日幂等更新。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.research_agent_models import (
    DailyReview,
    RecommendationBatch,
    StageReview,
)

_SCHEMA_VERSION = 1


class ResearchAgentStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "user_data" / "ai_research_agent.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 15000")
        return conn

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recommendation_batches (
                    id TEXT PRIMARY KEY,
                    as_of TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    parent_batch_id TEXT REFERENCES recommendation_batches(id),
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    screening_version TEXT NOT NULL,
                    market_snapshot_json TEXT NOT NULL,
                    screen_summary_json TEXT NOT NULL,
                    candidates_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_recommendation_batches_as_of
                    ON recommendation_batches(as_of DESC, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_recommendation_batches_version
                    ON recommendation_batches(as_of, version);
                CREATE TABLE IF NOT EXISTS recommendations (
                    batch_id TEXT NOT NULL REFERENCES recommendation_batches(id) ON DELETE RESTRICT,
                    position INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (batch_id, position),
                    UNIQUE (batch_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS daily_reviews (
                    batch_id TEXT NOT NULL REFERENCES recommendation_batches(id) ON DELETE RESTRICT,
                    symbol TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    holding_day INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (batch_id, symbol, trade_date)
                );
                CREATE INDEX IF NOT EXISTS idx_daily_reviews_trade_date
                    ON daily_reviews(trade_date DESC);
                CREATE TABLE IF NOT EXISTS stage_reviews (
                    batch_id TEXT NOT NULL REFERENCES recommendation_batches(id) ON DELETE RESTRICT,
                    symbol TEXT NOT NULL,
                    stage_day INTEGER NOT NULL,
                    trade_date TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (batch_id, symbol, stage_day)
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    as_of TEXT,
                    trigger TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    result_json TEXT NOT NULL,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_started
                    ON agent_runs(started_at DESC);
                """
            )
            current = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if current is None:
                conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
                    (str(_SCHEMA_VERSION),),
                )
            elif int(current["value"]) != _SCHEMA_VERSION:
                raise RuntimeError(f"不支持的研究 Agent 数据库版本: {current['value']}")

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    @staticmethod
    def _load_json(value: str) -> Any:
        return json.loads(value)

    def save_batch(self, batch: RecommendationBatch | dict[str, Any]) -> dict[str, Any]:
        item = (
            batch
            if isinstance(batch, RecommendationBatch)
            else RecommendationBatch.model_validate(batch)
        )
        batch_id = item.id or f"rab_{uuid.uuid4().hex}"
        created_at = item.created_at.isoformat() if item.created_at else self._now()
        payload = item.model_dump(mode="json")
        payload.update({"id": batch_id, "created_at": created_at})
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO recommendation_batches(
                            id, as_of, created_at, trigger, version, parent_batch_id,
                            model, prompt_version, screening_version, market_snapshot_json,
                            screen_summary_json, candidates_json, status, message
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            batch_id,
                            item.as_of.isoformat(),
                            created_at,
                            item.trigger,
                            item.version,
                            item.parent_batch_id,
                            item.model,
                            item.prompt_version,
                            item.screening_version,
                            self._json(item.market_snapshot),
                            self._json(item.screen_summary),
                            self._json(item.candidates),
                            item.status,
                            item.message,
                        ),
                    )
                    for position, pick in enumerate(item.picks, start=1):
                        pick_payload = pick.model_dump(mode="json")
                        conn.execute(
                            """
                            INSERT INTO recommendations(batch_id, position, symbol, name, payload_json)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (batch_id, position, pick.symbol, pick.name, self._json(pick_payload)),
                        )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"推荐批次写入冲突: {exc}") from exc
        return payload

    def _batch_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        picks = [
            self._load_json(item["payload_json"])
            for item in conn.execute(
                "SELECT payload_json FROM recommendations WHERE batch_id = ? ORDER BY position",
                (row["id"],),
            ).fetchall()
        ]
        return {
            "id": row["id"],
            "as_of": row["as_of"],
            "created_at": row["created_at"],
            "trigger": row["trigger"],
            "version": row["version"],
            "parent_batch_id": row["parent_batch_id"],
            "model": row["model"],
            "prompt_version": row["prompt_version"],
            "screening_version": row["screening_version"],
            "market_snapshot": self._load_json(row["market_snapshot_json"]),
            "screen_summary": self._load_json(row["screen_summary_json"]),
            "candidates": self._load_json(row["candidates_json"]),
            "picks": picks,
            "status": row["status"],
            "message": row["message"],
        }

    def latest_batch(self, *, as_of: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM recommendation_batches"
        params: tuple[Any, ...] = ()
        if as_of:
            query += " WHERE as_of = ?"
            params = (as_of,)
        query += " ORDER BY as_of DESC, version DESC, created_at DESC LIMIT 1"
        with self._lock, self._connect() as conn:
            row = conn.execute(query, params).fetchone()
            return self._batch_from_row(conn, row) if row else None

    def list_batches(self, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 100)
        safe_offset = max(offset, 0)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM recommendation_batches
                ORDER BY as_of DESC, version DESC, created_at DESC LIMIT ? OFFSET ?
                """,
                (safe_limit, safe_offset),
            ).fetchall()
            return [self._batch_from_row(conn, row) for row in rows]

    def list_batches_before(self, as_of: str) -> list[dict[str, Any]]:
        """Return historical batches with an unfinished 20-day trajectory or stage."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT batch.* FROM recommendation_batches AS batch
                WHERE batch.as_of < ?
                  AND EXISTS (
                    SELECT 1 FROM recommendations AS pick
                    WHERE pick.batch_id = batch.id
                      AND (
                        NOT EXISTS (
                          SELECT 1 FROM daily_reviews AS daily
                          WHERE daily.batch_id = batch.id
                            AND daily.symbol = pick.symbol
                            AND daily.holding_day >= 20
                        )
                        OR EXISTS (
                          SELECT 1 FROM daily_reviews AS stage_daily
                          WHERE stage_daily.batch_id = batch.id
                            AND stage_daily.symbol = pick.symbol
                            AND stage_daily.holding_day IN (5, 10, 20)
                            AND NOT EXISTS (
                              SELECT 1 FROM stage_reviews AS stage
                              WHERE stage.batch_id = stage_daily.batch_id
                                AND stage.symbol = stage_daily.symbol
                                AND stage.stage_day = stage_daily.holding_day
                            )
                        )
                      )
                  )
                ORDER BY batch.as_of, batch.version, batch.created_at
                """,
                (as_of,),
            ).fetchall()
            return [self._batch_from_row(conn, row) for row in rows]

    def save_daily_review(self, review: DailyReview | dict[str, Any]) -> dict[str, Any]:
        item = review if isinstance(review, DailyReview) else DailyReview.model_validate(review)
        payload = item.model_dump(mode="json")
        created_at = item.created_at.isoformat() if item.created_at else self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_reviews(
                    batch_id, symbol, trade_date, holding_day, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id, symbol, trade_date) DO UPDATE SET
                    holding_day = excluded.holding_day,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    item.batch_id,
                    item.symbol,
                    item.trade_date.isoformat(),
                    item.holding_day,
                    self._json(payload),
                    created_at,
                    self._now(),
                ),
            )
        return payload

    def list_reviews(
        self,
        *,
        batch_id: str | None = None,
        symbol: str | None = None,
        trade_date: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for field, value in (
            ("batch_id", batch_id),
            ("symbol", symbol),
            ("trade_date", trade_date),
        ):
            if value:
                clauses.append(f"{field} = ?")
                params.append(value)
        query = "SELECT payload_json FROM daily_reviews"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY trade_date DESC, symbol"
        with self._lock, self._connect() as conn:
            return [
                self._load_json(row["payload_json"])
                for row in conn.execute(query, params).fetchall()
            ]

    def save_stage_review(self, review: StageReview | dict[str, Any]) -> dict[str, Any]:
        item = review if isinstance(review, StageReview) else StageReview.model_validate(review)
        payload = item.model_dump(mode="json")
        created_at = item.created_at.isoformat() if item.created_at else self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO stage_reviews(
                    batch_id, symbol, stage_day, trade_date, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id, symbol, stage_day) DO UPDATE SET
                    trade_date = excluded.trade_date,
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at
                """,
                (
                    item.batch_id,
                    item.symbol,
                    item.stage_day,
                    item.trade_date.isoformat(),
                    self._json(payload),
                    created_at,
                ),
            )
        return payload

    def list_stage_reviews(self, *, batch_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT payload_json FROM stage_reviews"
        params: tuple[Any, ...] = ()
        if batch_id:
            query += " WHERE batch_id = ?"
            params = (batch_id,)
        query += " ORDER BY trade_date DESC, symbol, stage_day"
        with self._lock, self._connect() as conn:
            return [
                self._load_json(row["payload_json"])
                for row in conn.execute(query, params).fetchall()
            ]

    def record_run(
        self,
        *,
        kind: str,
        trigger: str,
        status: str,
        as_of: str | None = None,
        run_id: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        finished: bool = False,
    ) -> dict[str, Any]:
        resolved_id = run_id or f"rar_{uuid.uuid4().hex}"
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs(
                    id, kind, as_of, trigger, status, started_at, finished_at, result_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    as_of = COALESCE(excluded.as_of, agent_runs.as_of),
                    status = excluded.status,
                    finished_at = excluded.finished_at,
                    result_json = excluded.result_json,
                    error = excluded.error
                """,
                (
                    resolved_id,
                    kind,
                    as_of,
                    trigger,
                    status,
                    now,
                    now if finished else None,
                    self._json(result or {}),
                    error,
                ),
            )
        return {"id": resolved_id, "status": status, "as_of": as_of}

    def get_status(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return {
                    "running": False,
                    "last_run": None,
                    "last_successful_at": None,
                    "degraded_reason": None,
                }
            successful = conn.execute(
                """
                SELECT finished_at FROM agent_runs
                WHERE status = 'succeeded' AND finished_at IS NOT NULL
                ORDER BY finished_at DESC LIMIT 1
                """
            ).fetchone()
            result = self._load_json(row["result_json"])
            degraded_reason = None
            if row["status"] in {"degraded", "failed", "cancelled"}:
                degraded_reason = row["error"] or result.get("message")
            return {
                "running": row["status"] in {"pending", "running"},
                "last_successful_at": successful["finished_at"] if successful else None,
                "degraded_reason": degraded_reason,
                "last_run": {
                    "id": row["id"],
                    "kind": row["kind"],
                    "as_of": row["as_of"],
                    "trigger": row["trigger"],
                    "status": row["status"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "result": result,
                    "error": row["error"],
                },
            }
