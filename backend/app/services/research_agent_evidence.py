"""指定 A 股和候选股票的结构化证据聚合。"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import polars as pl

from app.services.financial_sync import get_financial_df
from app.services.research_agent_announcements import CninfoAnnouncementProvider
from app.services.research_agent_models import StockEvidence

_TECHNICAL_FIELDS = (
    "close",
    "raw_close",
    "change_pct",
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "rsi_14",
    "boll_upper",
    "boll_lower",
    "vol_ratio_5d",
    "annual_vol_20d",
    "momentum_5d",
    "momentum_20d",
    "high_60d",
    "low_60d",
    "turnover_rate",
)
_FINANCIAL_FIELDS = (
    "ann_date",
    "report_date",
    "end_date",
    "fiscal_year",
    "fiscal_period",
    "revenue",
    "total_revenue",
    "revenue_yoy",
    "operating_revenue",
    "net_profit",
    "net_profit_yoy",
    "net_profit_parent",
    "gross_profit_margin",
    "net_profit_margin",
    "roe",
    "roa",
    "eps",
    "pe_ttm",
    "pb",
    "debt_to_assets",
    "current_ratio",
    "operating_cash_flow",
    "net_cash_flow",
)
_ANNOUNCEMENT_PROVIDER = CninfoAnnouncementProvider()


@lru_cache(maxsize=4)
def _read_dimension(path_value: str, modified_ns: int, field: str) -> pl.DataFrame:
    del modified_ns
    return pl.read_parquet(Path(path_value), columns=["symbol", field])


def _dimension_strength(
    frame: pl.DataFrame,
    field: str,
    separator: str,
    labels: list[str],
    latest: pl.DataFrame,
) -> list[dict[str, Any]]:
    if not labels or not {"symbol", "change_pct"}.issubset(latest.columns):
        return []
    members = (
        frame.select(
            pl.col("symbol").cast(pl.String).str.to_uppercase(),
            pl.col(field).cast(pl.String).str.split(separator).alias("_label"),
        )
        .explode("_label")
        .with_columns(pl.col("_label").str.strip_chars())
        .filter(pl.col("_label").is_in(labels))
    )
    changes = latest.select(
        pl.col("symbol").cast(pl.String).str.to_uppercase(),
        pl.col("change_pct").cast(pl.Float64, strict=False),
    ).filter(pl.col("change_pct").is_not_null() & pl.col("change_pct").is_finite())
    if members.is_empty() or changes.is_empty():
        return []
    stats = (
        members.join(changes, on="symbol", how="inner")
        .group_by("_label")
        .agg(
            pl.len().alias("sample_size"),
            pl.col("change_pct").mean().alias("mean_change_pct"),
            pl.col("change_pct").median().alias("median_change_pct"),
            (pl.col("change_pct") > 0).sum().alias("advancers"),
        )
    )
    rows = {row["_label"]: row for row in stats.to_dicts()}
    return [
        {
            "label": label,
            "sample_size": rows[label]["sample_size"],
            "mean_change_pct": _safe(rows[label]["mean_change_pct"]),
            "median_change_pct": _safe(rows[label]["median_change_pct"]),
            "advancers": rows[label]["advancers"],
        }
        for label in labels
        if label in rows
    ]


def _local_dimensions(data_dir, symbol: str, latest: pl.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False,
        "concepts": [],
        "industries": [],
        "concept_strength": [],
        "industry_strength": [],
        "change_pct_unit": "decimal",
    }
    definitions = (
        ("ext_gn_ths", "所属概念", "concepts", ";"),
        ("ext_hy_ths", "所属同花顺行业", "industries", "-"),
    )
    for config_id, field, output, separator in definitions:
        path = data_dir / "ext_data" / config_id / "part.parquet"
        if not path.exists():
            continue
        try:
            frame = _read_dimension(str(path), path.stat().st_mtime_ns, field)
        except Exception:
            continue
        row = frame.filter(pl.col("symbol").cast(pl.String).str.to_uppercase() == symbol).head(1)
        if row.is_empty():
            continue
        raw = row[field][0]
        values = [value.strip() for value in str(raw or "").split(separator) if value.strip()]
        values = values[:8] if output == "concepts" else values[:4]
        result[output] = values
        strength_key = "concept_strength" if output == "concepts" else "industry_strength"
        result[strength_key] = _dimension_strength(frame, field, separator, values, latest)
    result["available"] = bool(result["concepts"] or result["industries"])
    return result


def _safe(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    if isinstance(value, (date,)):
        return value.isoformat()
    return value


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) < 8:
        return None
    try:
        return date.fromisoformat(f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}")
    except ValueError:
        return None


def _latest_financials(data_dir, symbol: str, as_of: date) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for table in ("metrics", "income", "cash_flow"):
        try:
            frame = get_financial_df(data_dir, table)
        except Exception:
            continue
        if frame is None or frame.is_empty() or "symbol" not in frame.columns:
            continue
        records = frame.filter(pl.col("symbol") == symbol).to_dicts()
        if not records:
            continue
        dated_records = []
        for record in records:
            known_at = next(
                (
                    resolved
                    for column in ("ann_date", "publish_date", "announcement_date")
                    if (resolved := _coerce_date(record.get(column))) is not None
                ),
                None,
            )
            if known_at is not None and known_at <= as_of:
                dated_records.append((known_at, record))
        if not dated_records:
            continue
        _, record = max(dated_records, key=lambda item: item[0])
        selected = {
            key: _safe(record[key])
            for key in _FINANCIAL_FIELDS
            if key in record and record[key] is not None
        }
        if not selected:
            selected = {
                key: _safe(value)
                for key, value in record.items()
                if key not in {"symbol", "name"} and value is not None
            }
        output[table] = dict(list(selected.items())[:24])
    return output


def _market_sentiment(latest: pl.DataFrame, as_of: date) -> dict[str, Any]:
    if latest.is_empty() or "change_pct" not in latest.columns:
        return {"as_of": as_of.isoformat(), "available": False}
    valid = latest.filter(pl.col("change_pct").is_not_null() & pl.col("change_pct").is_finite())
    if valid.is_empty():
        return {"as_of": as_of.isoformat(), "available": False}
    values = valid["change_pct"]
    result = {
        "as_of": as_of.isoformat(),
        "available": True,
        "sample_size": valid.height,
        "advancers": valid.filter(pl.col("change_pct") > 0).height,
        "decliners": valid.filter(pl.col("change_pct") < 0).height,
        "median_change_pct": _safe(values.median()),
    }
    if "signal_limit_up" in valid.columns:
        result["limit_up_count"] = valid.filter(pl.col("signal_limit_up").fill_null(False)).height
    if "signal_broken_limit_up" in valid.columns:
        result["broken_limit_count"] = valid.filter(
            pl.col("signal_broken_limit_up").fill_null(False)
        ).height
    return result


def build_stock_evidence(
    repo,
    symbol: str,
    as_of: date | None = None,
    *,
    announcement_provider: CninfoAnnouncementProvider | None = None,
) -> StockEvidence:
    normalized = symbol.strip().upper()
    latest, latest_date = repo.get_enriched_latest()
    resolved_date = as_of or latest_date
    if resolved_date is None:
        raise ValueError("暂无可用的 A 股指标日期")

    start = resolved_date - timedelta(days=180)
    history = repo.get_enriched_range(start, resolved_date, symbols=[normalized])
    if history is None or history.is_empty():
        history = repo.get_daily(normalized, start, resolved_date)
    if history is None or history.is_empty():
        raise ValueError(f"{normalized} 缺少可用日线数据")
    history = history.filter(pl.col("date") <= resolved_date).sort("date")
    if history.is_empty():
        raise ValueError(f"{normalized} 在 {resolved_date} 前无可用数据")

    row = history.tail(1).to_dicts()[0]
    actual_date = row["date"]
    if not isinstance(actual_date, date):
        actual_date = date.fromisoformat(str(actual_date)[:10])
    names = repo.get_name_map([normalized])
    name = names.get(normalized) or str(row.get("name") or normalized)
    technical = {key: _safe(row.get(key)) for key in _TECHNICAL_FIELDS if key in row}
    technical["signals"] = [
        key for key, value in row.items() if key.startswith("signal_") and value is True
    ]
    technical["history_days"] = history["date"].n_unique()
    technical["price_unit"] = "CNY"
    technical["change_pct_unit"] = "decimal"
    technical["adjustment"] = "forward"

    if latest_date == actual_date:
        market_frame = latest
    else:
        market_frame = repo.get_enriched_range(actual_date, actual_date)
        if market_frame is None:
            market_frame = pl.DataFrame()
    market = _market_sentiment(market_frame, actual_date)
    fundamentals = _latest_financials(repo.store.data_dir, normalized, actual_date)
    dimensions = _local_dimensions(repo.store.data_dir, normalized, market_frame)
    provider = announcement_provider or _ANNOUNCEMENT_PROVIDER
    information = provider.fetch(normalized, end_date=actual_date)
    missing: list[str] = []
    if not fundamentals:
        missing.append("基本面数据")
    if not dimensions["available"]:
        missing.append("行业/概念分类")
    if not information["available"]:
        missing.append("公告元数据")
    missing.append("普通新闻")

    return StockEvidence(
        symbol=normalized,
        name=name,
        as_of=actual_date,
        snapshot={
            "close": _safe(row.get("close")),
            "raw_close": _safe(row.get("raw_close")),
            "date": actual_date.isoformat(),
        },
        technical=technical,
        sentiment=market,
        industry=dimensions,
        fundamental={"available": bool(fundamentals), "tables": fundamentals},
        information=information,
        missing_data=missing,
    )
