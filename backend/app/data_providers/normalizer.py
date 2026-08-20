"""Normalize provider responses into internal Polars schemas."""
from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from app.indicators.pipeline import filter_halt_days

DAILY_COLS = ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "quote_ts"]
ADJ_FACTOR_COLS = ["symbol", "trade_date", "ex_factor"]
INSTRUMENT_COLS = ["symbol", "name", "code", "exchange", "asset_type", "source"]
MINUTE_COLS = [
    "symbol", "asset_type", "source", "datetime", "received_at", "freq",
    "open", "high", "low", "close",
    "raw_open", "raw_high", "raw_low", "raw_close",
    "volume", "amount",
]


def to_polars(data) -> pl.DataFrame:
    if data is None:
        return pl.DataFrame()
    if isinstance(data, pl.DataFrame):
        return data
    if isinstance(data, dict):
        rows: list[dict] = []
        for sym, values in data.items():
            for item in values or []:
                row = dict(item or {})
                row.setdefault("symbol", sym)
                rows.append(row)
        return pl.DataFrame(rows) if rows else pl.DataFrame()
    if hasattr(data, "reset_index"):
        return pl.from_pandas(data.reset_index())
    try:
        return pl.DataFrame(data)
    except Exception:
        return pl.DataFrame()


def normalize_daily(data, default_symbol: str | None = None, source: str = "tickflow") -> pl.DataFrame:
    df = to_polars(data)
    if df.is_empty():
        return df
    rename_map = {
        "ts_code": "symbol",
        "trade_date": "date",
        "datetime": "date",
        "vol": "volume",
        "amt": "amount",
        "timestamp": "quote_ts",
    }
    df = df.rename({k: v for k, v in rename_map.items() if k in df.columns})
    if "symbol" not in df.columns and default_symbol:
        df = df.with_columns(pl.lit(default_symbol).alias("symbol"))
    if "date" in df.columns and df.schema["date"] != pl.Date:
        df = df.with_columns(pl.col("date").cast(pl.Date, strict=False))
    # quote_ts: 毫秒级行情时间戳, 用于盘后校验/量比折算。保留为 Int64, 缺失则置 null。
    if "quote_ts" in df.columns:
        df = df.with_columns(pl.col("quote_ts").cast(pl.Int64, strict=False))
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))
    df = filter_halt_days(df)
    keep = [c for c in DAILY_COLS if c in df.columns]
    return df.select(keep) if keep else pl.DataFrame()


def normalize_adj_factors(data, source: str = "tickflow") -> pl.DataFrame:
    df = to_polars(data)
    if df.is_empty():
        return df
    rename_map = {
        "timestamp": "trade_date",
        "date": "trade_date",
        "adj_factor": "ex_factor",
    }
    df = df.rename({k: v for k, v in rename_map.items() if k in df.columns})
    if "trade_date" in df.columns:
        if df.schema["trade_date"] in {pl.Int64, pl.Int32, pl.UInt64, pl.UInt32, pl.Float64, pl.Float32}:
            df = df.with_columns(
                pl.from_epoch(pl.col("trade_date").cast(pl.Int64), time_unit="ms").dt.date().alias("trade_date")
            )
        else:
            df = df.with_columns(pl.col("trade_date").cast(pl.Date, strict=False))
    if "ex_factor" in df.columns:
        df = df.with_columns(pl.col("ex_factor").cast(pl.Float64, strict=False))
    keep = [c for c in ADJ_FACTOR_COLS if c in df.columns]
    return df.select(keep).drop_nulls() if len(keep) == len(ADJ_FACTOR_COLS) else pl.DataFrame()


def normalize_minute(
    data,
    *,
    default_symbol: str | None = None,
    asset_type: str = "stock",
    source: str = "tickflow",
    freq: str = "1m",
    received_at: datetime | None = None,
) -> pl.DataFrame:
    """Normalize minute bars and preserve raw execution prices.

    Provider minute data is requested unadjusted.  The generic OHLC aliases are
    intentionally kept equal to the raw values here; callers may derive adjusted
    feature prices separately, while execution and price-limit checks always use
    the ``raw_*`` columns.
    """
    df = to_polars(data)
    if df.is_empty():
        return df
    aliases = {
        "symbol": ("ts_code",),
        # TickFlow minute frames include an epoch timestamp together with
        # string trade_date/trade_time columns.  Prefer the complete epoch;
        # a time-only string cannot be safely reconstructed on its own.
        "datetime": ("timestamp", "trade_time", "trade_date"),
        "volume": ("vol",),
        "amount": ("amt",),
    }
    rename_map: dict[str, str] = {}
    columns = set(df.columns)
    for target, sources in aliases.items():
        if target in columns:
            continue
        source = next((name for name in sources if name in columns), None)
        if source is not None:
            rename_map[source] = target
            columns.add(target)
    if rename_map:
        df = df.rename(rename_map)
    if "symbol" not in df.columns and default_symbol:
        df = df.with_columns(pl.lit(default_symbol).alias("symbol"))
    if "datetime" not in df.columns and "timestamp" in df.columns:
        df = df.with_columns(
            pl.from_epoch(pl.col("timestamp").cast(pl.Int64), time_unit="ms")
            .dt.replace_time_zone("UTC")
            .dt.convert_time_zone("Asia/Shanghai")
            .dt.replace_time_zone(None)
            .alias("datetime")
        )
    elif "datetime" in df.columns:
        dtype = df.schema["datetime"]
        if dtype in {pl.Int64, pl.Int32, pl.UInt64, pl.UInt32, pl.Float64, pl.Float32}:
            df = df.with_columns(
                pl.from_epoch(pl.col("datetime").cast(pl.Int64), time_unit="ms")
                .dt.replace_time_zone("UTC")
                .dt.convert_time_zone("Asia/Shanghai")
                .dt.replace_time_zone(None)
                .alias("datetime")
            )
        elif isinstance(dtype, pl.Datetime) and dtype.time_zone is not None:
            df = df.with_columns(
                pl.col("datetime")
                .dt.convert_time_zone("Asia/Shanghai")
                .dt.replace_time_zone(None)
                .cast(pl.Datetime("us"))
            )
        else:
            df = df.with_columns(pl.col("datetime").cast(pl.Datetime("us"), strict=False))
    if "symbol" not in df.columns or "datetime" not in df.columns:
        return pl.DataFrame()

    for column in ("open", "high", "low", "close", "volume", "amount"):
        if column not in df.columns:
            return pl.DataFrame()
        df = df.with_columns(pl.col(column).cast(pl.Float64, strict=False))
    for column in ("open", "high", "low", "close"):
        raw_column = f"raw_{column}"
        if raw_column in df.columns:
            df = df.with_columns(pl.col(raw_column).cast(pl.Float64, strict=False))
        else:
            df = df.with_columns(pl.col(column).alias(raw_column))

    received = received_at or datetime.now(UTC)
    df = df.with_columns(
        pl.lit(asset_type).alias("asset_type"),
        pl.lit(source).alias("source"),
        pl.lit(freq).alias("freq"),
        pl.lit(received).cast(pl.Datetime("us", "UTC")).alias("received_at"),
    ).filter(
        pl.col("symbol").is_not_null()
        & pl.col("datetime").is_not_null()
        & (pl.col("raw_open") > 0)
        & (pl.col("raw_high") > 0)
        & (pl.col("raw_low") > 0)
        & (pl.col("raw_close") > 0)
        & (pl.col("volume") >= 0)
        & (pl.col("amount") >= 0)
    )
    if df.is_empty():
        return df
    return (
        df.select(MINUTE_COLS)
        .unique(subset=["symbol", "datetime"], keep="last")
        .sort(["datetime", "symbol"])
    )


def normalize_instruments(rows: list[dict], asset_type: str, source: str = "tickflow") -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    out: list[dict] = []
    for item in rows:
        symbol = item.get("symbol")
        if not symbol:
            continue
        out.append({
            "symbol": str(symbol),
            "name": item.get("name") or str(symbol),
            "code": item.get("code") or str(symbol).split(".")[0],
            "exchange": item.get("exchange"),
            "asset_type": asset_type,
            "source": source,
        })
    if not out:
        return pl.DataFrame()
    return pl.DataFrame(out).select(INSTRUMENT_COLS).unique(subset=["symbol"], keep="last").sort("symbol")
