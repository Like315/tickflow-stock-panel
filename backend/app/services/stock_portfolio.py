"""用户维护的股票持仓账本。"""
# ruff: noqa: RUF001, RUF002
from __future__ import annotations

import json
import os
import re
import threading
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

import polars as pl

from app.services.watchlist_ocr.pipeline import extract_codes

_SYMBOL_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_PRICE = Decimal("0.0001")
_QUANTITY = Decimal("0.0001")
_MONEY = Decimal("0.01")
_RATIO = Decimal("0.000001")
_OCR_NUMBER_RE = re.compile(r"[-+]?\d[\d,，]*(?:\.\d+)?")
_OCR_QUANTITY_ALIASES = ("持仓数量", "持股数量", "证券数量", "股票数量", "股份余额", "持仓", "数量")
_OCR_TOTAL_COST_ALIASES = ("持仓成本", "成本金额", "买入成本", "总成本")
_OCR_BUY_PRICE_ALIASES = ("成本价", "买入均价", "持仓均价", "摊薄成本价", "买入价")
_OCR_GENERIC_COST_ALIASES = ("成本",)


class StockPortfolioRepository(Protocol):
    def get_enriched_latest(self) -> tuple[pl.DataFrame, date | None]: ...

    def get_name_map(self, symbols: list[str] | None = None) -> dict[str, str]: ...

    def get_instruments_asset(self, asset_type: str) -> pl.DataFrame: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _decimal(value: Any, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field}必须是有效数字") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{field}必须大于 0")
    return number


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _float(value: Decimal | None, quantum: Decimal) -> float | None:
    if value is None:
        return None
    return float(value.quantize(quantum, rounding=ROUND_HALF_UP))


def _normalize_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if not _SYMBOL_RE.fullmatch(normalized):
        raise ValueError("股票代码格式无效")
    return normalized


def _ocr_number_after_alias(lines: list[str], aliases: tuple[str, ...]) -> Decimal | None:
    """读取标签同行右侧的第一个正数，避免把证券代码误当成持仓数据。"""
    for line in lines:
        compact = re.sub(r"\s+", "", line)
        for alias in aliases:
            index = compact.find(alias)
            if index < 0:
                continue
            match = _OCR_NUMBER_RE.search(compact[index + len(alias) :])
            if not match:
                continue
            try:
                value = Decimal(match.group(0).replace(",", "").replace("，", ""))
            except InvalidOperation:
                continue
            if value.is_finite() and value > 0:
                return value
    return None


def parse_stock_portfolio_ocr(
    text: str,
    code_to_symbol: dict[str, str],
    symbol_to_name: dict[str, str],
) -> dict[str, Any]:
    """从券商持仓截图 OCR 文本生成只读、可编辑的候选持仓。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    codes = extract_codes(text)
    if not codes:
        raise ValueError("没有识别到 6 位股票代码，请换一张清晰截图")

    candidates: list[dict[str, Any]] = []
    warnings = ["图片识别结果可能有误，请核对股票、数量和成本后再保存"]
    for code in codes:
        symbol = code_to_symbol.get(code)
        if not symbol:
            warnings.append(f"代码 {code} 未匹配到本地股票标的，已跳过")
            continue

        line_index = next((index for index, line in enumerate(lines) if code in re.sub(r"\s+", "", line)), -1)
        if line_index < 0:
            block = lines
        else:
            next_index = next(
                (
                    index
                    for index in range(line_index + 1, len(lines))
                    if any(other != code and other in re.sub(r"\s+", "", lines[index]) for other in codes)
                ),
                len(lines),
            )
            block = lines[line_index:next_index]

        quantity = _ocr_number_after_alias(block, _OCR_QUANTITY_ALIASES)
        total_cost = _ocr_number_after_alias(block, _OCR_TOTAL_COST_ALIASES)
        buy_price = _ocr_number_after_alias(block, _OCR_BUY_PRICE_ALIASES)
        if buy_price is None and total_cost is None:
            buy_price = _ocr_number_after_alias(block, _OCR_GENERIC_COST_ALIASES)
        if total_cost is None and quantity is not None and buy_price is not None:
            total_cost = quantity * buy_price
        if buy_price is None and quantity is not None and total_cost is not None:
            buy_price = total_cost / quantity
        if quantity is None or total_cost is None:
            warnings.append(f"{symbol_to_name.get(symbol) or code} 未完整识别数量或成本，请手工补充")

        candidates.append({
            "code": code,
            "symbol": symbol,
            "name": symbol_to_name.get(symbol) or "",
            "quantity": _float(quantity, _QUANTITY),
            "cost_amount": _float(total_cost, _MONEY),
            "buy_price": _float(buy_price, _PRICE),
        })

    if not candidates:
        raise ValueError("图片中的股票代码未匹配到本地标的，请先同步标的列表")
    return {"candidates": candidates, "warnings": warnings}


class StockPortfolioService:
    """线程安全的本地股票持仓账本。行情只从标准化仓库快照读取。"""

    def __init__(self, data_dir: Path, repo: StockPortfolioRepository) -> None:
        self._path = Path(data_dir) / "user_data" / "stock_portfolio.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._repo = repo
        self._lock = threading.RLock()
        self._state = self._load()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"schema_version": 1, "updated_at": None, "positions": []}

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return self._empty()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("positions"), list):
                raise ValueError("invalid stock portfolio document")
            return {**self._empty(), **payload}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return self._empty()

    def _save(self) -> None:
        temporary = self._path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self._path)

    def _quote_snapshot(
        self,
        symbols: list[str],
    ) -> tuple[dict[str, dict[str, Any]], str | None]:
        if not symbols:
            return {}, None
        frame, price_date = self._repo.get_enriched_latest()
        if frame.is_empty() or "symbol" not in frame.columns:
            return {}, price_date.isoformat() if price_date else None
        columns = [column for column in ("symbol", "raw_close", "change_pct") if column in frame.columns]
        rows = (
            frame
            .filter(pl.col("symbol").is_in(symbols))
            .select(columns)
            .unique(subset=["symbol"], keep="last")
            .to_dicts()
        )
        return {str(row["symbol"]): row for row in rows}, price_date.isoformat() if price_date else None

    @staticmethod
    def _present_position(
        raw: dict[str, Any],
        quote: dict[str, Any] | None,
        price_date: str | None,
        canonical_name: str | None,
    ) -> dict[str, Any]:
        buy_price = _decimal(raw.get("buy_price"), "买入价格")
        quantity = _decimal(raw.get("quantity"), "数量")
        cost = buy_price * quantity
        current_price = _optional_decimal((quote or {}).get("raw_close"))
        if current_price is not None and current_price <= 0:
            current_price = None
        market_value = current_price * quantity if current_price is not None else None
        profit = market_value - cost if market_value is not None else None
        profit_pct = profit / cost if profit is not None and cost > 0 else None
        change_pct = _optional_decimal((quote or {}).get("change_pct"))
        return {
            **deepcopy(raw),
            "name": canonical_name or str(raw.get("name") or ""),
            "buy_price": _float(buy_price, _PRICE),
            "quantity": _float(quantity, _QUANTITY),
            "cost_amount": _float(cost, _MONEY),
            "current_price": _float(current_price, _PRICE),
            "market_value": _float(market_value, _MONEY),
            "profit_amount": _float(profit, _MONEY),
            "profit_pct": _float(profit_pct, _RATIO),
            "change_pct": _float(change_pct, _RATIO),
            "price_date": price_date if current_price is not None else None,
        }

    def get_portfolio(self) -> dict[str, Any]:
        with self._lock:
            state = deepcopy(self._state)
        symbols = [str(row.get("symbol") or "") for row in state["positions"]]
        quotes, price_date = self._quote_snapshot(symbols)
        try:
            names = self._repo.get_name_map(symbols)
        except Exception:
            names = {}
        positions = [
            self._present_position(row, quotes.get(row["symbol"]), price_date, names.get(row["symbol"]))
            for row in state["positions"]
        ]

        costs = [_optional_decimal(row.get("cost_amount")) for row in positions]
        markets = [_optional_decimal(row.get("market_value")) for row in positions]
        total_cost = sum((value for value in costs if value is not None), Decimal("0"))
        has_complete_prices = all(value is not None for value in markets)
        total_market = (
            sum((value for value in markets if value is not None), Decimal("0"))
            if has_complete_prices
            else None
        )
        total_profit = total_market - total_cost if total_market is not None else None
        profit_pct = total_profit / total_cost if total_profit is not None and total_cost > 0 else None
        return {
            "updated_at": state["updated_at"],
            "price_date": price_date,
            "summary": {
                "currency": "CNY",
                "position_count": len(positions),
                "total_cost_amount": _float(total_cost, _MONEY),
                "total_market_value": _float(total_market, _MONEY),
                "total_profit_amount": _float(total_profit, _MONEY),
                "profit_pct": _float(profit_pct, _RATIO),
            },
            "positions": positions,
        }

    def preview_ocr_text(self, text: str) -> dict[str, Any]:
        """匹配 OCR 代码到本地股票维表，并返回不落盘的导入预览。"""
        frame = self._repo.get_instruments_asset("stock")
        if frame.is_empty() or "symbol" not in frame.columns:
            raise ValueError("本地股票标的列表为空，请先同步标的列表")
        code_to_symbol: dict[str, str] = {}
        symbol_to_name: dict[str, str] = {}
        for row in frame.iter_rows(named=True):
            symbol = str(row.get("symbol") or "").strip().upper()
            if not _SYMBOL_RE.fullmatch(symbol):
                continue
            code = str(row.get("code") or symbol.split(".", 1)[0]).strip()
            if len(code) == 6 and code.isdigit():
                code_to_symbol.setdefault(code, symbol)
            name = str(row.get("name") or "").strip()
            if name:
                symbol_to_name.setdefault(symbol, name)
        return parse_stock_portfolio_ocr(text, code_to_symbol, symbol_to_name)

    def upsert_position(self, symbol: str, values: dict[str, Any]) -> dict[str, Any]:
        normalized_symbol = _normalize_symbol(symbol)
        buy_price = _decimal(values.get("buy_price"), "买入价格")
        quantity = _decimal(values.get("quantity"), "数量")
        name = str(values.get("name") or "").strip()[:100]
        now = _now()
        normalized = {
            "symbol": normalized_symbol,
            "name": name,
            "buy_price": _float(buy_price, _PRICE),
            "quantity": _float(quantity, _QUANTITY),
            "updated_at": now,
        }
        with self._lock:
            index = next(
                (i for i, row in enumerate(self._state["positions"]) if row.get("symbol") == normalized_symbol),
                None,
            )
            if index is None:
                normalized["created_at"] = now
                self._state["positions"].append(normalized)
            else:
                normalized["created_at"] = self._state["positions"][index].get("created_at", now)
                self._state["positions"][index] = normalized
            self._state["updated_at"] = now
            self._save()
        return self.get_portfolio()

    def delete_position(self, symbol: str) -> dict[str, Any]:
        normalized_symbol = _normalize_symbol(symbol)
        with self._lock:
            positions = [
                row for row in self._state["positions"]
                if row.get("symbol") != normalized_symbol
            ]
            if len(positions) == len(self._state["positions"]):
                raise KeyError(normalized_symbol)
            self._state["positions"] = positions
            self._state["updated_at"] = _now()
            self._save()
        return self.get_portfolio()
