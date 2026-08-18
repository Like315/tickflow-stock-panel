"""龙头板块分析 service — 判断哪些板块是当前市场的「龙头板块」, 并拆解每个板块的历史龙头股。

## 数据来源 (全部复用现有资产, 不引入新数据源)

- 板块成分股映射: `rps_rotation._load_concept_map_df`
  (symbol → 概念/行业, 与涨幅轮动矩阵同源, 按 kind 有 600s 缓存)
- 历史行情: `repo.get_enriched_range` → `_enriched_history_cache`
  (含 `change_pct` 小数制、`amount` 元、`consecutive_limit_ups`、`signal_limit_up`、`name`)

## 龙头板块评分口径 (0~100)

    score = 0.40 x persistence(排名持续性) + 0.30 x capital(资金强度) + 0.30 x leader(龙头股强度)

- **persistence 排名持续性**: 窗口内板块进入当日涨幅榜前 10 的天数占比 (60%)
  + 平均排名的线性得分 (40%, 平均第 1 名=1.0, 第 31 名起=0)
- **capital 资金强度**: 窗口内板块成分股总成交额, `log1p` 归一化 (最大板块=100)
- **leader 龙头股强度**: 区间冠军股的强度
  (领涨天数占比 40% + 累计涨幅 40%, 50% 封顶 + 最高连板 20%, 5 板封顶)

## 历史龙头股拆解

- **daily_leaders 每日龙头**: 每个交易日板块内当日涨幅最大 (同涨幅比成交额) 的个股
- **champion 区间冠军**: 领涨天数最多 → 累计涨幅 → 最高连板, 排序取第一

## 缓存

进程级 TTL 120s, 缓存键含最新交易日 → 数据更新后自动失效 (与 rps_rotation 同模式);
另提供 `invalidate_cache()` 供管道在需要时主动清空。
"""
from __future__ import annotations

import logging
import math
import time
from datetime import date, timedelta

import polars as pl

from app.services.rps_rotation import _latest_enriched_date, _load_concept_map_df

logger = logging.getLogger(__name__)

# 进程级结果缓存 (照搬 rps_rotation 的模式: TTL 120s, 键含最新交易日)
_CACHE_TTL = 120.0
_cache: dict[str, dict] = {}
_cache_ts: dict[str, float] = {}

# 权重: 排名持续性 / 资金强度 / 龙头股强度
_W_PERSIST = 0.40
_W_CAPITAL = 0.30
_W_LEADER = 0.30

# 进入当日涨幅榜前 N 名视为「强势日」; 与 RPS 对话框的 rankColorClass 前 10 语义一致
_TOP_RANK = 10
# 平均排名的得分线性区间下限: span = max(该值, 20% 板块总数)
_RANK_SPAN_MIN = 30
# 龙头股强度封顶参照: 累计涨幅 50% 满分, 最高连板 5 板满分
_CUM_CAP = 0.50
_BOARD_CAP = 5


def _trade_plan(history: pl.DataFrame, symbol: str) -> dict | None:
    """Build weekly-trend and daily-risk signals from data visible at the last row."""
    stock = history.filter(pl.col("symbol") == symbol).sort("date")
    if stock.is_empty() or "close" not in stock.columns:
        return None
    stock = stock.filter(pl.col("close").is_not_null() & pl.col("close").is_finite())
    if len(stock) < 120:
        return None

    stock = stock.with_columns([
        pl.col("close").rolling_mean(5).alias("ma5"),
        pl.col("close").rolling_max(20).alias("high_close_20d"),
        pl.col("date").dt.strftime("%G-%V").alias("week"),
    ])
    weekly = (
        stock.group_by("week", maintain_order=True)
        .agg(pl.col("date").last(), pl.col("close").last())
        .sort("date")
        .with_columns([
            pl.col("close").rolling_mean(4).alias("wma4"),
            pl.col("close").rolling_mean(10).alias("wma10"),
        ])
    )
    if len(weekly) < 10:
        return None
    monthly = (
        stock.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month"))
        .group_by("month", maintain_order=True)
        .agg(pl.col("date").last(), pl.col("close").last())
        .sort("date")
        .with_columns([
            pl.col("close").rolling_mean(3).alias("mma3"),
            pl.col("close").rolling_mean(6).alias("mma6"),
        ])
    )
    if len(monthly) < 6:
        return None

    previous, current = stock.tail(2).to_dicts()
    week = weekly.tail(1).to_dicts()[0]
    month = monthly.tail(1).to_dicts()[0]
    close = float(current["close"])
    ma5 = float(current["ma5"])
    previous_ma5 = float(previous["ma5"])
    high_close = float(current["high_close_20d"])
    wclose = float(week["close"])
    wma4 = float(week["wma4"])
    wma10 = float(week["wma10"])
    weekly_trend = wclose > wma4 > wma10
    mclose = float(month["close"])
    mma3 = float(month["mma3"])
    mma6 = float(month["mma6"])
    monthly_trend = mclose > mma3 > mma6
    above_ma5 = close >= ma5
    exit_ma5 = close < ma5 and float(previous["close"]) >= previous_ma5
    drawdown_stop = high_close * 0.90
    within_drawdown = close >= drawdown_stop
    return {
        "as_of": str(current["date"]),
        "close": round(close, 3),
        "ma5": round(ma5, 3),
        "weekly_close": round(wclose, 3),
        "weekly_ma4": round(wma4, 3),
        "weekly_ma10": round(wma10, 3),
        "weekly_trend": weekly_trend,
        "monthly_close": round(mclose, 3),
        "monthly_ma3": round(mma3, 3),
        "monthly_ma6": round(mma6, 3),
        "monthly_trend": monthly_trend,
        "above_ma5": above_ma5,
        "drawdown_stop_price": round(drawdown_stop, 3),
        "drawdown_pct": round(close / high_close - 1.0, 4),
        "exit_ma5": exit_ma5,
        "eligible": monthly_trend and weekly_trend and above_ma5 and within_drawdown and not exit_ma5,
    }


def invalidate_cache() -> None:
    """清空龙头板块结果缓存 (数据管道完成后调用, 避免返回旧数据)。"""
    _cache.clear()
    _cache_ts.clear()


def _empty_result(latest: date | None, kind: str, days: int) -> dict:
    return {
        "as_of": str(latest) if latest else None,
        "kind": kind,
        "days": days,
        "sector_count": 0,
        "sectors": [],
    }


def _persistence_score(top10_days: int, days_with_data: int, avg_rank: float, sector_count: int) -> float:
    """排名持续性得分 (0~100)。

    强势日占比 (前 10 天数 / 有数据天数) 权重 0.6; 平均排名线性分权重 0.4。
    平均排名按 _RANK_SPAN 线性衰减: 第 1 名=1.0, 第 (span+1) 名起=0。
    span 随板块总数缩放 (max(30, 20% 板块数)), 保证概念(数百个)与行业(几十个)
    两个维度下的评分尺度一致。
    """
    if not days_with_data:
        return 0.0
    top10_ratio = top10_days / days_with_data
    rank_span = max(_RANK_SPAN_MIN, round(sector_count * 0.2))
    rank_part = max(0.0, 1.0 - (avg_rank - 1.0) / rank_span)
    return round(100 * (0.6 * top10_ratio + 0.4 * rank_part), 1)


def _capital_score(total_amount: float, max_amount: float) -> float:
    """资金强度得分 (0~100): log1p 归一化到最大板块=100。"""
    if max_amount <= 0:
        return 0.0
    return round(100 * math.log1p(max(0.0, total_amount)) / math.log1p(max(0.0, max_amount)), 1)


def _leader_score(champion: dict | None, window_days: int) -> float:
    """龙头股强度得分 (0~100)。

    领涨天数占比 40% + 累计涨幅 (50% 封顶) 40% + 最高连板 (5 板封顶) 20%。
    """
    if not champion:
        return 0.0
    lead_part = min(1.0, (champion.get("lead_days") or 0) / max(1, window_days))
    cum_part = min(1.0, max(0.0, champion.get("cum_pct") or 0.0) / _CUM_CAP)
    board_part = min(1.0, (champion.get("max_boards") or 0) / _BOARD_CAP)
    return round(100 * (0.4 * lead_part + 0.4 * cum_part + 0.2 * board_part), 1)


def _build_sector_stats(wdf: pl.DataFrame, kind: str) -> pl.DataFrame:
    """板块窗口统计: 每日平均涨幅 + 当日涨幅榜排名 + 窗口聚合。

    Returns columns: kind, avg_rank, top10_days, days_with_data, total_amount,
    avg_pct, member_count。无 amount 列 (降级缓存) 时 total_amount=0。
    """
    # 缺失 amount 列时补常量 0, 后续聚合/评分统一走同一路径
    if "amount" not in wdf.columns:
        wdf = wdf.with_columns(pl.lit(0.0).alias("amount"))
    # 每日板块平均涨幅 (与 rps_rotation 的简单平均口径一致) + 成交额
    sday = (
        wdf.group_by(["date", kind])
        .agg(
            avg_pct=pl.col("change_pct").mean(),
            amount=pl.col("amount").sum(),
            member_count=pl.col("symbol").n_unique(),
        )
        .filter(pl.col("avg_pct").is_not_null() & pl.col("avg_pct").is_not_nan())
    )
    # 当日涨幅榜排名 (avg_pct 降序, 每列各自排)
    sday = sday.with_columns(
        pl.col("avg_pct").rank(descending=True).over("date").alias("day_rank")
    )
    stats = sday.group_by(kind).agg(
        avg_rank=pl.col("day_rank").mean(),
        top10_days=(pl.col("day_rank") <= _TOP_RANK).sum(),
        days_with_data=pl.len(),
        total_amount=pl.col("amount").sum(),
        avg_pct=pl.col("avg_pct").mean(),
        member_count=pl.col("member_count").max(),
    )
    return stats


def _build_daily_leaders(wdf: pl.DataFrame, kind: str) -> pl.DataFrame:
    """每个交易日板块内当日龙头 (涨幅最大, 同涨幅比成交额)。

    排序后 group_by 取每组第一行 (maintain_order 保持已排序顺序)。
    缺失 amount/name/signal_limit_up 列 (降级缓存) 时退化为可用的列。
    """
    sort_cols = ["date", kind, "change_pct"]
    descending = [False, False, True]
    if "amount" in wdf.columns:
        sort_cols.append("amount")
        descending.append(True)
    sorted_df = wdf.sort(sort_cols, descending=descending, nulls_last=True)
    keep = ["date", kind, "symbol", "change_pct"]
    if "amount" in wdf.columns:
        keep.append("amount")
    if "name" in wdf.columns:
        keep.append("name")
    if "signal_limit_up" in wdf.columns:
        keep.append("signal_limit_up")
    return sorted_df.group_by(["date", kind], maintain_order=True).first().select(keep)


def _build_champions(wdf: pl.DataFrame, daily_leaders: pl.DataFrame, kind: str) -> dict[str, dict]:
    """每个板块的区间冠军: 领涨天数最多 → 累计涨幅 → 最高连板。

    领涨天数 = 成为板块每日龙头的天数; 累计涨幅 = 有行情日的 (1+pct) 连乘 - 1;
    最高连板 = 窗口内 consecutive_limit_ups 最大值 (缺失列退化为 0)。
    """
    # 领涨天数: 每日龙头按 (kind, symbol) 计数
    lead = (
        daily_leaders.group_by([kind, "symbol"])
        .agg(pl.len().alias("lead_days"))
    )

    # 累计涨幅: 只对有行情的日子连乘, 避免停牌日 null 污染
    cum = (
        wdf.filter(pl.col("change_pct").is_not_null())
        .group_by([kind, "symbol"])
        .agg(
            cum_pct=((1 + pl.col("change_pct")).product() - 1).alias("cum_pct"),
            avg_pct=pl.col("change_pct").mean(),
        )
    )

    base = (
        wdf.group_by([kind, "symbol"])
        .agg(
            name=(
                pl.col("name").first()
                if "name" in wdf.columns
                else pl.col("symbol").first()
            ),
            max_boards=(
                pl.col("consecutive_limit_ups").fill_null(0).max()
                if "consecutive_limit_ups" in wdf.columns
                else pl.lit(0).alias("max_boards")
            ),
        )
    )

    merged = (
        base.join(lead, on=[kind, "symbol"], how="left")
        .join(cum, on=[kind, "symbol"], how="left")
        .with_columns(pl.col("lead_days").fill_null(0))
        .with_columns(pl.col("cum_pct").fill_null(0.0))
        .with_columns(pl.col("avg_pct").fill_null(0.0))
    )

    # 冠军排序: 领涨天数 → 累计涨幅 → 最高连板, 均降序; 每组取第一
    merged = merged.sort(
        ["lead_days", "cum_pct", "max_boards"], descending=[True, True, True],
    )
    top = merged.group_by(kind, maintain_order=True).first()

    champions: dict[str, dict] = {}
    for row in top.iter_rows(named=True):
        champions[row[kind]] = {
            "symbol": row["symbol"],
            "name": row["name"] or row["symbol"],
            "lead_days": int(row["lead_days"]),
            "cum_pct": round(float(row["cum_pct"]), 4),
            "max_boards": int(row["max_boards"]),
            "avg_pct": round(float(row["avg_pct"]), 4),
        }
    return champions


def build_leading_sectors(
    repo,
    days: int = 12,
    kind: str = "concept",
    level: int | None = None,
    top: int = 30,
) -> dict:
    """构建龙头板块排行 + 历史龙头股拆解。

    Args:
        repo: KlineRepository (含 _enriched_history_cache 内存历史)。
        days: 分析最近 N 个交易日 (7-30), 默认 12。
        kind: "concept"(概念) 或 "industry"(行业), 决定维度映射来源。
        level: 行业层级 (仅 kind=industry 有效, 1/2/3 级)。None 用原始全路径名。
        top: 返回前 N 个龙头板块 (仅这些板块带每日龙头/区间冠军明细), 默认 30。

    Returns:
        {
          "as_of": "2026-07-01",           # 最新交易日
          "kind": "concept",
          "days": 12,                       # 实际窗口交易日数 (≤ 请求 days)
          "sector_count": 387,              # 去重维度成员总数
          "sectors": [
            {
              "name": "人工智能",
              "count": 120,                 # 板块成分股数
              "score": 87.5,                # 龙头分 0~100
              "parts": {"persistence": .., "capital": .., "leader": ..},
              "avg_pct": 0.035,             # 窗口平均日涨幅 (小数)
              "total_amount": 1.2e10,       # 窗口总成交额 (元)
              "avg_rank": 3.2,              # 窗口平均涨幅榜排名
              "top10_days": 8,              # 进入前 10 的天数
              "champion": {symbol, name, lead_days, cum_pct, max_boards, avg_pct} | null,
              "daily_leaders": [            # 每日龙头, 最新在前
                {"date", "symbol", "name", "change_pct", "rank_in_sector", "is_limit_up"}
              ],
            }
          ]
        }
        无数据时 sectors 为空列表。
    """
    days = max(7, min(30, days))
    top = max(1, min(100, top))

    latest = _latest_enriched_date(repo)
    if latest is None:
        return _empty_result(latest, kind, days)

    # 窗口 (days) 影响评分与窗口统计, 必须进缓存键; top 只是响应截断, 不进键。
    cache_key = f"{kind}|{level}|{days}|{latest.isoformat()}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and (now - _cache_ts.get(cache_key, 0)) < _CACHE_TTL:
        return _slice_cached(cached, top)

    # 1. 维度映射 (symbol → 概念/行业), 已按 kind 缓存
    map_df, member_count = _load_concept_map_df(repo, kind)
    if map_df.is_empty():
        logger.info("leading_sector: no %s data (ext dimension not fetched yet)", kind)
        return _empty_result(latest, kind, days)

    # 2. 历史行情 (命中内存缓存)
    start = latest - timedelta(days=max(days * 2 + 10, 260))
    want_cols = [
        "symbol", "date", "change_pct", "amount",
        "consecutive_limit_ups", "signal_limit_up", "name", "close",
    ]
    df = repo.get_enriched_range(start, latest, columns=want_cols)
    if df is None or df.is_empty():
        return _empty_result(latest, kind, days)

    # 3. symbol 大写匹配并 join 维度成员
    df = df.with_columns(pl.col("symbol").str.to_uppercase().alias("_sym_up"))
    joined = df.join(map_df, on="_sym_up", how="inner").drop("_sym_up")
    if joined.is_empty():
        return _empty_result(latest, kind, days)

    # 4. 行业层级聚合 (与 rps_rotation 的 level 口径一致)
    if kind == "industry" and level is not None:
        parts = pl.col(kind).str.split("-")
        idx = pl.min_horizontal(pl.lit(level - 1), pl.col(kind).str.count_matches("-"))
        joined = joined.with_columns(parts.list.get(idx).alias(kind))

    # 5. 裁剪到最近 days 个交易日 (按实际交易日, 非自然日)
    trading_dates = joined["date"].unique().sort(descending=True)
    window_dates = trading_dates[:days].to_list()
    if not window_dates:
        return _empty_result(latest, kind, days)
    wdf = joined.filter(pl.col("date").is_in(window_dates))

    # 6. 板块窗口统计 + 评分
    stats = _build_sector_stats(wdf, kind)
    if stats.is_empty():
        return _empty_result(latest, kind, days)

    daily_leaders = _build_daily_leaders(wdf, kind)
    champions = _build_champions(wdf, daily_leaders, kind)
    trade_plans = {
        symbol: _trade_plan(df, symbol)
        for symbol in {item["symbol"] for item in champions.values()}
    }

    # 每日龙头: 按板块分组 (仅对有龙头股的板块)
    leader_by_sector: dict[str, list[dict]] = {}
    for row in daily_leaders.iter_rows(named=True):
        sec = row[kind]
        leader_by_sector.setdefault(sec, []).append({
            "date": str(row["date"]),
            "symbol": row["symbol"],
            "name": row.get("name") or row["symbol"],
            "change_pct": round(float(row["change_pct"]), 4) if row["change_pct"] is not None else None,
            "rank_in_sector": 1,
            "is_limit_up": bool(row.get("signal_limit_up")),
        })

    max_amount = float(stats["total_amount"].max() or 0.0)
    window_len = len(window_dates)
    # 板块总数 = 实际参与评分的板块数 (行业 level 聚合后成员数会合并)
    total_sectors = len(stats)

    sectors: list[dict] = []
    for row in stats.iter_rows(named=True):
        name = row[kind]
        champion = champions.get(name)
        if champion is not None:
            champion = dict(champion)
            champion["trade_plan"] = trade_plans.get(champion["symbol"])
        parts = {
            "persistence": _persistence_score(int(row["top10_days"]), int(row["days_with_data"]), float(row["avg_rank"]), total_sectors),
            "capital": _capital_score(float(row["total_amount"]), max_amount),
            "leader": _leader_score(champion, window_len),
        }
        score = round(
            _W_PERSIST * parts["persistence"]
            + _W_CAPITAL * parts["capital"]
            + _W_LEADER * parts["leader"],
            1,
        )
        daily = leader_by_sector.get(name, [])
        daily.sort(key=lambda d: d["date"], reverse=True)  # 最新在前
        sectors.append({
            "name": name,
            "count": int(row["member_count"]),
            "score": score,
            "parts": parts,
            "avg_pct": round(float(row["avg_pct"]), 4),
            "total_amount": round(float(row["total_amount"]), 2),
            "avg_rank": round(float(row["avg_rank"]), 1),
            "top10_days": int(row["top10_days"]),
            "champion": champion,
            "daily_leaders": daily,
        })

    sectors.sort(key=lambda s: s["score"], reverse=True)

    full = {
        "as_of": str(latest),
        "kind": kind,
        "days": window_len,
        "sector_count": member_count,
        "sectors": sectors,
    }
    _cache[cache_key] = full
    _cache_ts[cache_key] = now
    return _slice_cached(full, top)


def _slice_cached(full: dict, top: int) -> dict:
    """从全量缓存截断响应: 只返回前 top 个板块 (明细随板块截断)。"""
    out = dict(full)
    out["sectors"] = full["sectors"][:top]
    return out
