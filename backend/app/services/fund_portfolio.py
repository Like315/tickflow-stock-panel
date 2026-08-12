"""Local fund portfolio ledger, snapshot import parsers, and quote refresh."""
# ruff: noqa: RUF001
from __future__ import annotations

import csv
import json
import logging
import os
import re
import threading
from copy import deepcopy
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import StringIO
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_MONEY = Decimal("0.01")
_SHARES = Decimal("0.0001")
_NAV = Decimal("0.0001")
_PERCENT = Decimal("0.01")
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _now() -> str:
    return datetime.now(_SHANGHAI).isoformat(timespec="seconds")


def _float(value: Decimal | None, quantum: Decimal | None = None) -> float | None:
    if value is None:
        return None
    if quantum is not None:
        value = value.quantize(quantum, rounding=ROUND_HALF_UP)
    return float(value)


def parse_localized_number(value: Any) -> Decimal | None:
    """Parse money/share/percentage text while preserving decimal semantics."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int | float):
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None

    text = str(value).strip()
    if not text or text in {"--", "-", "暂无", "无"}:
        return None
    negative = (text.startswith("(") and text.endswith(")")) or (
        text.startswith("（") and text.endswith("）")
    )
    multiplier = Decimal("100000000") if "亿" in text else Decimal("10000") if "万" in text else Decimal("1")
    match = _NUMBER_RE.search(text.replace("，", ","))
    if not match:
        return None
    try:
        parsed = Decimal(match.group(0).replace(",", "")) * multiplier
    except InvalidOperation:
        return None
    if negative:
        parsed = -abs(parsed)
    return parsed if parsed.is_finite() else None


def _decode_csv(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 编码无法识别，请使用 UTF-8 或 GB18030")


_CSV_ALIASES = {
    "code": {"基金代码", "代码", "fund_code", "code"},
    "name": {"基金名称", "名称", "fund_name", "name"},
    "holding_amount": {"持有金额", "持仓金额", "持有市值", "市值", "holding_amount", "market_value"},
    "shares": {"持有份额", "持仓份额", "份额", "shares"},
    "cost_amount": {"持仓成本", "成本金额", "持有成本", "cost_amount", "cost"},
    "holding_profit": {"持有收益", "累计收益", "持仓收益", "holding_profit", "profit"},
    "holding_profit_pct": {"持有收益率", "收益率", "holding_profit_pct", "profit_pct"},
    "day_profit": {"昨日收益", "当日收益", "今日收益", "day_profit", "daily_profit"},
}


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit() and len(text) <= 6:
        text = text.zfill(6)
    if not _CODE_RE.fullmatch(text):
        raise ValueError("基金代码必须是 6 位数字")
    return text


def _candidate_from_mapping(row: dict[str, Any]) -> dict[str, Any]:
    code = _normalize_code(row.get("code"))
    candidate: dict[str, Any] = {
        "code": code,
        "name": str(row.get("name") or "").strip()[:100],
    }
    for field in (
        "holding_amount",
        "shares",
        "cost_amount",
        "holding_profit",
        "holding_profit_pct",
        "day_profit",
    ):
        value = parse_localized_number(row.get(field))
        quantum = _SHARES if field == "shares" else _PERCENT if field.endswith("pct") else _MONEY
        candidate[field] = _float(value, quantum)
    return candidate


def parse_csv_snapshot(payload: bytes) -> dict[str, Any]:
    """Parse a user-exported fund CSV into a write-free confirmation preview."""
    text = _decode_csv(payload)
    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV 缺少表头")
    normalized_headers = {str(header).strip().lower(): header for header in reader.fieldnames if header}
    column_map: dict[str, str] = {}
    for target, aliases in _CSV_ALIASES.items():
        for alias in aliases:
            original = normalized_headers.get(alias.lower())
            if original is not None:
                column_map[target] = original
                break
    if "code" not in column_map:
        raise ValueError("CSV 缺少“基金代码”列")
    if not ({"holding_amount", "shares"} & column_map.keys()):
        raise ValueError("CSV 至少需要“持有金额”或“持有份额”列")

    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for row_number, raw in enumerate(reader, start=2):
        mapped = {target: raw.get(source) for target, source in column_map.items()}
        if not any(str(value or "").strip() for value in mapped.values()):
            continue
        try:
            candidate = _candidate_from_mapping(mapped)
        except ValueError as exc:
            warnings.append(f"第 {row_number} 行已跳过：{exc}")
            continue
        if candidate["code"] in seen:
            warnings.append(f"第 {row_number} 行基金 {candidate['code']} 重复，已跳过")
            continue
        if candidate["holding_amount"] is None and candidate["shares"] is None:
            warnings.append(f"第 {row_number} 行缺少持有金额或份额，已跳过")
            continue
        seen.add(candidate["code"])
        candidates.append(candidate)
    if not candidates:
        raise ValueError("CSV 中没有可导入的基金持仓")
    return {"candidates": candidates, "warnings": warnings}


_OCR_FIELDS = {
    "holding_amount": ("持有金额", "持仓金额", "持有市值"),
    "shares": ("持有份额", "持仓份额"),
    "cost_amount": ("持仓成本", "成本金额", "持有成本"),
    "holding_profit_pct": ("持有收益率", "累计收益率"),
    "holding_profit": ("持有收益", "累计收益", "持仓收益"),
    "day_profit": ("昨日收益", "当日收益", "今日收益"),
}


def _extract_ocr_value(lines: list[str], aliases: tuple[str, ...]) -> Decimal | None:
    for line in lines:
        compact = re.sub(r"\s+", "", line)
        for alias in aliases:
            index = compact.find(alias)
            if index >= 0:
                value = parse_localized_number(compact[index + len(alias) :])
                if value is not None:
                    return value
    return None


def _looks_like_fund_name(line: str) -> bool:
    if _CODE_RE.search(line) or any(label in line for labels in _OCR_FIELDS.values() for label in labels):
        return False
    if any(word in line for word in ("支付宝", "资产", "基金代码", "详情", "更新于")):
        return False
    return len(line) >= 3 and bool(re.search(r"[\u4e00-\u9fff]", line))


def parse_ocr_snapshot(text: str) -> dict[str, Any]:
    """Extract conservative, editable candidates from OCR text."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    code_rows = [(index, match.group(1)) for index, line in enumerate(lines) if (match := _CODE_RE.search(line))]
    if not code_rows:
        raise ValueError("没有识别到 6 位基金代码，请换一张清晰截图或使用 CSV")

    candidates: list[dict[str, Any]] = []
    warnings = ["截图识别结果可能有误，请核对金额、份额和收益后再确认同步"]
    seen: set[str] = set()
    for code_index, (line_index, code) in enumerate(code_rows):
        if code in seen:
            continue
        next_index = code_rows[code_index + 1][0] if code_index + 1 < len(code_rows) else len(lines)
        block = lines[line_index:next_index]
        name = ""
        for previous in reversed(lines[max(0, line_index - 3) : line_index]):
            if _looks_like_fund_name(previous):
                name = previous
                break
        mapped: dict[str, Any] = {"code": code, "name": name}
        for field, aliases in _OCR_FIELDS.items():
            mapped[field] = _extract_ocr_value(block, aliases)
        candidate = _candidate_from_mapping(mapped)
        if candidate["holding_amount"] is None and candidate["shares"] is None:
            warnings.append(f"基金 {code} 未识别到金额或份额，需要手工补充")
        candidates.append(candidate)
        seen.add(code)
    return {"candidates": candidates, "warnings": warnings}


class FundQuoteProvider(Protocol):
    name: str

    def fetch_quote(self, code: str) -> dict[str, Any]: ...

    def close(self) -> None: ...


class EastmoneyFundQuoteProvider:
    """Best-effort public valuation adapter; no user account data is involved."""

    name = "eastmoney_public_fund_data"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=8.0,
            headers={
                "User-Agent": "Mozilla/5.0 TickFlowFundPortfolio/1.0",
                "Referer": "https://fund.eastmoney.com/",
            },
        )

    def fetch_quote(self, code: str) -> dict[str, Any]:
        code = _normalize_code(code)
        name = ""
        estimated_nav = None
        estimated_change_pct = None
        quote_time = None
        try:
            estimate_response = self._client.get(
                f"https://fundgz.1234567.com.cn/js/{code}.js",
            )
            estimate_response.raise_for_status()
            match = re.search(r"jsonpgz\((\{.*\})\)\s*;?", estimate_response.text.strip())
            if match:
                estimate = json.loads(match.group(1))
                if str(estimate.get("fundcode") or "") == code:
                    name = str(estimate.get("name") or "").strip()
                    estimated_nav = parse_localized_number(estimate.get("gsz"))
                    estimated_change_pct = parse_localized_number(estimate.get("gszzl"))
                    quote_time = str(estimate.get("gztime") or "") or None
        except Exception as exc:
            logger.info("intraday fund estimate unavailable for %s: %s", code, exc)

        official_response = self._client.get(
            "https://api.fund.eastmoney.com/f10/lsjz",
            params={"fundCode": code, "pageIndex": 1, "pageSize": 1},
            headers={"Referer": f"https://fundf10.eastmoney.com/jjjz_{code}.html"},
        )
        official_response.raise_for_status()
        document = official_response.json()
        rows = ((document.get("Data") or {}).get("LSJZList") or [])
        if int(document.get("ErrCode") or 0) != 0 or not rows:
            raise RuntimeError("该基金暂无正式净值数据")
        latest = rows[0]
        official_nav = parse_localized_number(latest.get("DWJZ"))
        official_change_pct = parse_localized_number(latest.get("JZZZL"))
        if official_nav is None:
            raise RuntimeError("行情源未返回可解析的正式净值")
        if not name:
            try:
                search_response = self._client.get(
                    "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx",
                    params={"m": 1, "key": code},
                )
                search_response.raise_for_status()
                matches = search_response.json().get("Datas") or []
                exact = next(
                    (row for row in matches if str(row.get("CODE") or "") == code),
                    None,
                )
                if exact:
                    name = str(exact.get("NAME") or "").strip()
            except Exception as exc:
                logger.info("fund name lookup unavailable for %s: %s", code, exc)
        return {
            "name": name,
            "official_nav": _float(official_nav, _NAV),
            "official_nav_date": str(latest.get("FSRQ") or "") or None,
            "estimated_nav": _float(estimated_nav, _NAV),
            "estimated_change_pct": _float(
                estimated_change_pct if estimated_nav is not None else official_change_pct,
                _PERCENT,
            ),
            "quote_time": quote_time,
            "quote_source": self.name,
        }

    def close(self) -> None:
        self._client.close()


def _normalized_position(raw: dict[str, Any], *, require_holding: bool = True) -> dict[str, Any]:
    candidate = _candidate_from_mapping(raw)
    if require_holding and candidate["holding_amount"] is None and candidate["shares"] is None:
        raise ValueError(f"基金 {candidate['code']} 至少填写持有金额或持有份额")
    for field in (
        "official_nav",
        "estimated_nav",
        "estimated_change_pct",
        "quote_time",
        "quote_source",
        "quote_status",
        "official_nav_date",
        "day_profit_estimated",
        "updated_at",
    ):
        if field in raw:
            candidate[field] = raw[field]
    if candidate["cost_amount"] is None and candidate["holding_amount"] is not None and candidate["holding_profit"] is not None:
        candidate["cost_amount"] = _float(
            Decimal(str(candidate["holding_amount"])) - Decimal(str(candidate["holding_profit"])),
            _MONEY,
        )
    candidate["updated_at"] = str(candidate.get("updated_at") or _now())
    return candidate


class FundPortfolioService:
    """Thread-safe local ledger for a single user-managed fund snapshot."""

    def __init__(self, data_dir: Path, quote_provider: FundQuoteProvider | None = None) -> None:
        self._path = Path(data_dir) / "user_data" / "fund_portfolio.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._quote_provider = quote_provider or EastmoneyFundQuoteProvider()
        self._state = self._load()

    def _empty(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source": None,
            "synced_at": None,
            "quotes_refreshed_at": None,
            "positions": [],
        }

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return self._empty()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("positions"), list):
                raise ValueError("invalid fund portfolio document")
            return {**self._empty(), **payload}
        except Exception:
            logger.exception("failed to read fund portfolio, starting with an empty ledger")
            return self._empty()

    def _save(self) -> None:
        temporary = self._path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self._path)

    @staticmethod
    def _present_position(raw: dict[str, Any]) -> dict[str, Any]:
        position = deepcopy(raw)
        shares = parse_localized_number(position.get("shares"))
        estimate_nav = parse_localized_number(position.get("estimated_nav"))
        official_nav = parse_localized_number(position.get("official_nav"))
        imported_amount = parse_localized_number(position.get("holding_amount"))
        current_nav = estimate_nav or official_nav
        market_value = shares * current_nav if shares is not None and current_nav is not None else imported_amount
        cost = parse_localized_number(position.get("cost_amount"))
        profit = market_value - cost if market_value is not None and cost is not None else parse_localized_number(position.get("holding_profit"))
        profit_pct = profit / cost * 100 if profit is not None and cost is not None and cost > 0 else parse_localized_number(position.get("holding_profit_pct"))
        position["market_value"] = _float(market_value, _MONEY)
        position["holding_profit"] = _float(profit, _MONEY)
        position["holding_profit_pct"] = _float(profit_pct, _PERCENT)
        return position

    def get_portfolio(self) -> dict[str, Any]:
        with self._lock:
            state = deepcopy(self._state)
        positions = [self._present_position(position) for position in state["positions"]]
        market_values = [parse_localized_number(row.get("market_value")) for row in positions]
        costs = [parse_localized_number(row.get("cost_amount")) for row in positions]
        profits = [parse_localized_number(row.get("holding_profit")) for row in positions]
        day_profits = [parse_localized_number(row.get("day_profit")) for row in positions]
        total_market = sum((value for value in market_values if value is not None), Decimal("0"))
        total_cost = sum((value for value in costs if value is not None), Decimal("0"))
        total_profit = sum((value for value in profits if value is not None), Decimal("0"))
        total_day_profit = sum((value for value in day_profits if value is not None), Decimal("0"))
        total_pct = total_profit / total_cost * 100 if total_cost > 0 else None
        return {
            "source": state["source"],
            "synced_at": state["synced_at"],
            "quotes_refreshed_at": state["quotes_refreshed_at"],
            "summary": {
                "currency": "CNY",
                "position_count": len(positions),
                "total_market_value": _float(total_market, _MONEY),
                "total_cost_amount": _float(total_cost, _MONEY),
                "total_holding_profit": _float(total_profit, _MONEY),
                "holding_profit_pct": _float(total_pct, _PERCENT),
                "total_day_profit": _float(total_day_profit, _MONEY),
            },
            "positions": positions,
        }

    def replace_positions(self, positions: list[dict[str, Any]], *, source: str) -> dict[str, Any]:
        if not positions:
            raise ValueError("至少需要一条基金持仓")
        if len(positions) > 200:
            raise ValueError("单次最多同步 200 只基金")
        normalized = [_normalized_position(position) for position in positions]
        codes = [position["code"] for position in normalized]
        if len(codes) != len(set(codes)):
            raise ValueError("基金代码不能重复")
        now = _now()
        for position in normalized:
            position["updated_at"] = now
            position["day_profit_estimated"] = False
        with self._lock:
            self._state = {
                **self._empty(),
                "source": str(source or "manual")[:40],
                "synced_at": now,
                "positions": normalized,
            }
            self._save()
        return self.get_portfolio()

    def upsert_position(self, code: str, values: dict[str, Any]) -> dict[str, Any]:
        normalized_code = _normalize_code(code)
        with self._lock:
            index = next((i for i, row in enumerate(self._state["positions"]) if row["code"] == normalized_code), None)
            existing = self._state["positions"][index] if index is not None else {}
            merged = {**existing, **values, "code": normalized_code, "updated_at": _now()}
            normalized = _normalized_position(merged)
            if index is None:
                self._state["positions"].append(normalized)
            else:
                self._state["positions"][index] = normalized
            self._state["source"] = "manual"
            self._state["synced_at"] = _now()
            self._save()
        return self.get_portfolio()

    def delete_position(self, code: str) -> dict[str, Any]:
        normalized_code = _normalize_code(code)
        with self._lock:
            positions = [row for row in self._state["positions"] if row["code"] != normalized_code]
            if len(positions) == len(self._state["positions"]):
                raise KeyError(normalized_code)
            self._state["positions"] = positions
            self._state["synced_at"] = _now()
            self._save()
        return self.get_portfolio()

    def refresh_quotes(self) -> dict[str, Any]:
        with self._lock:
            codes = [position["code"] for position in self._state["positions"]]
        updated = 0
        failures: list[dict[str, str]] = []
        quotes: dict[str, dict[str, Any]] = {}
        now = _now()
        for code in codes:
            try:
                quotes[code] = self._quote_provider.fetch_quote(code)
                updated += 1
            except Exception as exc:
                logger.warning("fund quote refresh failed for %s: %s", code, exc)
                failures.append({"code": code, "message": str(exc)[:160]})
        with self._lock:
            # Quotes are fetched without holding the ledger lock. Merge them into the
            # latest positions so a concurrent manual edit or deletion is preserved.
            for position in self._state["positions"]:
                quote = quotes.get(position["code"])
                if quote is None:
                    continue
                for field in (
                    "official_nav",
                    "official_nav_date",
                    "estimated_nav",
                    "estimated_change_pct",
                    "quote_time",
                    "quote_source",
                ):
                    position[field] = quote.get(field)
                if quote.get("name") and not position.get("name"):
                    position["name"] = quote["name"]
                position["quote_status"] = "estimate" if quote.get("estimated_nav") is not None else "official"
                change_pct = parse_localized_number(quote.get("estimated_change_pct"))
                presented = self._present_position(position)
                market_value = parse_localized_number(presented.get("market_value"))
                if market_value is not None and change_pct is not None and change_pct != -100:
                    position["day_profit"] = _float(
                        market_value * change_pct / (Decimal("100") + change_pct),
                        _MONEY,
                    )
                    position["day_profit_estimated"] = quote.get("estimated_nav") is not None
                position["updated_at"] = now
            self._state["quotes_refreshed_at"] = now
            self._save()
        return {
            "refresh": {"updated": updated, "failed": len(failures), "failures": failures},
            "portfolio": self.get_portfolio(),
        }

    def lookup_fund(self, code: str) -> dict[str, str]:
        if not re.fullmatch(r"\d{6}", str(code).strip()):
            raise ValueError("基金代码必须是 6 位数字")
        normalized_code = _normalize_code(code)
        quote = self._quote_provider.fetch_quote(normalized_code)
        name = str(quote.get("name") or "").strip()
        if not name:
            raise ValueError(f"未查询到基金 {normalized_code} 的名称")
        return {"code": normalized_code, "name": name}

    def close(self) -> None:
        self._quote_provider.close()
