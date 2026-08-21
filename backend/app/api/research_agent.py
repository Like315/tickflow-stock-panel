"""AI 研究 Agent HTTP API。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app.services.research_agent_terms import find_term, list_terms

router = APIRouter(prefix="/api/research-agent", tags=["research-agent"])


def _service(request: Request):
    return request.app.state.research_agent_service


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    symbol: str | None = Field(default=None, max_length=32)
    context: str = Field(default="general", pattern=r"^(general|fund_portfolio|fund|fund_market)$")
    fund_code: str | None = Field(default=None, pattern=r"^\d{6}$")

    @model_validator(mode="after")
    def validate_fund_context(self):
        if self.context == "fund" and not self.fund_code:
            raise ValueError("单基金研究需要提供 6 位基金代码")
        return self


class RunRequest(BaseModel):
    force: bool = False


@router.get("/terms")
def terms(q: str | None = Query(default=None, max_length=100)) -> dict:
    if q:
        item = find_term(q)
        return {"term": item.model_dump(mode="json") if item else None}
    return {"terms": [item.model_dump(mode="json") for item in list_terms()]}


@router.post("/chat")
async def chat(request: Request, payload: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _service(request).chat_stream(
            payload.question.strip(),
            payload.symbol,
            context=payload.context,
            fund_code=payload.fund_code,
        ),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/recommendations/latest")
def latest_recommendation(request: Request, as_of: str | None = None) -> dict:
    return {"batch": _service(request).store.latest_batch(as_of=as_of)}


@router.get("/recommendations")
def recommendations(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    rows = _service(request).store.list_batches(limit=limit, offset=offset)
    return {"batches": rows, "limit": limit, "offset": offset}


@router.post("/recommendations/run")
async def run_recommendations(request: Request, payload: RunRequest) -> dict:
    return await _service(request).run_recommendations(force=payload.force, trigger="manual")


@router.get("/reviews")
def reviews(
    request: Request,
    batch_id: str | None = None,
    symbol: str | None = None,
    trade_date: str | None = None,
) -> dict:
    service = _service(request)
    return {
        "reviews": service.store.list_reviews(
            batch_id=batch_id,
            symbol=symbol.upper() if symbol else None,
            trade_date=trade_date,
        ),
        "stages": service.store.list_stage_reviews(batch_id=batch_id),
    }


@router.post("/reviews/run")
async def run_reviews(request: Request) -> dict:
    return await _service(request).run_daily_reviews(trigger="manual")


@router.post("/daily/run")
def run_daily_cycle(request: Request) -> dict:
    return _service(request).submit_daily_cycle(trigger="manual")


@router.get("/status")
def status(request: Request) -> dict:
    return _service(request).status()
