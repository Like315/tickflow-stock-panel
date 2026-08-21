from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from app.services.research_agent_store import ResearchAgentStore


def _pick(symbol: str = "600000.SH") -> dict:
    return {
        "symbol": symbol,
        "name": "示例股票",
        "stance": "观察",
        "confidence": 60,
        "thesis": "趋势改善但需要确认",
        "evidence": [
            {
                "dimension": "技术面",
                "conclusion": "均线改善",
                "supports": ["收盘站上 MA20"],
                "source": "TickFlow enriched",
                "evidence_refs": ["technical.ma20"],
                "as_of": "2026-08-11",
            }
        ],
        "counter_evidence": ["量能未确认"],
        "risks": ["市场转弱"],
    }


def _batch(**patch) -> dict:
    value = {
        "id": "rab_test",
        "as_of": "2026-08-11",
        "trigger": "manual",
        "picks": [_pick()],
        "candidates": [{"symbol": "600000.SH", "score": 0.8}],
    }
    value.update(patch)
    return value


def test_store_saves_and_reads_immutable_batch(tmp_path) -> None:
    store = ResearchAgentStore(tmp_path)
    saved = store.save_batch(_batch())
    assert saved["id"] == "rab_test"
    assert store.path == tmp_path / "user_data" / "ai_research_agent.db"
    latest = store.latest_batch()
    assert latest["as_of"] == "2026-08-11"
    assert latest["picks"][0]["symbol"] == "600000.SH"

    with pytest.raises(ValueError, match="写入冲突"):
        store.save_batch(_batch(message="不得覆盖"))
    assert store.latest_batch()["message"] is None


def test_batch_write_rolls_back_when_pick_symbols_repeat(tmp_path) -> None:
    store = ResearchAgentStore(tmp_path)
    bad = _batch(id="rab_bad", picks=[_pick(), _pick()])
    with pytest.raises(ValueError):
        store.save_batch(bad)

    assert store.latest_batch() is None
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == 0


def test_same_date_and_version_is_unique_across_batch_ids(tmp_path) -> None:
    store = ResearchAgentStore(tmp_path)
    store.save_batch(_batch())
    with pytest.raises(ValueError, match="写入冲突"):
        store.save_batch(_batch(id="rab_parallel"))


def test_daily_review_is_idempotent_and_queryable(tmp_path) -> None:
    store = ResearchAgentStore(tmp_path)
    store.save_batch(_batch())
    review = {
        "batch_id": "rab_test",
        "symbol": "600000.SH",
        "trade_date": "2026-08-12",
        "holding_day": 1,
        "daily_return": 0.01,
        "thesis_state": "维持",
    }
    store.save_daily_review(review)
    store.save_daily_review({**review, "daily_return": 0.02, "thesis_state": "增强"})
    rows = store.list_reviews(batch_id="rab_test", symbol="600000.SH")
    assert len(rows) == 1
    assert rows[0]["daily_return"] == 0.02
    assert rows[0]["thesis_state"] == "增强"


def test_parent_version_stage_and_run_status(tmp_path) -> None:
    store = ResearchAgentStore(tmp_path)
    store.save_batch(_batch())
    store.save_batch(_batch(id="rab_v2", version=2, parent_batch_id="rab_test"))
    assert store.latest_batch(as_of="2026-08-11")["id"] == "rab_v2"
    store.save_stage_review(
        {
            "batch_id": "rab_test",
            "symbol": "600000.SH",
            "stage_day": 5,
            "trade_date": date(2026, 8, 18),
            "summary": "阶段表现稳定",
            "thesis_state": "维持",
        }
    )
    assert store.list_stage_reviews(batch_id="rab_test")[0]["stage_day"] == 5

    run = store.record_run(kind="recommendation", trigger="manual", status="running")
    assert store.get_status()["running"] is True
    store.record_run(
        kind="recommendation",
        trigger="manual",
        status="succeeded",
        run_id=run["id"],
        result={"batch_id": "rab_v2"},
        finished=True,
    )
    status = store.get_status()
    assert status["running"] is False
    assert status["last_run"]["result"]["batch_id"] == "rab_v2"
