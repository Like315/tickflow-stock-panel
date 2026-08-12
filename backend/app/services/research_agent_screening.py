"""全 A 股保守型量化预筛。

预筛只负责缩小研究候选范围，分数不直接映射为买卖结论。
"""
# ruff: noqa: RUF001, RUF002
from __future__ import annotations

from datetime import date

import polars as pl

from app.services.research_agent_models import CandidateScreenResult

_MIN_HISTORY_DAYS = 60
_DEFAULT_LIMIT = 25


def _finite_positive(column: str) -> pl.Expr:
    return pl.col(column).is_not_null() & pl.col(column).is_finite() & (pl.col(column) > 0)


def _ensure_columns(df: pl.DataFrame, defaults: dict[str, object]) -> pl.DataFrame:
    expressions = [pl.lit(value).alias(name) for name, value in defaults.items() if name not in df.columns]
    return df.with_columns(expressions) if expressions else df


def _percentile(column: str, *, descending: bool = False) -> pl.Expr:
    rank = pl.col(column).fill_null(pl.col(column).median()).rank(
        method="average", descending=descending
    )
    return (rank / pl.len()).fill_nan(0.5).fill_null(0.5)


def screen_dataframe(
    latest: pl.DataFrame,
    history: pl.DataFrame,
    instruments: pl.DataFrame | None = None,
    *,
    as_of: date | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> CandidateScreenResult:
    if latest.is_empty():
        return CandidateScreenResult(as_of=as_of, message="暂无最新 A 股指标数据")
    if history.is_empty() or "symbol" not in history.columns:
        return CandidateScreenResult(as_of=as_of, message="历史行情不足，无法进行 60 日预筛")

    df = latest
    if (
        "name" not in df.columns
        and instruments is not None
        and not instruments.is_empty()
        and {"symbol", "name"}.issubset(instruments.columns)
    ):
        df = df.join(instruments.select(["symbol", "name"]), on="symbol", how="left")
    df = _ensure_columns(
        df,
        {
            "name": "",
            "volume": 0.0,
            "amount": None,
            "close": None,
            "raw_close": None,
            "ma20": None,
            "ma60": None,
            "momentum_20d": None,
            "annual_vol_20d": None,
            "rsi_14": None,
            "vol_ratio_5d": None,
            "change_pct": None,
        },
    )

    counts = history.group_by("symbol").agg(pl.col("date").n_unique().alias("_history_days"))
    df = df.join(counts, on="symbol", how="left").with_columns(
        pl.col("_history_days").fill_null(0),
        pl.col("name").fill_null(""),
    )

    excluded: dict[str, int] = {}

    def apply_filter(label: str, eligible: pl.Expr) -> None:
        nonlocal df
        before = df.height
        df = df.filter(eligible)
        excluded[label] = before - df.height

    apply_filter("risk_warning", ~pl.col("name").str.contains(r"(?i)(?:\*?ST|退市|退$)"))
    apply_filter("invalid_price", _finite_positive("close") & _finite_positive("raw_close"))
    apply_filter("suspended", _finite_positive("volume"))
    apply_filter("insufficient_history", pl.col("_history_days") >= _MIN_HISTORY_DAYS)

    if df.is_empty():
        return CandidateScreenResult(as_of=as_of, excluded=excluded, message="过滤后无合格候选")

    if df["amount"].drop_nulls().len() >= 20:
        threshold = df.select(pl.col("amount").quantile(0.2, interpolation="nearest")).item()
        if threshold is not None and float(threshold) > 0:
            apply_filter("low_liquidity", pl.col("amount").fill_null(0) >= float(threshold))
    else:
        excluded["low_liquidity"] = 0

    if df.is_empty():
        return CandidateScreenResult(as_of=as_of, excluded=excluded, message="流动性过滤后无候选")

    df = df.with_columns(
        _percentile("momentum_20d").alias("_momentum_score"),
        _percentile("annual_vol_20d", descending=True).alias("_stability_score"),
        _percentile("amount").alias("_liquidity_score"),
        (
            (pl.col("close") > pl.col("ma20")).cast(pl.Float64) * 0.45
            + (pl.col("ma20") > pl.col("ma60")).cast(pl.Float64) * 0.35
            + ((pl.col("close") / pl.col("ma20") - 1).clip(-0.1, 0.1) + 0.1) * 1.0
        ).fill_null(0.0).alias("_trend_score"),
        (
            (pl.col("rsi_14").fill_null(50) - 68).clip(0, 32) / 32
            + (pl.col("vol_ratio_5d").fill_null(1) - 2.5).clip(0, 3) / 3
            + (pl.col("change_pct").fill_null(0).abs() - 0.07).clip(0, 0.13) / 0.13
        ).alias("_overheat_penalty"),
    ).with_columns(
        (
            pl.col("_trend_score") * 0.34
            + pl.col("_momentum_score") * 0.23
            + pl.col("_stability_score") * 0.20
            + pl.col("_liquidity_score") * 0.13
            + (1 - pl.col("_overheat_penalty").clip(0, 1)) * 0.10
        ).alias("research_score")
    )

    output_columns = [
        "symbol", "name", "close", "raw_close", "change_pct", "amount", "volume",
        "ma20", "ma60", "momentum_20d", "annual_vol_20d", "rsi_14", "vol_ratio_5d",
        "research_score", "_history_days",
    ]
    selected = (
        df.sort(["research_score", "symbol"], descending=[True, False])
        .head(min(max(limit, 1), 50))
        .select(output_columns)
    )
    candidates = []
    for row in selected.to_dicts():
        candidates.append({
            key.removeprefix("_"): (round(value, 6) if isinstance(value, float) else value)
            for key, value in row.items()
        })
    return CandidateScreenResult(
        as_of=as_of,
        candidates=candidates,
        eligible_count=df.height,
        excluded=excluded,
    )


def screen_candidates(repo, limit: int = _DEFAULT_LIMIT) -> CandidateScreenResult:
    latest, as_of = repo.get_enriched_latest()
    if latest.is_empty() or as_of is None:
        return CandidateScreenResult(as_of=as_of, message="最新指标缓存尚未就绪")
    history = repo.get_enriched_history(as_of, _MIN_HISTORY_DAYS)
    return screen_dataframe(
        latest,
        history if history is not None else pl.DataFrame(),
        repo.get_instruments(),
        as_of=as_of,
        limit=limit,
    )
