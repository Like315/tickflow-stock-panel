"""Candidate-scoped A-share minute history from a public Hugging Face archive.

The upstream dataset is stored as one Parquet file per A-share symbol.  This
The loader reads only the row groups covering the requested window, joins them to
Point-in-Time AI candidate symbol/date pairs, and writes canonical local minute
partitions without replacing existing full-market files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import httpx
import polars as pl

LOGGER = logging.getLogger("backfill_ai_training_minutes")
HF_REPOSITORY = "neigezhu/china-a-share-1min-ohlcv"
HF_RESOLVE_ROOT = f"https://huggingface.co/datasets/{HF_REPOSITORY}/resolve/main"
HF_SUMMARY_REPO_PATH = "metadata/summary.json"
BACKFILL_FILENAME = "ai_training_history.parquet"
HF_AUDIT_REPO_PATH = "viewer/no_bar_day_classification.parquet"
AUDITED_GAPS_FILENAME = "huggingface_no_bar_day_classification.parquet"
VERIFIED_GAP_CLASSIFICATION = "verified_trading_day_missing_minutes"
BACKFILL_SCHEMA_VERSION = 2
CANONICAL_COLUMNS = [
    "symbol",
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
]


@dataclass(frozen=True)
class ArchiveCoverage:
    first_date: date
    last_date: date
    built_at: str | None = None
    revision: str | None = None


def parse_archive_coverage(
    payload: dict[str, object],
    *,
    revision: str | None = None,
) -> ArchiveCoverage:
    """Parse and validate the snapshot bounds published by the dataset."""
    try:
        first = datetime.fromisoformat(str(payload["first_timestamp"])).date()
        last = datetime.fromisoformat(str(payload["last_timestamp"])).date()
    except (KeyError, ValueError) as exc:
        raise RuntimeError("Hugging Face minute archive metadata is invalid") from exc
    if first > last:
        raise RuntimeError("Hugging Face minute archive metadata has reversed bounds")
    built_at = payload.get("built_at")
    return ArchiveCoverage(
        first_date=first,
        last_date=last,
        built_at=str(built_at) if built_at else None,
        revision=revision,
    )


def build_candidate_pairs(
    candidate_dir: Path,
    *,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Return candidate(T) union candidate(T-1) pairs used by the builder."""
    rows: list[dict[str, object]] = []
    previous_symbols: set[str] = set()
    files = sorted(candidate_dir.glob("date=*/part.parquet"))
    for path in files:
        trade_date = date.fromisoformat(path.parent.name.removeprefix("date="))
        if trade_date < start_date or trade_date > end_date:
            continue
        frame = pl.read_parquet(path, columns=["symbol"])
        current_symbols = {
            str(symbol)
            for symbol in frame["symbol"].drop_nulls().unique().to_list()
        }
        for symbol in sorted(current_symbols | previous_symbols):
            rows.append({"trade_date": trade_date, "symbol": symbol})
        previous_symbols = current_symbols
    if not rows:
        return pl.DataFrame(
            schema={"trade_date": pl.Date, "symbol": pl.String},
        )
    return pl.DataFrame(rows).unique().sort(["trade_date", "symbol"])


def _sql_literal(value: str | Path) -> str:
    return "'" + str(value).replace("\\", "/").replace("'", "''") + "'"


def _symbol_repo_path(symbol: str) -> str:
    parts = symbol.upper().split(".", maxsplit=1)
    if len(parts) != 2 or parts[1] not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"unsupported canonical A-share symbol: {symbol}")
    code, exchange = parts
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"unsupported canonical A-share symbol: {symbol}")
    return f"data/stock_1m/{exchange}/{code}.parquet"


def _resolve_root(revision: str) -> str:
    return f"https://huggingface.co/datasets/{HF_REPOSITORY}/resolve/{revision}"


def _symbol_source(
    symbol: str,
    *,
    raw_dir: Path | None,
    revision: str = "main",
) -> str:
    repo_path = _symbol_repo_path(symbol)
    if raw_dir is not None:
        return str(raw_dir / repo_path)
    return f"{_resolve_root(revision)}/{repo_path}"


def download_raw_snapshot(
    symbols: list[str],
    *,
    raw_dir: Path,
    workers: int,
    revision: str = "main",
) -> None:
    """Download resumable source files and validate their Parquet footers."""
    curl = shutil.which("curl")
    if curl is None:
        raise RuntimeError("raw snapshot download requires curl in PATH")
    raw_dir.mkdir(parents=True, exist_ok=True)

    def valid(path: Path) -> bool:
        if not path.exists() or path.stat().st_size == 0:
            return False
        try:
            schema = pl.read_parquet_schema(path)
        except (OSError, pl.exceptions.PolarsError):
            return False
        return {
            "symbol",
            "exchange",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
        }.issubset(schema)

    def download(symbol: str) -> tuple[str, int, bool]:
        target = raw_dir / _symbol_repo_path(symbol)
        if valid(target):
            return symbol, target.stat().st_size, True
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".parquet.part")
        command = [
            curl,
            "--location",
            "--fail",
            "--silent",
            "--show-error",
            "--retry",
            "8",
            "--retry-all-errors",
            "--retry-delay",
            "2",
            "--connect-timeout",
            "30",
            "--max-time",
            "1800",
            "--continue-at",
            "-",
            "--output",
            str(partial),
            _symbol_source(symbol, raw_dir=None, revision=revision),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"curl failed for {symbol} ({result.returncode}): {result.stderr.strip()}"
            )
        if not valid(partial):
            raise RuntimeError(f"downloaded parquet validation failed for {symbol}")
        partial.replace(target)
        return symbol, target.stat().st_size, False

    completed = 0
    downloaded_bytes = 0
    resumed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(download, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol, size, existed = future.result()
            completed += 1
            downloaded_bytes += size
            resumed += int(existed)
            if completed % 25 == 0 or completed == len(symbols):
                LOGGER.info(
                    "raw download %s/%s (%.2f GiB verified, %s resumed; latest %s)",
                    completed,
                    len(symbols),
                    downloaded_bytes / 1024**3,
                    resumed,
                    symbol,
                )


def download_audit_snapshot(*, target: Path, revision: str = "main") -> None:
    """Download and validate the upstream per-symbol/date no-bar audit."""
    required_columns = {"symbol", "exchange", "date", "classification", "evidence"}

    def valid(path: Path) -> bool:
        if not path.exists() or path.stat().st_size == 0:
            return False
        try:
            schema = pl.read_parquet_schema(path)
        except (OSError, pl.exceptions.PolarsError):
            return False
        return required_columns.issubset(schema)

    revision_path = target.with_suffix(".revision")
    cached_revision = None
    with suppress(OSError):
        cached_revision = revision_path.read_text(encoding="utf-8").strip()
    if valid(target) and cached_revision == revision:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".parquet.part")
    partial.unlink(missing_ok=True)
    timeout = httpx.Timeout(600.0, connect=30.0)
    with httpx.stream(
        "GET",
        f"{_resolve_root(revision)}/{HF_AUDIT_REPO_PATH}",
        follow_redirects=True,
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        with partial.open("wb") as output:
            for chunk in response.iter_bytes():
                output.write(chunk)
    if not valid(partial):
        raise RuntimeError("downloaded no-bar audit validation failed")
    partial.replace(target)
    revision_tmp = revision_path.with_suffix(".revision.tmp")
    revision_tmp.write_text(revision, encoding="utf-8")
    revision_tmp.replace(revision_path)


def _query_batch(
    connection: duckdb.DuckDBPyConnection,
    *,
    pairs: pl.DataFrame,
    symbols: list[str],
    output_path: Path,
    start_date: date,
    end_date: date,
    raw_dir: Path | None = None,
    revision: str = "main",
    attempts: int = 4,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pair_path = output_path.with_suffix(".pairs.parquet")
    temp_path = output_path.with_suffix(".parquet.tmp")
    pairs.filter(pl.col("symbol").is_in(symbols)).write_parquet(pair_path)
    sources_sql = "[" + ",".join(
        _sql_literal(
            _symbol_source(symbol, raw_dir=raw_dir, revision=revision)
        )
        for symbol in symbols
    ) + "]"
    range_end = end_date + timedelta(days=1)
    query = f"""
        COPY (
            SELECT
                concat(raw.symbol, '.', raw.exchange) AS symbol,
                CAST(raw.timestamp AS TIMESTAMP) AS datetime,
                CAST(raw.open AS DOUBLE) AS open,
                CAST(raw.high AS DOUBLE) AS high,
                CAST(raw.low AS DOUBLE) AS low,
                CAST(raw.close AS DOUBLE) AS close,
                CAST(raw.volume AS DOUBLE) / 100.0 AS volume,
                CAST(raw.turnover AS DOUBLE) AS amount
            FROM read_parquet({sources_sql}) AS raw
            INNER JOIN read_parquet({_sql_literal(pair_path)}) AS pairs
                ON concat(raw.symbol, '.', raw.exchange) = pairs.symbol
                AND CAST(raw.timestamp AS DATE) = pairs.trade_date
            WHERE raw.timestamp >= TIMESTAMP '{start_date.isoformat()}'
              AND raw.timestamp < TIMESTAMP '{range_end.isoformat()}'
        ) TO {_sql_literal(temp_path)} (
            FORMAT PARQUET,
            COMPRESSION ZSTD
        )
    """
    for attempt in range(1, attempts + 1):
        try:
            temp_path.unlink(missing_ok=True)
            connection.execute(query)
            temp_path.replace(output_path)
            pair_path.unlink(missing_ok=True)
            return
        except (duckdb.Error, OSError):
            temp_path.unlink(missing_ok=True)
            if attempt == attempts:
                raise
            delay = min(30, 2**attempt)
            LOGGER.warning(
                "batch %s failed on attempt %s/%s; retrying in %ss",
                output_path.name,
                attempt,
                attempts,
                delay,
                exc_info=True,
            )
            time.sleep(delay)


def _safe_remove_tree(path: Path, *, parent: Path) -> None:
    resolved = path.resolve()
    resolved_parent = parent.resolve()
    if resolved == resolved_parent or resolved_parent not in resolved.parents:
        raise ValueError(f"refusing to remove unsafe staging path: {resolved}")
    if path.exists():
        shutil.rmtree(path)


def _partition_batches(
    connection: duckdb.DuckDBPyConnection,
    *,
    batch_dir: Path,
    partition_dir: Path,
    staging_root: Path,
) -> None:
    _safe_remove_tree(partition_dir, parent=staging_root)
    partition_dir.mkdir(parents=True, exist_ok=True)
    batch_glob = batch_dir / "batch-*.parquet"
    connection.execute("SET partitioned_write_max_open_files = 50")
    connection.execute(f"""
        COPY (
            SELECT *, CAST(datetime AS DATE) AS date
            FROM read_parquet({_sql_literal(batch_glob)})
        ) TO {_sql_literal(partition_dir)} (
            FORMAT PARQUET,
            COMPRESSION ZSTD,
            PARTITION_BY (date),
            OVERWRITE_OR_IGNORE
        )
    """)


def _merge_local_partition(source_dir: Path, target_dir: Path) -> int:
    incoming = pl.read_parquet(source_dir / "*.parquet").select(CANONICAL_COLUMNS)
    if incoming.is_empty():
        return 0
    incoming = incoming.unique(subset=["symbol", "datetime"], keep="last")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / BACKFILL_FILENAME
    base_files = [
        path
        for path in target_dir.glob("*.parquet")
        if path.name != BACKFILL_FILENAME
    ]
    frames = [incoming]
    if target_path.exists():
        frames.append(pl.read_parquet(target_path).select(CANONICAL_COLUMNS))
    merged = pl.concat(frames, how="diagonal_relaxed").unique(
        subset=["symbol", "datetime"],
        keep="last",
    )
    if base_files:
        base_keys = (
            pl.scan_parquet(base_files)
            .select("symbol", "datetime")
            .unique()
            .collect()
        )
        merged = merged.join(base_keys, on=["symbol", "datetime"], how="anti")
    if merged.is_empty():
        return 0
    merged = merged.sort(["symbol", "datetime"])
    temp_path = target_path.with_suffix(".parquet.tmp")
    merged.write_parquet(temp_path, compression="zstd")
    temp_path.replace(target_path)
    return merged.height


def _coverage_report(
    pairs: pl.DataFrame,
    *,
    batch_dir: Path,
) -> tuple[int, pl.DataFrame]:
    covered = (
        pl.scan_parquet(batch_dir / "batch-*.parquet")
        .select(
            pl.col("symbol"),
            pl.col("datetime").dt.date().alias("trade_date"),
        )
        .unique()
        .collect()
    )
    missing = pairs.join(covered, on=["trade_date", "symbol"], how="anti")
    return covered.height, missing.sort(["trade_date", "symbol"])


def _validate_missing_against_audit(
    missing: pl.DataFrame,
    *,
    audit_path: Path,
) -> pl.DataFrame:
    """Return verified upstream gaps and reject unclassified missing bars."""
    if missing.is_empty():
        return pl.DataFrame(
            schema={
                "trade_date": pl.Date,
                "symbol": pl.String,
                "classification": pl.String,
                "evidence": pl.String,
            }
        )
    audit = (
        pl.read_parquet(audit_path)
        .with_columns(
            (pl.col("symbol") + pl.lit(".") + pl.col("exchange")).alias("symbol")
        )
        .rename({"date": "trade_date"})
        .select("trade_date", "symbol", "classification", "evidence")
    )
    classified = missing.join(
        audit,
        on=["trade_date", "symbol"],
        how="left",
    )
    unexpected = classified.filter(
        pl.col("classification").fill_null("") != VERIFIED_GAP_CLASSIFICATION
    )
    if not unexpected.is_empty():
        sample = ", ".join(
            f"{row['trade_date']} {row['symbol']}"
            for row in unexpected.head(5).iter_rows(named=True)
        )
        raise RuntimeError(
            "minute coverage contains gaps not verified by the upstream audit: "
            f"{sample}"
        )
    return classified.sort(["trade_date", "symbol"])


def backfill(
    *,
    data_dir: Path,
    candidate_dir: Path,
    start_date: date,
    end_date: date,
    batch_size: int = 50,
    threads: int = 8,
    download_raw: bool = False,
    raw_workers: int = 8,
    progress_cb: Callable[[int, int, str], None] | None = None,
    revision: str = "main",
) -> dict[str, object]:
    pairs = build_candidate_pairs(
        candidate_dir,
        start_date=start_date,
        end_date=end_date,
    )
    if pairs.is_empty():
        raise ValueError("no candidate symbol/date pairs found in the requested window")
    symbols = sorted(str(value) for value in pairs["symbol"].unique().to_list())
    identity_payload = (
        f"schema={BACKFILL_SCHEMA_VERSION}\nrepo={HF_REPOSITORY}\n"
        f"revision={revision}\n"
        + pairs.write_json()
    )
    identity = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()[:16]
    history_root = data_dir / "user_data" / "investment_expert" / "history_backfill"
    staging_root = history_root / identity
    batch_dir = staging_root / "batches"
    partition_dir = staging_root / "partitioned"
    batch_dir.mkdir(parents=True, exist_ok=True)
    revision_dir = (
        revision
        if revision.isalnum()
        else hashlib.sha256(revision.encode("utf-8")).hexdigest()[:16]
    )
    raw_dir = (
        history_root / "huggingface_raw" / revision_dir
        if download_raw
        else None
    )
    audit_path = history_root / AUDITED_GAPS_FILENAME
    download_audit_snapshot(target=audit_path, revision=revision)
    if raw_dir is not None:
        LOGGER.info(
            "downloading %s source files through resumable curl downloads",
            len(symbols),
        )
        download_raw_snapshot(
            symbols,
            raw_dir=raw_dir,
            workers=raw_workers,
            revision=revision,
        )

    connection = duckdb.connect(database=":memory:")
    connection.execute(f"SET threads = {max(1, threads)}")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET enable_progress_bar = false")
    try:
        total_batches = (len(symbols) + batch_size - 1) // batch_size
        for batch_index, offset in enumerate(range(0, len(symbols), batch_size), start=1):
            batch_symbols = symbols[offset : offset + batch_size]
            output_path = batch_dir / f"batch-{batch_index:05d}.parquet"
            if output_path.exists():
                try:
                    pl.read_parquet_schema(output_path)
                    LOGGER.info(
                        "batch %s/%s already exists; resuming",
                        batch_index,
                        total_batches,
                    )
                    if progress_cb is not None:
                        progress_cb(
                            batch_index,
                            total_batches,
                            f"Hugging Face {batch_index}/{total_batches}",
                        )
                    continue
                except Exception:
                    output_path.unlink(missing_ok=True)
            LOGGER.info(
                "reading %s batch %s/%s (%s symbols)",
                "local" if raw_dir is not None else "remote",
                batch_index,
                total_batches,
                len(batch_symbols),
            )
            _query_batch(
                connection,
                pairs=pairs,
                symbols=batch_symbols,
                output_path=output_path,
                start_date=start_date,
                end_date=end_date,
                raw_dir=raw_dir,
                revision=revision,
            )
            if progress_cb is not None:
                progress_cb(
                    batch_index,
                    total_batches,
                    f"Hugging Face {batch_index}/{total_batches}",
                )

        _partition_batches(
            connection,
            batch_dir=batch_dir,
            partition_dir=partition_dir,
            staging_root=staging_root,
        )
    finally:
        connection.close()

    covered_pairs, missing = _coverage_report(pairs, batch_dir=batch_dir)
    verified_missing = _validate_missing_against_audit(
        missing,
        audit_path=audit_path,
    )

    written_rows = 0
    partition_count = 0
    minute_root = data_dir / "kline_minute"
    for source_dir in sorted(partition_dir.glob("date=*")):
        target_dir = minute_root / source_dir.name
        written_rows += _merge_local_partition(source_dir, target_dir)
        partition_count += 1

    manifest = {
        "schema_version": BACKFILL_SCHEMA_VERSION,
        "source": f"huggingface:{HF_REPOSITORY}",
        "source_revision": revision,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "candidate_dir": str(candidate_dir.resolve()),
        "candidate_pairs": pairs.height,
        "covered_symbol_dates": covered_pairs,
        "missing_symbol_dates": missing.height,
        "verified_upstream_gap_symbol_dates": verified_missing.height,
        "no_bar_audit_path": str(audit_path.resolve()),
        "symbols": len(symbols),
        "partitions": partition_count,
        "local_backfill_rows": written_rows,
        "volume_contract": "source shares converted to canonical lots (shares / 100)",
        "staging_dir": str(staging_root.resolve()),
        "raw_dir": str(raw_dir.resolve()) if raw_dir is not None else None,
    }
    staging_root.mkdir(parents=True, exist_ok=True)
    missing.write_parquet(staging_root / "missing_symbol_dates.parquet")
    verified_missing.write_parquet(
        staging_root / "verified_upstream_gap_symbol_dates.parquet"
    )
    manifest_path = staging_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


class HuggingFaceAshareMinuteArchive:
    """Batch archive adapter used when TickFlow cannot cover the requested window."""

    name = f"huggingface:{HF_REPOSITORY}"

    def __init__(self, data_dir: Path, *, metadata_timeout: float = 30.0) -> None:
        self.data_dir = data_dir
        self.metadata_timeout = metadata_timeout
        self._coverage: ArchiveCoverage | None = None

    def coverage(self) -> ArchiveCoverage:
        if self._coverage is not None:
            return self._coverage
        response = httpx.get(
            f"{HF_RESOLVE_ROOT}/{HF_SUMMARY_REPO_PATH}",
            follow_redirects=True,
            timeout=self.metadata_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Hugging Face minute archive metadata is not an object")
        self._coverage = parse_archive_coverage(
            payload,
            revision=response.headers.get("x-repo-commit"),
        )
        return self._coverage

    def backfill_candidates(
        self,
        *,
        candidate_dir: Path,
        start_date: date,
        end_date: date,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, object]:
        coverage = self.coverage()
        return backfill(
            data_dir=self.data_dir,
            candidate_dir=candidate_dir,
            start_date=start_date,
            end_date=end_date,
            progress_cb=progress_cb,
            revision=coverage.revision or "main",
        )
