"""Fund portfolio APIs: local ledger, import preview, and quote refresh."""

from __future__ import annotations

import logging
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.services.fund_portfolio import FundPortfolioService, parse_csv_snapshot, parse_ocr_snapshot
from app.services.watchlist_ocr.provider import OcrProvider, get_ocr_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/funds", tags=["funds"])

_MAX_IMAGE_BYTES = 12 * 1024 * 1024
_MAX_CSV_BYTES = 2 * 1024 * 1024
_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/bmp", "image/gif"}
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
_OCR_LIMITER = anyio.CapacityLimiter(2)


class FundPositionIn(BaseModel):
    name: str = Field(default="", max_length=100)
    holding_amount: float | None = Field(default=None, ge=0)
    shares: float | None = Field(default=None, ge=0)
    cost_amount: float | None = Field(default=None, ge=0)
    holding_profit: float | None = None
    holding_profit_pct: float | None = None
    day_profit: float | None = None


class FundPositionWithCodeIn(FundPositionIn):
    code: str = Field(min_length=1, max_length=10)


class ImportConfirmIn(BaseModel):
    source: str = Field(default="manual", max_length=40)
    positions: list[FundPositionWithCodeIn] = Field(min_length=1, max_length=200)


class FundMarketResearchIn(BaseModel):
    codes: list[str] | None = Field(default=None, max_length=50)


def _service(request: Request) -> FundPortfolioService:
    service = getattr(request.app.state, "fund_portfolio_service", None)
    if service is None:
        raise HTTPException(503, "基金账户服务尚未初始化")
    return service


def _ocr_provider(request: Request) -> OcrProvider:
    return getattr(request.app.state, "fund_ocr_provider", None) or get_ocr_provider()


@router.get("/portfolio")
def get_portfolio(request: Request):
    return _service(request).get_portfolio()


@router.get("/lookup/{code}")
async def lookup_fund(code: str, request: Request):
    try:
        return await anyio.to_thread.run_sync(lambda: _service(request).lookup_fund(code))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/ocr-status")
def ocr_status(request: Request):
    provider = _ocr_provider(request)
    return {"provider": provider.name, "available": provider.available()}


@router.post("/import-preview")
async def import_preview(request: Request, file: Annotated[UploadFile, File()]):
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    filename = (file.filename or "").lower()
    is_image = content_type in _IMAGE_TYPES or filename.endswith(_IMAGE_EXTENSIONS)
    is_csv = content_type in {
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
        "text/plain",
    } or filename.endswith(".csv")
    if not is_image and not is_csv:
        raise HTTPException(400, "仅支持 PNG/JPG/WebP/BMP/GIF 截图或 CSV 文件")
    limit = _MAX_IMAGE_BYTES if is_image else _MAX_CSV_BYTES
    data = await file.read(limit + 1)
    if not data:
        raise HTTPException(400, "上传文件为空")
    if len(data) > limit:
        raise HTTPException(413, f"文件过大，最大允许 {limit // (1024 * 1024)}MB")

    try:
        if is_image:
            provider = _ocr_provider(request)
            if not provider.available():
                raise HTTPException(503, "本机 OCR 不可用，请安装 Tesseract 或改用 CSV")
            raw_text = await anyio.to_thread.run_sync(
                lambda: provider.extract_text(data),
                limiter=_OCR_LIMITER,
            )
            result = parse_ocr_snapshot(raw_text)
            return {"source": "alipay_screenshot", "provider": provider.name, **result}
        result = parse_csv_snapshot(data)
        return {"source": "csv", "provider": "csv", **result}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        logger.exception("fund import preview failed")
        raise HTTPException(500, f"导入预览失败：{exc}") from exc


@router.post("/import-confirm")
def import_confirm(request: Request, body: ImportConfirmIn):
    try:
        positions = [position.model_dump() for position in body.positions]
        return _service(request).replace_positions(positions, source=body.source)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/positions/{code}")
def upsert_position(code: str, body: FundPositionIn, request: Request):
    try:
        values: dict[str, Any] = body.model_dump(exclude_unset=True)
        return _service(request).upsert_position(code, values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/positions/{code}")
def delete_position(code: str, request: Request):
    try:
        return _service(request).delete_position(code)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "基金持仓不存在") from exc


@router.post("/refresh")
async def refresh_quotes(request: Request):
    return await anyio.to_thread.run_sync(_service(request).refresh_quotes)


@router.post("/research/run")
async def run_fund_market_research(request: Request, body: FundMarketResearchIn | None = None):
    """基于历史净值 + 大盘趋势的基金市场研究，不依赖本地持仓账本。

    若本地账本存在持仓，服务会自动将其纳入研究范围并标记 held，
    用于前端区分「我持有的」与「外部市场」。
    """
    service = getattr(request.app.state, "fund_market_research_service", None)
    if service is None:
        raise HTTPException(503, "基金市场研究服务尚未初始化")
    codes = body.codes if body else None
    try:
        return await anyio.to_thread.run_sync(lambda: service.run_research(codes=codes))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("fund market research failed")
        raise HTTPException(500, f"基金市场研究失败：{exc}") from exc
