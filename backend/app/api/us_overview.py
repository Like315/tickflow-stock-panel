"""美股市场总览 API。"""
from __future__ import annotations

from fastapi import APIRouter

from app.services.us_market_service import build_us_market_overview

router = APIRouter(prefix="/api/us", tags=["us-overview"])


@router.get("/overview/market")
async def us_market_overview():
    """美股市场总览 — 指数、广度、板块、涨跌排行。

    数据源: Yahoo Finance 公开 API, 不依赖 TickFlow SDK。
    """
    return await build_us_market_overview()
