"""股票持仓 API。"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.services.stock_portfolio import StockPortfolioService
from app.services.watchlist_ocr.provider import OcrProvider, get_ocr_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stock-portfolio", tags=["stock-portfolio"])

_MAX_IMAGE_BYTES = 12 * 1024 * 1024
_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/bmp", "image/gif"}
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
_OCR_LIMITER = anyio.CapacityLimiter(2)


class StockPositionIn(BaseModel):
    name: str = Field(default="", max_length=100)
    buy_price: float = Field(gt=0)
    quantity: float = Field(gt=0)


def _service(request: Request) -> StockPortfolioService:
    service = getattr(request.app.state, "stock_portfolio_service", None)
    if service is None:
        raise HTTPException(503, "股票持仓服务尚未初始化")
    return service


def _ocr_provider(request: Request) -> OcrProvider:
    return getattr(request.app.state, "stock_ocr_provider", None) or get_ocr_provider()


@router.get("")
def get_portfolio(request: Request):
    return _service(request).get_portfolio()


@router.get("/ocr-status")
def ocr_status(request: Request):
    provider = _ocr_provider(request)
    return {"provider": provider.name, "available": provider.available()}


@router.post("/import-preview")
async def import_preview(request: Request, file: Annotated[UploadFile, File()]):
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    filename = (file.filename or "").lower()
    if content_type not in _IMAGE_TYPES and not filename.endswith(_IMAGE_EXTENSIONS):
        raise HTTPException(400, "仅支持 PNG/JPG/WebP/BMP/GIF 图片")
    data = await file.read(_MAX_IMAGE_BYTES + 1)
    if not data:
        raise HTTPException(400, "上传文件为空")
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(413, "图片过大，最大允许 12MB")

    provider = _ocr_provider(request)
    if not provider.available():
        raise HTTPException(503, "本机 OCR 不可用，请安装 Tesseract 后重试")
    try:
        raw_text = await anyio.to_thread.run_sync(
            lambda: provider.extract_text(data),
            limiter=_OCR_LIMITER,
        )
        return {"provider": provider.name, **_service(request).preview_ocr_text(raw_text)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        logger.exception("stock portfolio import preview failed")
        raise HTTPException(500, f"图片识别失败：{exc}") from exc


@router.put("/positions/{symbol}")
def upsert_position(symbol: str, body: StockPositionIn, request: Request):
    try:
        values: dict[str, Any] = body.model_dump()
        return _service(request).upsert_position(symbol, values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/positions/{symbol}")
def delete_position(symbol: str, request: Request):
    try:
        return _service(request).delete_position(symbol)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "股票持仓不存在") from exc
