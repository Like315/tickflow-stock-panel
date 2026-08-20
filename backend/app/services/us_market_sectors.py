"""美股行业分类、主题 ETF 持仓与指定板块聚合服务。"""
from __future__ import annotations

import copy
import json
import logging
import math
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path
from statistics import median
from typing import Any, Literal

from app.data_providers.us_market_reference import (
    NasdaqStateStreetUsMarketReferenceProvider,
    UsMarketReferenceProvider,
)

logger = logging.getLogger(__name__)

SECTOR_CN: dict[str, str] = {
    "Basic Materials": "基础材料",
    "Consumer Discretionary": "可选消费",
    "Consumer Staples": "日常消费",
    "Energy": "能源",
    "Finance": "金融",
    "Health Care": "医疗保健",
    "Industrials": "工业",
    "Miscellaneous": "其他",
    "Real Estate": "房地产",
    "Technology": "科技",
    "Telecommunications": "通信服务",
    "Utilities": "公用事业",
}

THEME_GROUPS: dict[str, dict[str, str]] = {
    "semiconductors": {"symbol": "XSD.US", "name": "半导体", "name_en": "Semiconductors"},
    "software-services": {"symbol": "XSW.US", "name": "软件与服务", "name_en": "Software & Services"},
    "biotechnology": {"symbol": "XBI.US", "name": "生物科技", "name_en": "Biotechnology"},
    "pharmaceuticals": {"symbol": "XPH.US", "name": "制药", "name_en": "Pharmaceuticals"},
    "healthcare-equipment": {"symbol": "XHE.US", "name": "医疗设备", "name_en": "Health Care Equipment"},
    "banks": {"symbol": "KBE.US", "name": "银行", "name_en": "Banking"},
    "regional-banks": {"symbol": "KRE.US", "name": "区域银行", "name_en": "Regional Banking"},
    "retail": {"symbol": "XRT.US", "name": "零售", "name_en": "Retail"},
    "homebuilders": {"symbol": "XHB.US", "name": "住宅建筑", "name_en": "Homebuilders"},
    "oil-gas-exploration": {"symbol": "XOP.US", "name": "油气勘探", "name_en": "Oil & Gas Exploration"},
    "metals-mining": {"symbol": "XME.US", "name": "金属与采矿", "name_en": "Metals & Mining"},
    "aerospace-defense": {"symbol": "XAR.US", "name": "航空航天与国防", "name_en": "Aerospace & Defense"},
    "telecom": {"symbol": "XTL.US", "name": "电信", "name_en": "Telecom"},
}

THEME_PROXIES: dict[str, str] = {
    spec["symbol"]: spec["name"] for spec in THEME_GROUPS.values()
}


class UsMarketGroupNotFoundError(KeyError):
    """指定的美股行业或主题不存在。"""


class UsMarketSectorUnavailableError(RuntimeError):
    """指定板块的数据源当前不可用。"""


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "unknown"


class NasdaqClassificationStore:
    TTL_SECONDS = 24 * 60 * 60

    def __init__(
        self,
        data_dir: Path,
        *,
        provider: UsMarketReferenceProvider | None = None,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(data_dir) / "us_market" / "classifications.json"
        self._provider = provider or NasdaqStateStreetUsMarketReferenceProvider()
        self._wall_time = wall_time
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None

    def get(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            disk = self._cache or self._read()
            if not force and disk is not None and self._is_fresh(disk):
                disk["status"] = "live" if self._cache is not None else "snapshot"
                self._cache = disk
                return copy.deepcopy(disk)
            try:
                parsed = self._provider.get_sector_classifications()
                parsed["fetched_at"] = int(self._wall_time() * 1000)
                parsed["status"] = "live"
                self._write(parsed)
                self._cache = parsed
                return copy.deepcopy(parsed)
            except Exception as exc:
                logger.warning("美股行业分类刷新失败: %s", exc)
                if disk is not None:
                    disk["status"] = "snapshot"
                    disk["message"] = "行业分类刷新失败,使用最近快照"
                    self._cache = disk
                    return copy.deepcopy(disk)
                return {
                    "schema_version": 1,
                    "source": "Nasdaq / Quotemedia SIC mapped sector and industry",
                    "standard": "sic_mapped",
                    "status": "unavailable",
                    "as_of": "",
                    "fetched_at": 0,
                    "message": "行业分类当前不可用",
                    "rows": [],
                }

    def _is_fresh(self, payload: Mapping[str, Any]) -> bool:
        fetched_at = _finite(payload.get("fetched_at")) or 0
        return self._wall_time() - fetched_at / 1000 < self.TTL_SECONDS

    def _read(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return None
        rows = payload.get("rows")
        return payload if isinstance(rows, list) else None

    def _write(self, payload: Mapping[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self._path)
        except OSError as exc:
            logger.warning("美股行业分类快照写入失败: %s", exc)


class StateStreetHoldingsStore:
    TTL_SECONDS = 6 * 60 * 60

    def __init__(
        self,
        data_dir: Path,
        *,
        provider: UsMarketReferenceProvider | None = None,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self._root = Path(data_dir) / "us_market" / "theme_holdings"
        self._provider = provider or NasdaqStateStreetUsMarketReferenceProvider()
        self._wall_time = wall_time
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}

    def get(self, group_id: str, *, force: bool = False) -> dict[str, Any]:
        spec = THEME_GROUPS.get(group_id)
        if spec is None:
            raise UsMarketGroupNotFoundError(group_id)
        ticker = spec["symbol"].removesuffix(".US")
        with self._lock:
            disk = self._cache.get(group_id) or self._read(group_id)
            if not force and disk is not None and self._is_fresh(disk):
                disk["status"] = "live" if group_id in self._cache else "snapshot"
                self._cache[group_id] = disk
                return copy.deepcopy(disk)
            try:
                parsed = self._provider.get_theme_holdings(ticker)
                parsed.update({
                    "group_id": group_id,
                    "ticker": ticker,
                    "fetched_at": int(self._wall_time() * 1000),
                    "status": "live",
                })
                self._write(group_id, parsed)
                self._cache[group_id] = parsed
                return copy.deepcopy(parsed)
            except Exception as exc:
                logger.warning("美股主题持仓刷新失败 (%s): %s", ticker, exc)
                if disk is not None:
                    disk["status"] = "snapshot"
                    disk["message"] = "主题持仓刷新失败,使用最近快照"
                    self._cache[group_id] = disk
                    return copy.deepcopy(disk)
                raise UsMarketSectorUnavailableError(
                    f"主题「{spec['name']}」的官方成分持仓当前不可用"
                ) from exc

    def _path(self, group_id: str) -> Path:
        return self._root / f"{group_id}.json"

    def _is_fresh(self, payload: Mapping[str, Any]) -> bool:
        fetched_at = _finite(payload.get("fetched_at")) or 0
        return self._wall_time() - fetched_at / 1000 < self.TTL_SECONDS

    def _read(self, group_id: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._path(group_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return None
        return payload if isinstance(payload.get("members"), list) else None

    def _write(self, group_id: str, payload: Mapping[str, Any]) -> None:
        path = self._path(group_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as exc:
            logger.warning("美股主题持仓快照写入失败 (%s): %s", group_id, exc)


def _valid_quote(row: Mapping[str, Any] | None) -> bool:
    if row is None:
        return False
    return (
        (_finite(row.get("last_price")) or 0) > 0
        and (_finite(row.get("prev_close")) or 0) > 0
        and _finite(row.get("change_pct")) is not None
    )


def _quote_public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "last_price": _finite(row.get("last_price")),
        "change_amount": _finite(row.get("change_amount")),
        "change_pct": _finite(row.get("change_pct")),
        "volume": _finite(row.get("volume")),
        "amount": _finite(row.get("amount")),
        "amount_estimated": bool(row.get("amount_estimated", False)),
        "timestamp": row.get("timestamp"),
    }


def aggregate_group(
    group_id: str,
    name: str,
    name_en: str,
    members: list[Mapping[str, Any]],
    quote_map: Mapping[str, Mapping[str, Any]],
    *,
    kind: Literal["sector", "theme"],
    proxy_symbol: str | None = None,
) -> dict[str, Any]:
    """按分类成员或 ETF 持仓成员聚合板块行情,比例保持小数制。"""
    valid: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for member in members:
        quote = quote_map.get(str(member.get("symbol") or ""))
        if _valid_quote(quote):
            valid.append((member, quote))

    changes = [float(quote["change_pct"]) for _, quote in valid]
    up = sum(value >= 0.00005 for value in changes)
    down = sum(value <= -0.00005 for value in changes)
    flat = len(changes) - up - down
    total_weight = sum(_finite(member.get("weight_pct")) or 0 for member in members)
    valid_weight = sum(_finite(member.get("weight_pct")) or 0 for member, _ in valid)
    weighted_change = None
    if valid_weight > 0:
        weighted_change = sum(
            float(quote["change_pct"]) * (_finite(member.get("weight_pct")) or 0)
            for member, quote in valid
        ) / valid_weight
    leaders = sorted(valid, key=lambda item: float(item[1]["change_pct"]), reverse=True)

    return {
        "id": group_id,
        "kind": kind,
        "name": name,
        "name_en": name_en,
        "proxy_symbol": proxy_symbol,
        "total_count": len(members),
        "valid_count": len(valid),
        "coverage_ratio": len(valid) / len(members) if members else 0,
        "weight_coverage_ratio": valid_weight / total_weight if total_weight > 0 else None,
        "avg_change_pct": sum(changes) / len(changes) if changes else None,
        "median_change_pct": median(changes) if changes else None,
        "weighted_change_pct": weighted_change,
        "up": up,
        "down": down,
        "flat": flat,
        "strong": sum(value >= 0.02 for value in changes),
        "weak": sum(value <= -0.02 for value in changes),
        "leader": _quote_public(leaders[0][1]) if leaders else None,
        "laggard": _quote_public(leaders[-1][1]) if leaders else None,
    }


class UsMarketSectorService:
    """组合行业分类、官方 ETF 持仓和 TickFlow 行情。"""

    def __init__(
        self,
        data_dir: Path,
        overview_service: Any,
        *,
        classifications: NasdaqClassificationStore | None = None,
        holdings: StateStreetHoldingsStore | None = None,
        reference_provider: UsMarketReferenceProvider | None = None,
    ) -> None:
        self._overview = overview_service
        provider = reference_provider or NasdaqStateStreetUsMarketReferenceProvider()
        self._classifications = classifications or NasdaqClassificationStore(
            data_dir, provider=provider
        )
        self._holdings = holdings or StateStreetHoldingsStore(data_dir, provider=provider)

    @property
    def classification_store(self) -> NasdaqClassificationStore:
        """共享行业分类缓存,避免其他美股服务重复刷新上游。"""
        return self._classifications

    def list_groups(self, *, force: bool = False) -> dict[str, Any]:
        overview, market_rows = self._overview.get_market_snapshot(force=force)
        quote_map = {str(row.get("symbol") or ""): row for row in market_rows}
        classification = self._classifications.get(force=force)
        sector_members: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in classification.get("rows", []):
            if isinstance(row, Mapping) and row.get("sector"):
                sector_members[str(row["sector"])].append(row)

        sectors = [
            aggregate_group(
                _slug(sector),
                SECTOR_CN.get(sector, sector),
                sector,
                members,
                quote_map,
                kind="sector",
            )
            for sector, members in sector_members.items()
        ]
        sectors.sort(
            key=lambda row: (
                row["avg_change_pct"] is not None,
                row["avg_change_pct"] if row["avg_change_pct"] is not None else float("-inf"),
            ),
            reverse=True,
        )

        theme_quotes = {
            str(row.get("symbol") or ""): row for row in overview.get("themes", [])
        }
        themes = []
        for group_id, spec in THEME_GROUPS.items():
            themes.append({
                "id": group_id,
                "kind": "theme",
                "name": spec["name"],
                "name_en": spec["name_en"],
                "proxy_symbol": spec["symbol"],
                "proxy_quote": theme_quotes.get(spec["symbol"]),
            })

        return {
            "schema_version": 1,
            "market_status": overview.get("status"),
            "market_as_of": overview.get("as_of"),
            "classification": {
                key: classification.get(key)
                for key in ("status", "source", "standard", "as_of", "fetched_at", "message")
                if classification.get(key) is not None
            },
            "sectors": sectors,
            "themes": themes,
        }

    def get_detail(
        self,
        group_id: str,
        *,
        kind: Literal["sector", "theme"],
        force: bool = False,
    ) -> dict[str, Any]:
        overview, market_rows = self._overview.get_market_snapshot(force=force)
        quote_map = {str(row.get("symbol") or ""): row for row in market_rows}
        if kind == "sector":
            return self._sector_detail(group_id, overview, quote_map, force=force)
        return self._theme_detail(group_id, overview, quote_map, force=force)

    def _sector_detail(
        self,
        group_id: str,
        overview: Mapping[str, Any],
        quote_map: Mapping[str, Mapping[str, Any]],
        *,
        force: bool,
    ) -> dict[str, Any]:
        classification = self._classifications.get(force=force)
        rows = [
            row for row in classification.get("rows", [])
            if isinstance(row, Mapping) and _slug(str(row.get("sector") or "")) == group_id
        ]
        if not rows:
            if classification.get("status") == "unavailable":
                raise UsMarketSectorUnavailableError("美股行业分类当前不可用")
            raise UsMarketGroupNotFoundError(group_id)
        sector_name = str(rows[0]["sector"])
        summary = aggregate_group(
            group_id,
            SECTOR_CN.get(sector_name, sector_name),
            sector_name,
            rows,
            quote_map,
            kind="sector",
        )
        industries: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            industry = str(row.get("industry") or "未分类")
            industries[industry].append(row)
        industry_summaries = [
            aggregate_group(
                _slug(industry),
                industry,
                industry,
                members,
                quote_map,
                kind="sector",
            )
            for industry, members in industries.items()
        ]
        industry_summaries.sort(
            key=lambda row: row["avg_change_pct"] if row["avg_change_pct"] is not None else float("-inf"),
            reverse=True,
        )
        return self._detail_payload(
            overview,
            summary,
            rows,
            quote_map,
            source={
                key: classification.get(key)
                for key in ("status", "source", "standard", "as_of", "message")
                if classification.get(key) is not None
            },
            industries=industry_summaries,
        )

    def _theme_detail(
        self,
        group_id: str,
        overview: Mapping[str, Any],
        quote_map: dict[str, Mapping[str, Any]],
        *,
        force: bool,
    ) -> dict[str, Any]:
        spec = THEME_GROUPS.get(group_id)
        if spec is None:
            raise UsMarketGroupNotFoundError(group_id)
        holdings = self._holdings.get(group_id, force=force)
        members = [row for row in holdings.get("members", []) if isinstance(row, Mapping)]
        missing_symbols = [
            str(row.get("symbol") or "") for row in members
            if row.get("symbol") and row.get("symbol") not in quote_map
        ]
        if missing_symbols:
            for row in self._overview.fetch_symbol_quotes(missing_symbols):
                quote_map[str(row.get("symbol") or "")] = row
        summary = aggregate_group(
            group_id,
            spec["name"],
            spec["name_en"],
            members,
            quote_map,
            kind="theme",
            proxy_symbol=spec["symbol"],
        )
        proxy_quote = next(
            (
                row for row in overview.get("themes", [])
                if isinstance(row, Mapping) and row.get("symbol") == spec["symbol"]
            ),
            None,
        )
        summary["proxy_quote"] = proxy_quote
        return self._detail_payload(
            overview,
            summary,
            members,
            quote_map,
            source={
                key: holdings.get(key)
                for key in ("status", "source", "source_url", "as_of", "message")
                if holdings.get(key) is not None
            },
            industries=[],
        )

    @staticmethod
    def _detail_payload(
        overview: Mapping[str, Any],
        summary: Mapping[str, Any],
        members: list[Mapping[str, Any]],
        quote_map: Mapping[str, Mapping[str, Any]],
        *,
        source: Mapping[str, Any],
        industries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        public_members = []
        for member in members:
            symbol = str(member.get("symbol") or "")
            quote = quote_map.get(symbol)
            public_members.append({
                "symbol": symbol,
                "name": str(member.get("name") or (quote or {}).get("name") or symbol),
                "sector": str(member.get("sector") or ""),
                "industry": str(member.get("industry") or ""),
                "weight_pct": _finite(member.get("weight_pct")),
                "quote": _quote_public(quote) if _valid_quote(quote) else None,
            })
        if any(row["weight_pct"] is not None for row in public_members):
            public_members.sort(
                key=lambda row: row["weight_pct"] if row["weight_pct"] is not None else float("-inf"),
                reverse=True,
            )
        else:
            public_members.sort(
                key=lambda row: (
                    row["quote"] is not None,
                    row["quote"]["change_pct"] if row["quote"] is not None else float("-inf"),
                ),
                reverse=True,
            )
        return {
            "schema_version": 1,
            "market_status": overview.get("status"),
            "market_as_of": overview.get("as_of"),
            "source": dict(source),
            "summary": dict(summary),
            "industries": industries,
            "members": public_members,
        }
