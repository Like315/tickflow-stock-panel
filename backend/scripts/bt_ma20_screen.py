"""
MA20 趋势逻辑的两种用法对比：指数择时 vs 个股筛选 vs 组合
防作弊口径与 bt_tech_compare.py 一致：
  - 静态科技池（warmup 期流动性 Top400，正式期不更换）
  - T-1 收盘信号 → T 日开盘成交；全量再平衡；全成本（佣金0.02%+印花税0.05%+滑点0.05%）
  - 涨停开盘/停牌不可买入
正式区间：2025-12-01 ~ 2026-08-13
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from app.config import settings

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DATA = settings.data_dir
OUTPUT_DIR = Path(os.environ.get("TICKFLOW_RESEARCH_OUTPUT_DIR", DATA.parent / "output")).resolve()
START = date(2025, 12, 1)
END = date(2026, 8, 13)
WARMUP_END = date(2025, 11, 28)
COMM, TAX, SLIP = 0.0002, 0.0005, 0.0005
COST_BUY, COST_SELL = COMM + SLIP, COMM + TAX + SLIP

TECH_KEYWORDS = [
    "人工智能",
    "AI",
    "AIGC",
    "大模型",
    "ChatGPT",
    "DeepSeek",
    "芯片",
    "半导体",
    "集成电路",
    "存储芯片",
    "先进封装",
    "光刻机",
    "算力",
    "服务器",
    "液冷",
    "CPO",
    "光模块",
    "数据中心",
    "IDC",
    "机器人",
    "人形机器人",
    "机器视觉",
    "消费电子",
    "华为概念",
    "苹果概念",
    "折叠屏",
    "智能穿戴",
    "国产软件",
    "信创",
    "操作系统",
    "数据库",
    "数据要素",
    "数字经济",
    "云计算",
    "大数据",
    "5G",
    "6G",
    "卫星互联网",
    "卫星导航",
    "PCB",
    "GPU",
    "智能驾驶",
    "无人驾驶",
    "车联网",
    "低空经济",
]

print("loading...")
kline = (
    pl.scan_parquet(f"{DATA}/kline_daily_enriched/**/*.parquet")
    .select(["symbol", "date", "open", "close", "amount", "raw_close"])
    .collect()
    .sort(["date", "symbol"])
)
kline = kline.filter(pl.col("date") <= END)
gn = pl.read_parquet(f"{DATA}/ext_data/ext_gn_ths/part.parquet")
tech_syms = set(
    r["symbol"]
    for r in gn.iter_rows(named=True)
    if any(k in str(r.get("所属概念") or "") for k in TECH_KEYWORDS)
)
hy = pl.read_parquet(f"{DATA}/ext_data/ext_hy_ths/part.parquet")
hy_map = {r["symbol"]: str(r["所属同花顺行业"]).split("-")[0] for r in hy.iter_rows(named=True)}
inst = pl.read_parquet(f"{DATA}/instruments/instruments.parquet")
name_map = {r["symbol"]: r.get("name", "") for r in inst.iter_rows(named=True)}

idx = (
    pl.scan_parquet(f"{DATA}/kline_index_daily/**/*.parquet")
    .filter(pl.col("symbol") == "000688.SH")
    .select(["date", "close"])
    .collect()
    .sort("date")
)
kc_map = {str(r["date"])[:10]: r["close"] for r in idx.iter_rows(named=True)}

all_dates = sorted(kline["date"].unique().to_list())
formal_dates = [d for d in all_dates if START <= d <= END]
n_f = len(formal_dates)

# 静态池
k_warmup = kline.filter(pl.col("date") <= WARMUP_END).filter(pl.col("symbol").is_in(tech_syms))
liquidity = (
    k_warmup.group_by("symbol")
    .agg(pl.col("amount").mean().alias("avg_amount"), pl.col("close").count().alias("n_days"))
    .filter(pl.col("n_days") >= 40)
    .sort("avg_amount", descending=True)
)
pool = liquidity.head(400)["symbol"].to_list()
n_a = len(pool)
print(f"科技池 Top400, 正式交易日 {n_f}")

kline_pool = kline.filter(pl.col("symbol").is_in(pool)).sort(["date", "symbol"])
pc = kline_pool.pivot(index="date", columns="symbol", values="close").sort("date")
po = kline_pool.pivot(index="date", columns="symbol", values="open").sort("date")
pr = kline_pool.pivot(index="date", columns="symbol", values="raw_close").sort("date")
dates_arr = pc["date"].to_list()
date2row = {d: i for i, d in enumerate(dates_arr)}
close_m = pc.select(pool).to_numpy().astype(np.float64)
open_m = po.select(pool).to_numpy().astype(np.float64)
raw_m = pr.select(pool).to_numpy().astype(np.float64)
prev_raw = np.zeros_like(raw_m)
prev_raw[1:] = raw_m[:-1]
open_chg = raw_m / np.maximum(prev_raw, 1e-9) - 1.0

# 个股均线
ma20 = np.full_like(close_m, np.nan)
ma60 = np.full_like(close_m, np.nan)
for t in range(20, len(dates_arr)):
    ma20[t] = np.where(
        np.isfinite(close_m[t - 19 : t + 1]).all(axis=0),
        close_m[t - 19 : t + 1].mean(axis=0),
        np.nan,
    )
for t in range(60, len(dates_arr)):
    ma60[t] = np.where(
        np.isfinite(close_m[t - 59 : t + 1]).all(axis=0),
        close_m[t - 59 : t + 1].mean(axis=0),
        np.nan,
    )

# 科创50 + 其 MA20
kc_arr = np.array([kc_map.get(str(x)[:10], np.nan) for x in dates_arr])
ma20_kc = np.full(len(kc_arr), np.nan)
for t in range(20, len(kc_arr)):
    seg = kc_arr[t - 19 : t + 1]
    if np.isfinite(seg).all():
        ma20_kc[t] = seg.mean()

i0 = date2row[START]
i1 = date2row[END] + 1
r_cc = np.zeros_like(close_m)
r_cc[1:] = close_m[1:] / close_m[:-1] - 1.0
open_chg_f = open_chg[i0:i1]
r_cc_f = r_cc[i0:i1]
dates_f = [str(x)[:10] for x in dates_arr[i0:i1]]


class Sim:
    def __init__(self, cash=1_000_000.0):
        self.cash, self.mv, self.hold, self.equity, self.trades = (
            cash,
            np.zeros(n_a),
            np.zeros(n_a, dtype=bool),
            [],
            0,
        )

    def rebalance(self, t, target_idx):
        if self.hold.any():
            self.cash += self.mv[self.hold].sum() * (1 - COST_SELL)
            self.mv[self.hold] = 0.0
            self.hold[:] = False
            self.trades += 1
        blocked = ~np.isfinite(open_chg_f[t]) | (open_chg_f[t] >= 0.095)
        can = target_idx & (~blocked)
        nb = int(can.sum())
        if nb > 0 and self.cash > 1e4:
            budget = self.cash / nb
            self.mv[can] += budget * (1 - COST_BUY)
            self.cash -= budget * nb
            self.hold[can] = True
            self.trades += nb
        dr = np.where(
            np.isfinite(np.where(self.hold, r_cc_f[t], 0.0)),
            np.where(self.hold, r_cc_f[t], 0.0),
            0.0,
        )
        self.mv *= 1 + dr
        self.equity.append(self.cash + self.mv.sum())

    def hold_day(self, t):
        dr = np.where(
            np.isfinite(np.where(self.hold, r_cc_f[t], 0.0)),
            np.where(self.hold, r_cc_f[t], 0.0),
            0.0,
        )
        self.mv *= 1 + dr
        self.equity.append(self.cash + self.mv.sum())

    def stats(self):
        eq = np.array(self.equity)
        rets = eq[1:] / eq[:-1] - 1
        total = eq[-1] / eq[0] - 1
        ann = (1 + total) ** (242 / max(len(eq) - 1, 1)) - 1
        peak = np.maximum.accumulate(eq)
        sharpe = rets.mean() / rets.std() * np.sqrt(242) if rets.std() > 0 else 0
        return {
            "total": total,
            "annual": ann,
            "max_dd": (eq / peak - 1).min(),
            "sharpe": sharpe,
            "trades": self.trades,
            "equity": eq.tolist(),
        }


month_boundaries = [k for k in range(1, len(dates_f)) if dates_f[k][:7] != dates_f[k - 1][:7]]
ALL = np.ones(n_a, dtype=bool)
results = {}

# 对照1：科技等权持有（月再平衡）
sim = Sim()
for t in range(len(dates_f)):
    if t == 0 or t in month_boundaries:
        sim.rebalance(t, ALL)
    else:
        sim.hold_day(t)
results["科技等权持有"] = sim.stats()

# 对照2：科创50 + MA20 指数择时（满仓/空仓，标的=科创50 用指数近似）
sim = Sim()
state = 1
for t in range(len(dates_f)):
    tt = i0 + t
    target = 1
    if tt - 1 >= 0 and np.isfinite(ma20_kc[tt - 1]) and np.isfinite(kc_arr[tt - 1]):
        target = 1 if kc_arr[tt - 1] > ma20_kc[tt - 1] else 0
    if t == 0:
        sim.rebalance(t, ALL)
    elif target != state:
        if target == 1:
            sim.rebalance(t, ALL)
        else:
            if sim.hold.any():
                sim.cash += sim.mv[sim.hold].sum() * (1 - COST_SELL)
                sim.mv[sim.hold] = 0.0
                sim.hold[:] = False
                sim.trades += 1
            sim.equity.append(sim.cash)
        state = target
    else:
        sim.hold_day(t)
results["指数择时:科创50+MA20(满仓等权池近似)"] = sim.stats()

# 策略1：个股 MA20 单均线筛选（月频：close>MA20 且 MA20 上行）
sim = Sim()
for t in range(len(dates_f)):
    if t == 0 or t in month_boundaries:
        sig = i0 + t - 1
        up5 = ma20[sig] > ma20[sig - 5] if sig - 5 >= 0 else np.full(n_a, True)
        cond = np.isfinite(ma20[sig]) & np.isfinite(close_m[sig]) & (close_m[sig] > ma20[sig]) & up5
        sim.rebalance(t, cond)
    else:
        sim.hold_day(t)
results["个股筛选:MA20单均线(月频)"] = sim.stats()

# 策略2：个股 MA20>MA60 双均线筛选（月频）
sim = Sim()
for t in range(len(dates_f)):
    if t == 0 or t in month_boundaries:
        sig = i0 + t - 1
        cond = (
            np.isfinite(ma20[sig])
            & np.isfinite(ma60[sig])
            & (close_m[sig] > ma20[sig])
            & (ma20[sig] > ma60[sig])
        )
        sim.rebalance(t, cond)
    else:
        sim.hold_day(t)
results["个股筛选:MA20>MA60双均线(月频)"] = sim.stats()

# 策略3：组合 = 指数 MA20 择时开关 + 个股双均线筛选
sim = Sim()
state = 1
for t in range(len(dates_f)):
    tt = i0 + t
    target = 1
    if tt - 1 >= 0 and np.isfinite(ma20_kc[tt - 1]) and np.isfinite(kc_arr[tt - 1]):
        target = 1 if kc_arr[tt - 1] > ma20_kc[tt - 1] else 0
    sig = i0 + t - 1
    cond = (
        np.isfinite(ma20[sig])
        & np.isfinite(ma60[sig])
        & (close_m[sig] > ma20[sig])
        & (ma20[sig] > ma60[sig])
    )
    if t == 0 or t in month_boundaries:
        if target == 1:
            sim.rebalance(t, cond)
        else:
            if sim.hold.any():
                sim.cash += sim.mv[sim.hold].sum() * (1 - COST_SELL)
                sim.mv[sim.hold] = 0.0
                sim.hold[:] = False
                sim.trades += 1
            sim.equity.append(sim.cash)
        state = target
    else:
        if target != state:
            if target == 1:
                sim.rebalance(t, cond)
            else:
                if sim.hold.any():
                    sim.cash += sim.mv[sim.hold].sum() * (1 - COST_SELL)
                    sim.mv[sim.hold] = 0.0
                    sim.hold[:] = False
                    sim.trades += 1
                sim.equity.append(sim.cash)
            state = target
        else:
            sim.hold_day(t)
results["组合:指数择时+个股双均线"] = sim.stats()

print()
print("=" * 100)
print(f"MA20 逻辑两种用法对比（{START} ~ {END}, {n_f} 交易日, 全成本防作弊）")
print("=" * 100)
for name, r in results.items():
    print(
        f"{name:34s} 总收益{r['total'] * 100:8.2f}% 年化{r['annual'] * 100:8.2f}% 回撤{r['max_dd'] * 100:8.2f}% 夏普{r['sharpe']:5.2f} 换手{r['trades']:5d}"
    )

# ---- 当前时点候选名单（2026-08-13 收盘，演示该规则当下选出什么）----
t_last = len(dates_arr) - 1
sig = t_last
cond_now = (
    np.isfinite(ma20[sig])
    & np.isfinite(ma60[sig])
    & (close_m[sig] > ma20[sig])
    & (ma20[sig] > ma60[sig])
    & (ma20[sig] > ma20[sig - 5])
)
cands = [i for i in range(n_a) if cond_now[i]]
# 排序：按成交额
amt_last = kline_pool.filter(pl.col("date") == dates_arr[-1])
amt_map = {r["symbol"]: r["amount"] for r in amt_last.iter_rows(named=True)}
cands.sort(key=lambda i: amt_map.get(pool[i], 0), reverse=True)
print()
print(f"当前(2026-08-13) MA20>MA60 且 MA20上行 的科技股候选: {len(cands)} 只, 前25:")
out_list = []
for i in cands[:25]:
    sym = pool[i]
    out_list.append(
        {
            "symbol": sym,
            "name": str(name_map.get(sym, "")),
            "industry": hy_map.get(sym, "?"),
            "close": round(float(close_m[sig, i]), 2),
            "ma20": round(float(ma20[sig, i]), 2),
            "ma60": round(float(ma60[sig, i]), 2),
            "pct_vs_ma20": round(float(close_m[sig, i] / ma20[sig, i] - 1) * 100, 2),
        }
    )
    print(
        f"  {sym} {name_map.get(sym, ''):8s} {hy_map.get(sym, '?'):10s} 收{close_m[sig, i]:.2f} MA20:{ma20[sig, i]:.2f} MA60:{ma60[sig, i]:.2f} 乖离{close_m[sig, i] / ma20[sig, i] - 1:+.2%}"
    )

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
with (OUTPUT_DIR / "ma20_screen.json").open("w", encoding="utf-8") as output_file:
    json.dump(
        {
            "results": {
                key: {name: value for name, value in result.items() if name != "equity"}
                for key, result in results.items()
            },
            "current_candidates": out_list,
        },
        output_file,
        ensure_ascii=False,
        default=float,
    )
print("saved output/ma20_screen.json")
