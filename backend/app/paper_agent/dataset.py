"""Point-in-Time training dataset construction for the investment expert."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from app.market_time import CN_TZ
from app.price_limits import (
    polars_is_risk_warning_name,
    polars_limit_price,
    polars_price_limit_pct,
)


class HistoricalMinuteDataError(RuntimeError):
    """Historical minute data is missing or incomplete for an executable date."""


def build_point_in_time_candidates(
    daily: pl.DataFrame,
    *,
    limit: int = 50,
) -> pl.DataFrame:
    """Rank on day T and make the result eligible only on the next observed day."""
    required = {"symbol", "date", "close", "amount"}
    if daily.is_empty() or not required.issubset(daily.columns):
        return pl.DataFrame()
    frame = daily.sort(["symbol", "date"])
    if frame.schema["date"] != pl.Date:
        frame = frame.with_columns(pl.col("date").cast(pl.Date, strict=False))
    if "name" in frame.columns:
        frame = frame.filter(~pl.col("name").fill_null("").str.to_uppercase().str.contains(r"\*?ST"))
    frame = frame.filter(
        (pl.col("close").cast(pl.Float64, strict=False) > 0)
        & (pl.col("amount").cast(pl.Float64, strict=False) > 0)
    ).with_columns(
        (
            pl.col("close").cast(pl.Float64)
            / pl.col("close").cast(pl.Float64).shift(20).over("symbol")
            - 1
        ).alias("_momentum_20d"),
        pl.col("amount").cast(pl.Float64).alias("_amount"),
    ).drop_nulls(["_momentum_20d"])
    if frame.is_empty():
        return pl.DataFrame()

    dates = frame.select("date").unique().sort("date").with_columns(
        pl.col("date").shift(-1).alias("trade_date")
    ).rename({"date": "source_date"}).drop_nulls("trade_date")
    ranked = frame.with_columns(
        pl.col("_momentum_20d").rank(method="average", descending=True).over("date").alias("_mom_rank"),
        pl.col("_amount").rank(method="average", descending=True).over("date").alias("_amt_rank"),
        pl.len().over("date").cast(pl.Float64).alias("_count"),
    ).with_columns(
        (
            0.7 * (1 - (pl.col("_mom_rank") - 1) / pl.col("_count"))
            + 0.3 * (1 - (pl.col("_amt_rank") - 1) / pl.col("_count"))
        ).alias("score")
    ).sort(["date", "score", "symbol"], descending=[False, True, False]).group_by(
        "date", maintain_order=True
    ).head(limit).rename({"date": "source_date"}).join(dates, on="source_date", how="inner")
    return ranked.select(
        "trade_date", "source_date", "symbol", "score", "_momentum_20d", "_amount"
    ).sort(["trade_date", "score", "symbol"], descending=[False, True, False])


class TrainingDatasetBuilder:
    def __init__(self, repo, data_dir: Path, minute_provider) -> None:
        self.repo = repo
        self.data_dir = data_dir
        self.root = data_dir / "user_data" / "investment_expert" / "training"
        self.minute_provider = minute_provider

    def build(
        self,
        *,
        start_date: date,
        end_date: date,
        candidate_limit: int = 50,
        download_minutes: bool = True,
        remote_minutes_enabled: bool = True,
        remote_minute_start_date: date | None = None,
        fallback_minute_source: str | None = None,
        fallback_minute_revision: str | None = None,
        progress_cb=None,
    ) -> dict[str, Any]:
        latest_available = self.repo.latest_daily_date()
        query_end = min(end_date, latest_available) if latest_available is not None else end_date
        instruments = self.repo.get_instruments()
        query_start = start_date - timedelta(days=40)
        enriched = self.repo.get_enriched_range(query_start, query_end)
        symbols = instruments["symbol"].to_list() if "symbol" in instruments.columns else []
        raw = self.repo.get_daily_batch(
            symbols,
            query_start,
            query_end,
            columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount"],
        )
        daily = self._merge_daily_history(enriched, raw)
        if "name" not in daily.columns and "name" in instruments.columns and not daily.is_empty():
            daily = daily.join(
                instruments.select("symbol", "name").unique("symbol"),
                on="symbol",
                how="left",
            )
        candidates = build_point_in_time_candidates(daily, limit=candidate_limit)
        if candidates.is_empty():
            raise ValueError("insufficient enriched daily history for Point-in-Time candidates")
        candidates = candidates.filter(
            (pl.col("trade_date") >= start_date) & (pl.col("trade_date") <= end_date)
        )
        if candidates.is_empty():
            raise ValueError("no Point-in-Time candidates inside requested range")

        candidate_dir = self.root / "candidates"
        minute_dir = self.root / "minute"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        minute_dir.mkdir(parents=True, exist_ok=True)
        date_groups = self._ordered_date_groups(candidates)
        written_candidate_rows = 0
        downloaded_minute_rows = 0
        local_minute_rows = 0
        local_minute_dates = 0
        skipped_minute_dates = 0
        total = len(date_groups)
        missing_minute_dates: list[str] = []
        previous_symbols: list[str] = []
        minute_rows_total = 0
        minute_dates_available = 0
        remote_minute_dates = 0
        audited_gap_pairs: set[tuple[date, str]] = set()
        audited_gaps = (
            self._load_audited_minute_gaps()
            if fallback_minute_source is not None
            else pl.DataFrame()
        )
        for index, (trade_date, group) in enumerate(date_groups, start=1):
            candidate_path = candidate_dir / f"date={trade_date}" / "part.parquet"
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_tmp = candidate_path.with_suffix(".parquet.tmp")
            group.write_parquet(candidate_tmp)
            candidate_tmp.replace(candidate_path)
            written_candidate_rows += group.height

            minute_path = minute_dir / f"date={trade_date}" / "part.parquet"
            current_symbols = group["symbol"].unique().sort().to_list()
            # Include yesterday's candidates for legal T+1 exits and labels.
            # Carry-over symbols remain excluded from today's buy candidates.
            symbols = sorted(set(current_symbols) | set(previous_symbols))
            required_symbols = self._tradable_symbols(daily, trade_date, symbols)
            remote_for_date = bool(
                remote_minutes_enabled
                and (
                    remote_minute_start_date is None
                    or trade_date >= remote_minute_start_date
                )
            )
            allowed_missing_symbols = self._audited_gap_symbols(
                audited_gaps,
                trade_date,
                required_symbols,
            ) if not remote_for_date else []
            if (
                download_minutes
                and minute_path.exists()
                and self._minute_partition_valid(
                    minute_path,
                    required_symbols,
                    trade_date=trade_date,
                    allowed_missing_symbols=allowed_missing_symbols,
                )
            ):
                if allowed_missing_symbols:
                    resumed = pl.read_parquet(minute_path)
                    audited_gap_pairs.update(
                        (trade_date, symbol)
                        for symbol in self._missing_symbols(resumed, required_symbols)
                        if symbol in allowed_missing_symbols
                    )
                skipped_minute_dates += 1
                minute_dates_available += 1
                minute_rows_total += self._parquet_row_count(minute_path)
            elif download_minutes:
                start_time = datetime.combine(trade_date, time(9, 15), tzinfo=CN_TZ)
                end_time = datetime.combine(trade_date, time(15, 5), tzinfo=CN_TZ)
                local = pl.DataFrame()
                if remote_for_date:
                    minute = self.minute_provider.get_minute(
                        symbols,
                        start_time=start_time,
                        end_time=end_time,
                        asset_type="stock",
                        freq="1m",
                    )
                else:
                    local = self._load_local_minute(symbols, trade_date)
                    minute = local
                if minute.is_empty():
                    if not remote_for_date:
                        raise HistoricalMinuteDataError(
                            "minute dataset incomplete: canonical local stock/index/ETF "
                            f"partitions returned no 1m data for {trade_date}"
                        )
                    provider_name = getattr(
                        self.minute_provider,
                        "name",
                        "configured provider",
                    )
                    raise HistoricalMinuteDataError(
                        "minute dataset incomplete: "
                        f"historical minute provider '{provider_name}' returned no 1m data "
                        f"for {trade_date}; verify historical-minute entitlement and coverage"
                    )
                minute = self._filter_trade_date(minute, trade_date)
                if minute.is_empty():
                    raise HistoricalMinuteDataError(
                        f"historical minute response contains no rows for requested date {trade_date}"
                    )
                minute = self._ensure_raw_price_columns(minute)
                minute = self._add_execution_flags(minute, daily, trade_date)
                if not self._minute_frame_valid(
                    minute,
                    required_symbols,
                    trade_date=trade_date,
                    allowed_missing_symbols=allowed_missing_symbols,
                ):
                    missing = self._missing_symbols(minute, required_symbols)
                    unexpected = sorted(set(missing) - set(allowed_missing_symbols))
                    suffix = f": {', '.join(unexpected[:5])}" if unexpected else ""
                    raise HistoricalMinuteDataError(
                        f"historical minute coverage is incomplete for {trade_date}{suffix}"
                    )
                audited_gap_pairs.update(
                    (trade_date, symbol)
                    for symbol in self._missing_symbols(minute, required_symbols)
                    if symbol in allowed_missing_symbols
                )
                minute_path.parent.mkdir(parents=True, exist_ok=True)
                minute_tmp = minute_path.with_suffix(".parquet.tmp")
                minute.write_parquet(minute_tmp)
                minute_tmp.replace(minute_path)
                if local.height:
                    local_minute_dates += 1
                    local_minute_rows += local.height
                elif remote_for_date:
                    downloaded_minute_rows += minute.height
                    remote_minute_dates += 1
                minute_rows_total += minute.height
                minute_dates_available += 1
            previous_symbols = current_symbols
            if progress_cb is not None:
                progress_cb(index, total, str(trade_date))

        if download_minutes and missing_minute_dates:
            sample = ", ".join(missing_minute_dates[:5])
            raise RuntimeError(
                "minute dataset incomplete: "
                f"{len(missing_minute_dates)} date(s) returned no data; first: {sample}"
            )

        minute_sources = []
        if remote_minutes_enabled:
            minute_sources.append(
                getattr(self.minute_provider, "name", "configured provider")
            )
        if fallback_minute_source is not None:
            minute_sources.append(fallback_minute_source)
        manifest = {
            "schema_version": 1,
            "source": "provider_or_canonical_local_fallback",
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "candidate_limit": candidate_limit,
            "candidate_dates": total,
            "candidate_rows": written_candidate_rows,
            "minute_source": getattr(self.minute_provider, "name", "unknown"),
            "minute_sources": list(dict.fromkeys(minute_sources)),
            "minute_remote_enabled": remote_minutes_enabled,
            "minute_remote_start_date": (
                remote_minute_start_date.isoformat()
                if remote_minute_start_date is not None
                else None
            ),
            "minute_fallback_revision": fallback_minute_revision,
            "minute_dates_remote": remote_minute_dates,
            "minute_dates_available": minute_dates_available,
            "minute_rows": minute_rows_total,
            "minute_rows_downloaded": downloaded_minute_rows,
            "minute_dates_local": local_minute_dates,
            "minute_rows_local": local_minute_rows,
            "minute_dates_resumed": skipped_minute_dates,
            "audited_minute_gap_symbol_dates": len(audited_gap_pairs),
            "audited_minute_gap_handling": "skip_unlabelable_samples_without_synthesis",
            "price_contract": {
                "features": "daily enriched / minute OHLC",
                "execution": "raw_open/raw_high/raw_low/raw_close",
            },
            "anti_leakage": "source_date < trade_date",
            "t_plus_one_carryover": "minute date T includes candidates(T) union candidates(T-1)",
        }
        manifest["manifest_hash"] = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        manifest_path = self.root / "manifest.json"
        manifest_tmp = manifest_path.with_suffix(".json.tmp")
        manifest_tmp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest_tmp.replace(manifest_path)
        return manifest

    @staticmethod
    def _merge_daily_history(
        enriched: pl.DataFrame | None,
        raw: pl.DataFrame | None,
    ) -> pl.DataFrame:
        """Prefer enriched rows while filling uncovered dates from raw daily history."""
        if enriched is None or enriched.is_empty():
            return raw if raw is not None else pl.DataFrame()
        if raw is None or raw.is_empty():
            return enriched
        keys = enriched.select("symbol", "date").unique()
        missing_raw = raw.join(keys, on=["symbol", "date"], how="anti")
        return pl.concat([enriched, missing_raw], how="diagonal_relaxed")

    @staticmethod
    def _ordered_date_groups(candidates: pl.DataFrame) -> list[tuple[date, pl.DataFrame]]:
        groups = candidates.partition_by("trade_date", as_dict=True)
        resolved: list[tuple[date, pl.DataFrame]] = []
        for key, group in groups.items():
            trade_date = key[0] if isinstance(key, tuple) else key
            if not isinstance(trade_date, date):
                trade_date = date.fromisoformat(str(trade_date))
            resolved.append((trade_date, group))
        return sorted(resolved, key=lambda item: item[0])

    @staticmethod
    def _tradable_symbols(
        daily: pl.DataFrame,
        trade_date: date,
        symbols: list[str],
    ) -> list[str]:
        if daily.is_empty() or not {"symbol", "date"}.issubset(daily.columns):
            return symbols
        filters = (pl.col("date") == trade_date) & pl.col("symbol").is_in(symbols)
        if "volume" in daily.columns:
            filters &= pl.col("volume").cast(pl.Float64, strict=False).fill_null(0) > 0
        if "amount" in daily.columns:
            filters &= pl.col("amount").cast(pl.Float64, strict=False).fill_null(0) > 0
        return sorted(str(value) for value in daily.filter(filters)["symbol"].unique().to_list())

    @staticmethod
    def _filter_trade_date(minute: pl.DataFrame, trade_date: date) -> pl.DataFrame:
        if "datetime" not in minute.columns:
            return pl.DataFrame()
        return minute.filter(pl.col("datetime").cast(pl.Datetime, strict=False).dt.date() == trade_date)

    def _load_local_minute(self, symbols: list[str], trade_date: date) -> pl.DataFrame:
        loader = getattr(self.repo, "get_minute_batch", None)
        if not callable(loader):
            return pl.DataFrame()
        frames = []
        remaining = list(symbols)
        for asset_type in ("stock", "index", "etf"):
            minute = loader(remaining, trade_date, asset_type=asset_type)
            minute = self._filter_trade_date(minute, trade_date)
            if not minute.is_empty():
                frames.append(minute)
                remaining = self._missing_symbols(minute, remaining)
                if not remaining:
                    break
        if not frames:
            return pl.DataFrame()
        minute = (
            pl.concat(frames, how="diagonal_relaxed")
            .unique(subset=["symbol", "datetime"], keep="first")
            .sort(["symbol", "datetime"])
        )
        return self._ensure_raw_price_columns(minute)

    def _load_audited_minute_gaps(self) -> pl.DataFrame:
        path = (
            self.data_dir
            / "user_data"
            / "investment_expert"
            / "history_backfill"
            / "huggingface_no_bar_day_classification.parquet"
        )
        if not path.exists():
            return pl.DataFrame()
        try:
            audit = pl.read_parquet(
                path,
                columns=["symbol", "exchange", "date", "classification"],
            )
        except (OSError, pl.exceptions.PolarsError):
            return pl.DataFrame()
        return (
            audit.filter(
                pl.col("classification") == "verified_trading_day_missing_minutes"
            )
            .with_columns(
                (pl.col("symbol") + pl.lit(".") + pl.col("exchange")).alias("symbol")
            )
            .select(pl.col("date").alias("trade_date"), "symbol")
            .unique()
        )

    @staticmethod
    def _audited_gap_symbols(
        audit: pl.DataFrame,
        trade_date: date,
        required_symbols: list[str],
    ) -> list[str]:
        if audit.is_empty():
            return []
        return sorted(
            str(value)
            for value in audit.filter(
                (pl.col("trade_date") == trade_date)
                & pl.col("symbol").is_in(required_symbols)
            )["symbol"].to_list()
        )

    @staticmethod
    def _ensure_raw_price_columns(minute: pl.DataFrame) -> pl.DataFrame:
        if minute.is_empty():
            return minute
        aliases = [
            pl.col(column).cast(pl.Float64, strict=False).alias(f"raw_{column}")
            for column in ("open", "high", "low", "close")
            if f"raw_{column}" not in minute.columns and column in minute.columns
        ]
        return minute.with_columns(aliases) if aliases else minute

    @staticmethod
    def _missing_symbols(frame: pl.DataFrame, required_symbols: list[str]) -> list[str]:
        if frame.is_empty() or "symbol" not in frame.columns:
            return required_symbols
        counts = frame.group_by("symbol").len().filter(pl.col("len") >= 3)
        present = {str(symbol) for symbol in counts["symbol"].to_list()}
        return sorted(set(required_symbols) - present)

    @classmethod
    def _minute_frame_valid(
        cls,
        frame: pl.DataFrame,
        required_symbols: list[str],
        *,
        trade_date: date,
        allowed_missing_symbols: list[str] | None = None,
    ) -> bool:
        required_columns = {
            "symbol",
            "datetime",
            "raw_open",
            "raw_high",
            "raw_low",
            "raw_close",
            "previous_close",
            "is_suspended",
            "is_limit_up",
            "is_limit_down",
        }
        if frame.is_empty() or not required_columns.issubset(frame.columns):
            return False
        if cls._filter_trade_date(frame, trade_date).height != frame.height:
            return False
        missing = set(cls._missing_symbols(frame, required_symbols))
        return not missing.difference(allowed_missing_symbols or [])

    @staticmethod
    def _parquet_row_count(path: Path) -> int:
        return int(pl.scan_parquet(path).select(pl.len()).collect().item())

    @staticmethod
    def _minute_partition_valid(
        path: Path,
        required_symbols: list[str],
        *,
        trade_date: date,
        allowed_missing_symbols: list[str] | None = None,
    ) -> bool:
        required_columns = {
            "symbol",
            "datetime",
            "raw_open",
            "raw_high",
            "raw_low",
            "raw_close",
            "previous_close",
            "is_suspended",
            "is_limit_up",
            "is_limit_down",
        }
        try:
            schema = pl.read_parquet_schema(path)
            if not required_columns.issubset(schema):
                return False
            frame = pl.read_parquet(path)
        except Exception:
            return False
        return TrainingDatasetBuilder._minute_frame_valid(
            frame,
            required_symbols,
            trade_date=trade_date,
            allowed_missing_symbols=allowed_missing_symbols,
        )

    @staticmethod
    def _add_execution_flags(
        minute: pl.DataFrame,
        daily: pl.DataFrame,
        trade_date: date,
    ) -> pl.DataFrame:
        raw_daily = (
            pl.coalesce("raw_close", "close")
            if "raw_close" in daily.columns
            else pl.col("close")
        )
        context = daily.sort(["symbol", "date"]).with_columns(
            raw_daily.shift(1).over("symbol").alias("previous_close")
        ).filter(pl.col("date") == trade_date)
        if "name" not in context.columns:
            context = context.with_columns(pl.lit("").alias("name"))
        context = context.select("symbol", "previous_close", "name").unique("symbol")
        frame = minute.join(context, on="symbol", how="left").with_columns(
            pl.col("name").fill_null("").alias("instrument_name")
        )
        limit_pct = polars_price_limit_pct(
            pl.col("symbol"),
            pl.lit(trade_date),
            polars_is_risk_warning_name(pl.col("instrument_name")),
        )
        limit_up = polars_limit_price(pl.col("previous_close"), limit_pct, up=True)
        limit_down = polars_limit_price(pl.col("previous_close"), limit_pct, up=False)
        spread = (
            pl.max_horizontal("raw_open", "raw_high", "raw_low", "raw_close")
            - pl.min_horizontal("raw_open", "raw_high", "raw_low", "raw_close")
        )
        tolerance = pl.max_horizontal(pl.col("raw_close").abs() * 1e-4, pl.lit(0.01))
        one_price = spread <= tolerance
        return frame.with_columns(
            ((pl.col("volume") <= 0) | pl.col("previous_close").is_null()).alias(
                "is_suspended"
            ),
            (one_price & ((pl.col("raw_close") - limit_up).abs() < 0.005)).alias(
                "is_limit_up"
            ),
            (one_price & ((pl.col("raw_close") - limit_down).abs() < 0.005)).alias(
                "is_limit_down"
            ),
        ).drop("name")
