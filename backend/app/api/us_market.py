"""美股聚合看板 API。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.services.us_market_overview import UsMarketOverviewService, UsMarketUnavailableError

router = APIRouter(prefix="/api/us-market", tags=["us-market"])


def _service(request: Request) -> UsMarketOverviewService:
    service = getattr(request.app.state, "us_market_overview_service", None)
    if not isinstance(service, UsMarketOverviewService):
        raise HTTPException(status_code=503, detail="美股看板服务未初始化")
    return service


def _get_overview(request: Request, *, force: bool) -> dict[str, Any]:
    try:
        return _service(request).get_overview(force=force)
    except UsMarketUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/overview")
def get_us_market_overview(request: Request) -> dict[str, Any]:
    return _get_overview(request, force=False)


@router.post("/refresh")
def refresh_us_market_overview(request: Request) -> dict[str, Any]:
    return _get_overview(request, force=True)
