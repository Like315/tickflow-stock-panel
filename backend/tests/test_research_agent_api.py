from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.research_agent import router


class FakeStore:
    def latest_batch(self, as_of=None):
        return {"id": "rab_1", "as_of": as_of or "2026-08-11"}

    def list_batches(self, limit=20, offset=0):
        return [{"id": "rab_1", "limit": limit, "offset": offset}]

    def list_reviews(self, **filters):
        return [{"symbol": filters.get("symbol") or "600000.SH"}]

    def list_stage_reviews(self, batch_id=None):
        return [{"batch_id": batch_id, "stage_day": 5}]


class FakeService:
    store = FakeStore()

    async def chat_stream(self, question, symbol=None):
        yield json.dumps({"type": "delta", "content": question}, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "done"}, ensure_ascii=False) + "\n"

    async def run_recommendations(self, force=False, trigger="manual"):
        return {"status": "succeeded", "force": force, "trigger": trigger}

    async def run_daily_reviews(self, trigger="manual"):
        return {"status": "succeeded", "trigger": trigger}

    def submit_daily_cycle(self, trigger="manual"):
        return {"status": "started", "trigger": trigger}

    def status(self):
        return {"running": False, "ai_configured": True}


def _client() -> TestClient:
    app = FastAPI()
    app.state.research_agent_service = FakeService()
    app.include_router(router)
    return TestClient(app)


def test_terms_and_stream_chat() -> None:
    client = _client()
    terms = client.get("/api/research-agent/terms?q=MACD金叉").json()
    assert terms["term"]["id"] == "macd_golden"
    response = client.post("/api/research-agent/chat", json={"question": "分析 600000"})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["type"] == "done"


def test_recommendation_review_and_status_contracts() -> None:
    client = _client()
    assert client.get("/api/research-agent/recommendations/latest").json()["batch"]["id"] == "rab_1"
    assert client.get("/api/research-agent/recommendations?limit=5&offset=2").json()["batches"][0]["offset"] == 2
    run = client.post("/api/research-agent/recommendations/run", json={"force": True}).json()
    assert run == {"status": "succeeded", "force": True, "trigger": "manual"}
    reviews = client.get("/api/research-agent/reviews?batch_id=rab_1&symbol=600000.sh").json()
    assert reviews["reviews"][0]["symbol"] == "600000.SH"
    assert client.post("/api/research-agent/daily/run").json()["status"] == "started"
    assert client.get("/api/research-agent/status").json()["ai_configured"] is True
