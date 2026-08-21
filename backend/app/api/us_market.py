"""美股聚合看板 API。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.us_market_instruments import (
    UsMarketHistoryUnavailableError,
    UsMarketInstrumentNotFoundError,
    UsMarketInstrumentService,
)
from app.services.us_market_overview import UsMarketOverviewService, UsMarketUnavailableError
from app.services.us_market_sectors import (
    UsMarketGroupNotFoundError,
    UsMarketSectorService,
    UsMarketSectorUnavailableError,
)

router = APIRouter(prefix="/api/us-market", tags=["us-market"])


def _service(request: Request) -> UsMarketOverviewService:
    service = getattr(request.app.state, "us_market_overview_service", None)
    if not isinstance(service, UsMarketOverviewService):
        raise HTTPException(status_code=503, detail="美股看板服务未初始化")
    return service


def _sector_service(request: Request) -> UsMarketSectorService:
    service = getattr(request.app.state, "us_market_sector_service", None)
    if not isinstance(service, UsMarketSectorService):
        raise HTTPException(status_code=503, detail="美股板块服务未初始化")
    return service


def _instrument_service(request: Request) -> UsMarketInstrumentService:
    service = getattr(request.app.state, "us_market_instrument_service", None)
    if not isinstance(service, UsMarketInstrumentService):
        raise HTTPException(status_code=503, detail="美股基础档案服务未初始化")
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


@router.get("/groups")
def list_us_market_groups(request: Request, force: bool = False) -> dict[str, Any]:
    try:
        return _sector_service(request).list_groups(force=force)
    except UsMarketUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/groups/{group_id}")
def get_us_market_group(
    request: Request,
    group_id: str,
    kind: Literal["sector", "theme"] = "sector",
    force: bool = False,
) -> dict[str, Any]:
    try:
        return _sector_service(request).get_detail(group_id, kind=kind, force=force)
    except UsMarketGroupNotFoundError as exc:
        raise HTTPException(status_code=404, detail="指定的美股板块不存在") from exc
    except (UsMarketUnavailableError, UsMarketSectorUnavailableError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/instruments")
def list_us_market_instruments(
    request: Request,
    q: str = "",
    sector: str = "",
    industry: str = "",
    country: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    force: bool = False,
) -> dict[str, Any]:
    return _instrument_service(request).list_instruments(
        query=q,
        sector=sector,
        industry=industry,
        country=country,
        limit=limit,
        offset=offset,
        force=force,
    )


@router.get("/rankings")
def get_us_market_rankings(
    request: Request,
    limit: int = Query(10, ge=1, le=100),
    force: bool = False,
) -> dict[str, Any]:
    return _instrument_service(request).get_rankings(limit=limit, force=force)


@router.get("/instruments/{symbol}/daily")
def get_us_market_instrument_daily(
    request: Request,
    symbol: str,
    count: int = Query(260, ge=10, le=5000),
    adjust: Literal["none", "forward", "backward"] = "none",
    force: bool = False,
) -> dict[str, Any]:
    try:
        return _instrument_service(request).get_daily(
            symbol,
            count=count,
            adjust=adjust,
            force=force,
        )
    except UsMarketInstrumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="美股代码格式无效") from exc
    except UsMarketHistoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/instruments/{symbol}")
def get_us_market_instrument(
    request: Request,
    symbol: str,
    force: bool = False,
) -> dict[str, Any]:
    try:
        return _instrument_service(request).get_instrument(symbol, force=force)
    except UsMarketInstrumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="指定的美股代码不存在") from exc
