"""从公开 Hugging Face 归档按候选股范围补齐 A 股分钟历史。

上游按股票保存独立 Parquet。本模块只读取请求窗口对应的数据, 与 Point-in-Time
候选股票和交易日配对后写入规范化本地分区, 不覆盖已有的全市场分钟文件。
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
from typing import Final

import duckdb
import httpx
import polars as pl

# 历史分钟补齐任务专用日志记录器。
LOGGER: Final[logging.Logger] = logging.getLogger("backfill_ai_training_minutes")
# 公开 A 股一分钟归档的数据集标识。
HF_REPOSITORY: Final[str] = "neigezhu/china-a-share-1min-ohlcv"
# 主分支文件下载根地址。
HF_RESOLVE_ROOT: Final[str] = f"https://huggingface.co/datasets/{HF_REPOSITORY}/resolve/main"
# 数据集覆盖范围元数据路径。
HF_SUMMARY_REPO_PATH: Final[str] = "metadata/summary.json"
# 写入本地分钟分区的 AI 专用补齐文件名。
BACKFILL_FILENAME: Final[str] = "ai_training_history.parquet"
# 上游无分钟线交易日审计文件路径。
HF_AUDIT_REPO_PATH: Final[str] = "viewer/no_bar_day_classification.parquet"
# 本地缓存的上游缺口审计文件名。
AUDITED_GAPS_FILENAME: Final[str] = "huggingface_no_bar_day_classification.parquet"
# 允许作为已验证缺口继续处理的上游分类值。
VERIFIED_GAP_CLASSIFICATION: Final[str] = "verified_trading_day_missing_minutes"
# 回填身份与清单使用的结构版本。
BACKFILL_SCHEMA_VERSION: Final[int] = 2
# 本地分钟仓库要求的标准字段顺序。
CANONICAL_COLUMNS: Final[list[str]] = [
    "symbol",
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
]
# 可恢复查询批次必须完整包含全部规范字段。
CANONICAL_REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(CANONICAL_COLUMNS)
# 原始股票文件必须包含的上游字段。
RAW_REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "symbol",
        "exchange",
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
    }
)
# 无分钟线审计文件必须包含的上游字段。
AUDIT_REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "symbol",
        "exchange",
        "date",
        "classification",
        "evidence",
    }
)


@dataclass(frozen=True)
class ArchiveCoverage:
    """Hugging Face 分钟归档的可用日期范围与版本。"""

    # 归档覆盖的首个交易日期。
    first_date: date
    # 归档覆盖的最后交易日期。
    last_date: date
    # 上游快照构建时间。
    built_at: str | None = None
    # HTTP 响应声明的仓库提交版本。
    revision: str | None = None


@dataclass(frozen=True, slots=True)
class ArchiveBatchQuery:
    """单个归档查询批次的输入与重试选项。"""

    # 全窗口候选股票日期对。
    pairs: pl.DataFrame
    # 当前批次读取的规范股票代码。
    symbols: list[str]
    # 当前批次的原子输出文件。
    output_path: Path
    # 查询窗口首日。
    start_date: date
    # 查询窗口末日。
    end_date: date
    # 可选的本地原始股票文件根目录。
    raw_dir: Path | None = None
    # 固定读取的上游仓库版本。
    revision: str = "main"
    # DuckDB 或文件错误的最大尝试次数。
    attempts: int = 4
    # 当前批次序号, 仅用于日志和进度通知。
    batch_index: int = 0
    # 本次任务总批次数, 仅用于日志和进度通知。
    total_batches: int = 0
    # 可选的批次进度回调。
    progress_cb: Callable[[int, int, str], None] | None = None


@dataclass(frozen=True, slots=True)
class ArchiveBackfillRequest:
    """候选股分钟归档回填请求。"""

    data_dir: Path
    candidate_dir: Path
    start_date: date
    end_date: date
    batch_size: int = 50
    threads: int = 8
    download_raw: bool = False
    raw_workers: int = 8
    progress_cb: Callable[[int, int, str], None] | None = None
    revision: str = "main"


@dataclass(frozen=True, slots=True)
class ArchiveBackfillPlan:
    """经校验并解析完成的归档回填执行计划。"""

    request: ArchiveBackfillRequest
    pairs: pl.DataFrame
    symbols: list[str]
    staging_root: Path
    batch_dir: Path
    partition_dir: Path
    raw_dir: Path | None
    audit_path: Path


@dataclass(frozen=True, slots=True)
class ArchiveBackfillResult:
    """归档查询、覆盖校验和本地合并结果。"""

    covered_pairs: int
    missing: pl.DataFrame
    verified_missing: pl.DataFrame
    written_rows: int
    partition_count: int


def parse_archive_coverage(
    payload: dict[str, object],
    *,
    revision: str | None = None,
) -> ArchiveCoverage:
    """解析并校验数据集发布的快照覆盖范围。"""
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
    """构造样本所需的 candidate(T) 与 candidate(T-1) 股票日期对。"""
    rows: list[dict[str, object]] = []
    previous_symbols: set[str] = set()
    files = sorted(candidate_dir.glob("date=*/part.parquet"))
    for path in files:
        trade_date = date.fromisoformat(path.parent.name.removeprefix("date="))
        if trade_date > end_date:
            break
        frame = pl.read_parquet(path, columns=["symbol"])
        current_symbols = {
            str(symbol) for symbol in frame["symbol"].drop_nulls().unique().to_list()
        }
        if trade_date < start_date:
            previous_symbols = current_symbols
            continue
        for symbol in sorted(current_symbols | previous_symbols):
            rows.append({"trade_date": trade_date, "symbol": symbol})
        previous_symbols = current_symbols
    if not rows:
        return pl.DataFrame(
            schema={"trade_date": pl.Date, "symbol": pl.String},
        )
    return pl.DataFrame(rows).unique().sort(["trade_date", "symbol"])


def _sql_literal(value: str | Path) -> str:
    """把受控路径转义为 DuckDB 字符串字面量。"""
    return "'" + str(value).replace("\\", "/").replace("'", "''") + "'"


def _symbol_repo_path(symbol: str) -> str:
    """把规范 A 股代码映射到归档仓库中的 Parquet 路径。"""
    parts = symbol.upper().split(".", maxsplit=1)
    if len(parts) != 2 or parts[1] not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"unsupported canonical A-share symbol: {symbol}")
    code, exchange = parts
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"unsupported canonical A-share symbol: {symbol}")
    return f"data/stock_1m/{exchange}/{code}.parquet"


def _resolve_root(revision: str) -> str:
    """返回指定上游仓库版本的文件下载根地址。"""
    return f"https://huggingface.co/datasets/{HF_REPOSITORY}/resolve/{revision}"


def _symbol_source(
    symbol: str,
    *,
    raw_dir: Path | None,
    revision: str = "main",
) -> str:
    """返回单只股票的本地快照路径或远端归档地址。"""
    repo_path = _symbol_repo_path(symbol)
    if raw_dir is not None:
        return str(raw_dir / repo_path)
    return f"{_resolve_root(revision)}/{repo_path}"


def _is_valid_parquet(path: Path, required_columns: frozenset[str]) -> bool:
    """检查文件存在、非空且包含指定 Parquet 字段。"""
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        schema = pl.read_parquet_schema(path)
    except (OSError, pl.exceptions.PolarsError):
        return False
    return required_columns.issubset(schema)


def _curl_download_command(curl: str, source: str, target: Path) -> list[str]:
    """构造带断点续传和有限重试的 curl 下载命令。"""
    return [
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
        str(target),
        source,
    ]


def _download_raw_symbol(
    symbol: str,
    raw_dir: Path,
    revision: str,
    curl: str,
) -> tuple[str, int, bool]:
    """下载并原子安装单只股票原始文件, 返回大小和缓存命中状态。"""
    target = raw_dir / _symbol_repo_path(symbol)
    if _is_valid_parquet(target, RAW_REQUIRED_COLUMNS):
        return symbol, target.stat().st_size, True
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".parquet.part")
    source = _symbol_source(symbol, raw_dir=None, revision=revision)
    result = subprocess.run(
        _curl_download_command(curl, source, partial),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"curl failed for {symbol} ({result.returncode}): {result.stderr.strip()}"
        )
    if not _is_valid_parquet(partial, RAW_REQUIRED_COLUMNS):
        raise RuntimeError(f"downloaded parquet validation failed for {symbol}")
    partial.replace(target)
    return symbol, target.stat().st_size, False


def download_raw_snapshot(
    symbols: list[str],
    *,
    raw_dir: Path,
    workers: int,
    revision: str = "main",
) -> None:
    """并发断点下载原始股票文件并校验 Parquet 结构。"""
    curl = shutil.which("curl")
    if curl is None:
        raise RuntimeError("raw snapshot download requires curl in PATH")
    raw_dir.mkdir(parents=True, exist_ok=True)
    completed = 0
    downloaded_bytes = 0
    resumed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_download_raw_symbol, symbol, raw_dir, revision, curl): symbol
            for symbol in symbols
        }
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
    """下载并校验上游按股票和交易日发布的无分钟线审计。"""
    revision_path = target.with_suffix(".revision")
    cached_revision = None
    with suppress(OSError):
        cached_revision = revision_path.read_text(encoding="utf-8").strip()
    if _is_valid_parquet(target, AUDIT_REQUIRED_COLUMNS) and cached_revision == revision:
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
    if not _is_valid_parquet(partial, AUDIT_REQUIRED_COLUMNS):
        raise RuntimeError("downloaded no-bar audit validation failed")
    partial.replace(target)
    revision_tmp = revision_path.with_suffix(".revision.tmp")
    revision_tmp.write_text(revision, encoding="utf-8")
    revision_tmp.replace(revision_path)


def _batch_sources_sql(request: ArchiveBatchQuery) -> str:
    """构造当前批次所有来源文件的 DuckDB 列表字面量。"""
    return (
        "["
        + ",".join(
            _sql_literal(
                _symbol_source(
                    symbol,
                    raw_dir=request.raw_dir,
                    revision=request.revision,
                )
            )
            for symbol in request.symbols
        )
        + "]"
    )


def _batch_query_sql(
    request: ArchiveBatchQuery,
    pair_path: Path,
    temp_path: Path,
) -> str:
    """构造候选股票批次的 DuckDB COPY 查询。"""
    sources_sql = _batch_sources_sql(request)
    range_end = request.end_date + timedelta(days=1)
    return f"""
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
            WHERE raw.timestamp >= TIMESTAMP '{request.start_date.isoformat()}'
              AND raw.timestamp < TIMESTAMP '{range_end.isoformat()}'
        ) TO {_sql_literal(temp_path)} (
            FORMAT PARQUET,
            COMPRESSION ZSTD
        )
    """


def _query_batch(
    connection: duckdb.DuckDBPyConnection,
    request: ArchiveBatchQuery,
) -> None:
    """查询一批候选股票分钟线并原子替换批次文件。"""
    request.output_path.parent.mkdir(parents=True, exist_ok=True)
    pair_path = request.output_path.with_suffix(".pairs.parquet")
    temp_path = request.output_path.with_suffix(".parquet.tmp")
    request.pairs.filter(pl.col("symbol").is_in(request.symbols)).write_parquet(pair_path)
    query = _batch_query_sql(request, pair_path, temp_path)
    for attempt in range(1, request.attempts + 1):
        try:
            temp_path.unlink(missing_ok=True)
            connection.execute(query)
            temp_path.replace(request.output_path)
            pair_path.unlink(missing_ok=True)
            return
        except (duckdb.Error, OSError):
            temp_path.unlink(missing_ok=True)
            if attempt == request.attempts:
                raise
            delay = min(30, 2**attempt)
            LOGGER.warning(
                "batch %s failed on attempt %s/%s; retrying in %ss",
                request.output_path.name,
                attempt,
                request.attempts,
                delay,
                exc_info=True,
            )
            time.sleep(delay)


def _safe_remove_tree(path: Path, *, parent: Path) -> None:
    """仅允许递归删除指定暂存根目录内的子目录。"""
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
    """把已完成批次按交易日重新分区到安全暂存目录。"""
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
    """合并单日回填分区且不覆盖正式分钟文件中的已有键。"""
    incoming = pl.read_parquet(source_dir / "*.parquet").select(CANONICAL_COLUMNS)
    if incoming.is_empty():
        return 0
    incoming = incoming.unique(subset=["symbol", "datetime"], keep="last")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / BACKFILL_FILENAME
    base_files = [path for path in target_dir.glob("*.parquet") if path.name != BACKFILL_FILENAME]
    frames = [incoming]
    if target_path.exists():
        frames.append(pl.read_parquet(target_path).select(CANONICAL_COLUMNS))
    merged = pl.concat(frames, how="diagonal_relaxed").unique(
        subset=["symbol", "datetime"],
        keep="last",
    )
    if base_files:
        base_keys = pl.scan_parquet(base_files).select("symbol", "datetime").unique().collect()
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
    """统计已覆盖的股票日期对并返回仍缺失的明细。"""
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
    """返回上游已确认缺口, 并拒绝任何未分类的分钟线缺失。"""
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
        .with_columns((pl.col("symbol") + pl.lit(".") + pl.col("exchange")).alias("symbol"))
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
            f"minute coverage contains gaps not verified by the upstream audit: {sample}"
        )
    return classified.sort(["trade_date", "symbol"])


def _validate_backfill_request(request: ArchiveBackfillRequest) -> None:
    """在读取候选数据和访问网络前校验回填请求。"""
    if request.start_date > request.end_date:
        raise ValueError("start_date must not be after end_date")
    if request.batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    if request.threads <= 0:
        raise ValueError("threads must be greater than zero")
    if request.raw_workers <= 0:
        raise ValueError("raw_workers must be greater than zero")
    if not isinstance(request.revision, str) or not request.revision.strip():
        raise ValueError("revision must be a non-empty string")
    if request.progress_cb is not None and not callable(request.progress_cb):
        raise TypeError("progress_cb must be callable")


def _build_backfill_plan(request: ArchiveBackfillRequest) -> ArchiveBackfillPlan:
    """解析候选日期对并建立隔离的暂存目录计划。"""
    _validate_backfill_request(request)
    pairs = build_candidate_pairs(
        request.candidate_dir,
        start_date=request.start_date,
        end_date=request.end_date,
    )
    if pairs.is_empty():
        raise ValueError("no candidate symbol/date pairs found in the requested window")
    symbols = sorted(str(value) for value in pairs["symbol"].unique().to_list())
    identity_payload = (
        f"schema={BACKFILL_SCHEMA_VERSION}\nrepo={HF_REPOSITORY}\n"
        f"revision={request.revision}\n" + pairs.write_json()
    )
    identity = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()[:16]
    history_root = request.data_dir / "user_data" / "investment_expert" / "history_backfill"
    staging_root = history_root / identity
    batch_dir = staging_root / "batches"
    partition_dir = staging_root / "partitioned"
    batch_dir.mkdir(parents=True, exist_ok=True)
    revision_dir = (
        request.revision
        if request.revision.isalnum()
        else hashlib.sha256(request.revision.encode("utf-8")).hexdigest()[:16]
    )
    raw_dir = history_root / "huggingface_raw" / revision_dir if request.download_raw else None
    return ArchiveBackfillPlan(
        request=request,
        pairs=pairs,
        symbols=symbols,
        staging_root=staging_root,
        batch_dir=batch_dir,
        partition_dir=partition_dir,
        raw_dir=raw_dir,
        audit_path=history_root / AUDITED_GAPS_FILENAME,
    )


def _prepare_backfill_sources(plan: ArchiveBackfillPlan) -> None:
    """下载上游缺口审计, 并按配置准备本地原始快照。"""
    request = plan.request
    download_audit_snapshot(target=plan.audit_path, revision=request.revision)
    if plan.raw_dir is not None:
        LOGGER.info(
            "downloading %s source files through resumable curl downloads",
            len(plan.symbols),
        )
        download_raw_snapshot(
            plan.symbols,
            raw_dir=plan.raw_dir,
            workers=request.raw_workers,
            revision=request.revision,
        )


def _notify_backfill_progress(query: ArchiveBatchQuery) -> None:
    """在配置回调时报告已完成的归档批次。"""
    if query.progress_cb is not None:
        query.progress_cb(
            query.batch_index,
            query.total_batches,
            f"Hugging Face {query.batch_index}/{query.total_batches}",
        )


def _query_or_resume_batch(
    connection: duckdb.DuckDBPyConnection,
    query: ArchiveBatchQuery,
) -> None:
    """复用有效批次, 否则查询归档并原子写入。"""
    if _is_valid_parquet(query.output_path, CANONICAL_REQUIRED_COLUMNS):
        LOGGER.info(
            "batch %s/%s already exists; resuming",
            query.batch_index,
            query.total_batches,
        )
        _notify_backfill_progress(query)
        return
    query.output_path.unlink(missing_ok=True)
    LOGGER.info(
        "reading %s batch %s/%s (%s symbols)",
        "local" if query.raw_dir is not None else "remote",
        query.batch_index,
        query.total_batches,
        len(query.symbols),
    )
    _query_batch(connection, query)
    _notify_backfill_progress(query)


def _run_backfill_queries(plan: ArchiveBackfillPlan) -> None:
    """按批查询分钟归档并生成按交易日分区的暂存结果。"""
    request = plan.request
    connection = duckdb.connect(database=":memory:")
    connection.execute(f"SET threads = {request.threads}")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET enable_progress_bar = false")
    try:
        total = (len(plan.symbols) + request.batch_size - 1) // request.batch_size
        offsets = range(0, len(plan.symbols), request.batch_size)
        for batch_index, offset in enumerate(offsets, start=1):
            query = ArchiveBatchQuery(
                pairs=plan.pairs,
                symbols=plan.symbols[offset : offset + request.batch_size],
                output_path=plan.batch_dir / f"batch-{batch_index:05d}.parquet",
                start_date=request.start_date,
                end_date=request.end_date,
                raw_dir=plan.raw_dir,
                revision=request.revision,
                batch_index=batch_index,
                total_batches=total,
                progress_cb=request.progress_cb,
            )
            _query_or_resume_batch(connection, query)
        _partition_batches(
            connection,
            batch_dir=plan.batch_dir,
            partition_dir=plan.partition_dir,
            staging_root=plan.staging_root,
        )
    finally:
        connection.close()


def _collect_backfill_result(plan: ArchiveBackfillPlan) -> ArchiveBackfillResult:
    """校验覆盖缺口并合并规范化本地分钟分区。"""
    covered_pairs, missing = _coverage_report(plan.pairs, batch_dir=plan.batch_dir)
    verified_missing = _validate_missing_against_audit(
        missing,
        audit_path=plan.audit_path,
    )
    written_rows = 0
    partition_count = 0
    minute_root = plan.request.data_dir / "kline_minute"
    for source_dir in sorted(plan.partition_dir.glob("date=*")):
        target_dir = minute_root / source_dir.name
        written_rows += _merge_local_partition(source_dir, target_dir)
        partition_count += 1
    return ArchiveBackfillResult(
        covered_pairs=covered_pairs,
        missing=missing,
        verified_missing=verified_missing,
        written_rows=written_rows,
        partition_count=partition_count,
    )


def _build_backfill_manifest(
    plan: ArchiveBackfillPlan,
    result: ArchiveBackfillResult,
) -> dict[str, object]:
    """构造可审计且可追溯到固定上游版本的回填清单。"""
    request = plan.request
    return {
        "schema_version": BACKFILL_SCHEMA_VERSION,
        "source": f"huggingface:{HF_REPOSITORY}",
        "source_revision": request.revision,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "candidate_dir": str(request.candidate_dir.resolve()),
        "candidate_pairs": plan.pairs.height,
        "covered_symbol_dates": result.covered_pairs,
        "missing_symbol_dates": result.missing.height,
        "verified_upstream_gap_symbol_dates": result.verified_missing.height,
        "no_bar_audit_path": str(plan.audit_path.resolve()),
        "symbols": len(plan.symbols),
        "partitions": result.partition_count,
        "local_backfill_rows": result.written_rows,
        "volume_contract": "source shares converted to canonical lots (shares / 100)",
        "staging_dir": str(plan.staging_root.resolve()),
        "raw_dir": str(plan.raw_dir.resolve()) if plan.raw_dir is not None else None,
    }


def _write_backfill_audit(
    plan: ArchiveBackfillPlan,
    result: ArchiveBackfillResult,
    manifest: dict[str, object],
) -> None:
    """把覆盖缺口和回填清单写入本次隔离的暂存目录。"""
    plan.staging_root.mkdir(parents=True, exist_ok=True)
    result.missing.write_parquet(plan.staging_root / "missing_symbol_dates.parquet")
    result.verified_missing.write_parquet(
        plan.staging_root / "verified_upstream_gap_symbol_dates.parquet"
    )
    manifest_path = plan.staging_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def backfill(request: ArchiveBackfillRequest) -> dict[str, object]:
    """按候选股范围补齐历史分钟线并写入审计清单。"""
    plan = _build_backfill_plan(request)
    _prepare_backfill_sources(plan)
    _run_backfill_queries(plan)
    result = _collect_backfill_result(plan)
    manifest = _build_backfill_manifest(plan, result)
    _write_backfill_audit(plan, result, manifest)
    return manifest


class HuggingFaceAshareMinuteArchive:
    """在 TickFlow 无法覆盖请求窗口时提供批量历史分钟回退。

    该适配器缓存上游覆盖元数据, 并把候选股窗口委托给受审计的回填流程。
    网络或数据结构异常会直接抛出, 不会静默伪造完整覆盖。
    """

    name = f"huggingface:{HF_REPOSITORY}"

    def __init__(self, data_dir: Path, *, metadata_timeout: float = 30.0) -> None:
        """初始化归档适配器。

        Args:
            data_dir: 应用用户数据根目录。
            metadata_timeout: 元数据 HTTP 请求超时秒数。
        """
        self.data_dir = data_dir
        self.metadata_timeout = metadata_timeout
        self._coverage: ArchiveCoverage | None = None

    def coverage(self) -> ArchiveCoverage:
        """读取并缓存上游归档覆盖范围。"""
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
        """按候选目录与日期窗口补齐本地分钟历史。"""
        coverage = self.coverage()
        return backfill(
            ArchiveBackfillRequest(
                data_dir=self.data_dir,
                candidate_dir=candidate_dir,
                start_date=start_date,
                end_date=end_date,
                progress_cb=progress_cb,
                revision=coverage.revision or "main",
            )
        )
