# -*- coding: utf-8 -*-
"""
科技股非打板策略横向对比回测（防作弊版 v2）
防作弊口径：
  1) 静态股票池：warmup 期(2025-08-13~2025-11-28) 按日均成交额 top N 确定，正式期不再更换
  2) 信号全部使用 T-1 日收盘可得数据，成交在 T 日开盘价（无当日信号当日成交）
  3) 全成本：佣金 0.02% 双边 + 卖出印花税 0.05% + 滑点 0.05%
  4) 涨停开盘不可买入；跌停开盘卖出顺延（简化）
正式区间：2025-12-01 ~ 2026-08-13（warmup 足够 MA60）
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import numpy as np
import polars as pl
from datetime import date

DATA = r"D:\MyTickFlowStockPanel\data"
START = date(2025, 12, 1)
END = date(2026, 8, 13)
WARMUP_END = date(2025, 11, 28)

COMM = 0.0002
TAX = 0.0005
SLIP = 0.0005
COST_BUY = COMM + SLIP
COST_SELL = COMM + TAX + SLIP

TECH_KEYWORDS = [
    "人工智能", "AI", "AIGC", "大模型", "ChatGPT", "DeepSeek", "芯片", "半导体",
    "集成电路", "存储芯片", "先进封装", "光刻机", "算力", "服务器", "液冷", "CPO",
    "光模块", "数据中心", "IDC", "机器人", "人形机器人", "机器视觉", "消费电子",
    "华为概念", "苹果概念", "折叠屏", "智能穿戴", "国产软件", "信创", "操作系统",
    "数据库", "数据要素", "数字经济", "云计算", "大数据", "5G", "6G", "卫星互联网",
    "卫星导航", "PCB", "GPU", "智能驾驶", "无人驾驶", "车联网", "低空经济",
]

print("loading data...")
kline = (
    pl.scan_parquet(f"{DATA}/kline_daily_enriched/**/*.parquet")
    .select(["symbol", "date", "open", "close", "amount", "raw_close"])
    .collect()
    .sort(["date", "symbol"])
)
kline = kline.filter(pl.col("date") <= END)

gn = pl.read_parquet(f"{DATA}/ext_data/ext_gn_ths/part.parquet")
tech_syms = set()
for r in gn.iter_rows(named=True):
    if any(k in str(r.get("所属概念") or "") for k in TECH_KEYWORDS):
        tech_syms.add(r["symbol"])

idx = (
    pl.scan_parquet(f"{DATA}/kline_index_daily/**/*.parquet")
    .filter(pl.col("symbol").is_in(["000001.SH", "000688.SH"]))
    .select(["symbol", "date", "close"])
    .collect()
    .sort(["date"])
)

all_dates = sorted(kline["date"].unique().to_list())
formal_dates = [d for d in all_dates if START <= d <= END]
n_f = len(formal_dates)
print(f"交易日: 总{len(all_dates)} 正式{n_f}")

# ---- 静态科技池（warmup 流动性 top N）----
k_warmup = kline.filter(pl.col("date") <= WARMUP_END).filter(pl.col("symbol").is_in(tech_syms))
liquidity = (
    k_warmup.group_by("symbol")
    .agg(pl.col("amount").mean().alias("avg_amount"), pl.col("close").count().alias("n_days"))
    .filter(pl.col("n_days") >= 40)
    .sort("avg_amount", descending=True)
)
TOP_N = 400
pool = liquidity.head(TOP_N)["symbol"].to_list()
print(f"科技池: {len(tech_syms)} -> 流动性Top{TOP_N}: {len(pool)}")

sym_idx = {s: i for i, s in enumerate(pool)}
n_a = len(pool)
kline_pool = kline.filter(pl.col("symbol").is_in(pool)).sort(["date", "symbol"])
pivot_close = kline_pool.pivot(index="date", columns="symbol", values="close").sort("date")
pivot_open = kline_pool.pivot(index="date", columns="symbol", values="open").sort("date")
pivot_raw = kline_pool.pivot(index="date", columns="symbol", values="raw_close").sort("date")

dates_arr = pivot_close["date"].to_list()
date2row = {d: i for i, d in enumerate(dates_arr)}
close_m = pivot_close.select(pool).to_numpy().astype(np.float64)
open_m = pivot_open.select(pool).to_numpy().astype(np.float64)
raw_m = pivot_raw.select(pool).to_numpy().astype(np.float64)

# 缺失数据(停牌)处理: close 为 nan 时收益记 0
def _finite_ret(cur, prev):
    out = np.full(cur.shape, 0.0)
    ok = np.isfinite(cur) & np.isfinite(prev) & (prev > 0)
    out = np.where(ok, cur / np.maximum(prev, 1e-9) - 1.0, 0.0)
    return out

r_cc = _finite_ret(close_m[1:], close_m[:-1])
r_cc = np.vstack([np.zeros((1, n_a)), r_cc])
prev_raw = np.roll(raw_m, 1, axis=0)
prev_raw[0] = np.nan
open_chg = np.where(np.isfinite(raw_m) & np.isfinite(prev_raw) & (prev_raw > 0),
                    raw_m / np.maximum(prev_raw, 1e-9) - 1.0, np.nan)

def index_close(sym):
    d = idx.filter(pl.col("symbol") == sym)
    m = {str(r["date"])[:10]: r["close"] for r in d.iter_rows(named=True)}
    return np.array([m.get(str(x)[:10], np.nan) for x in dates_arr])

sh_close = index_close("000001.SH")
kc_close = index_close("000688.SH")

# ---- 正式区间切片 ----
i0 = date2row[START]
i1 = date2row[END] + 1
r_cc_f = r_cc[i0:i1]
open_chg_f = open_chg[i0:i1]
close_f = close_m[i0:i1]
dates_f = [str(x)[:10] for x in dates_arr[i0:i1]]

# ---- 均线（全序列计算）----
ma20 = np.full_like(close_m, np.nan)
ma60 = np.full_like(close_m, np.nan)
for t in range(20, len(dates_arr)):
    ma20[t] = np.where(np.isfinite(close_m[t - 19 : t + 1]).all(axis=0), close_m[t - 19 : t + 1].mean(axis=0), np.nan)
for t in range(60, len(dates_arr)):
    ma60[t] = np.where(np.isfinite(close_m[t - 59 : t + 1]).all(axis=0), close_m[t - 59 : t + 1].mean(axis=0), np.nan)

ret20 = np.full_like(close_m, np.nan)
for t in range(20, len(dates_arr)):
    prev = close_m[t - 20]
    valid = prev > 0
    ret20[t] = np.where(valid, close_m[t] / prev - 1.0, np.nan)

# 上证 MA20（全序列）
ma20_sh = np.full(len(sh_close), np.nan)
for t in range(20, len(sh_close)):
    seg = sh_close[t - 19 : t + 1]
    if np.isfinite(seg).all():
        ma20_sh[t] = seg.mean()

results = {}

def index_bh(close_arr):
    eq = close_arr[i0:i1] / close_arr[i0] * 1_000_000.0
    rets = eq[1:] / eq[:-1] - 1.0
    total = eq[-1] / eq[0] - 1.0
    ann = (1 + total) ** (242 / max(len(eq) - 1, 1)) - 1
    peak = np.maximum.accumulate(eq)
    sharpe = rets.mean() / rets.std() * np.sqrt(242) if rets.std() > 0 else 0
    return {"total": total, "annual": ann, "max_dd": (eq / peak - 1).min(),
            "sharpe": sharpe, "daily_win": (rets > 0).mean(), "trades": 0, "equity": eq.tolist()}

results["上证指数持有"] = index_bh(sh_close)
results["科创50持有"] = index_bh(kc_close)

# ---- 统一模拟器（全量再平衡：调仓日先卖后买，目标名单制，T 日开盘成交）----
class Sim:
    def __init__(self, cash=1_000_000.0):
        self.cash = cash
        self.mv = np.zeros(n_a)
        self.hold = np.zeros(n_a, dtype=bool)
        self.equity = []
        self.trades = 0

    def rebalance(self, t, target_idx):
        """t: 当日行号(正式区间索引); target_idx: 目标持仓布尔。卖出全部→按目标买入。"""
        # 开盘卖出全部持仓
        if self.hold.any():
            mv_sell = self.mv[self.hold].sum()
            self.cash += mv_sell * (1 - COST_SELL)
            self.mv[self.hold] = 0.0
            self.hold[:] = False
            self.trades += 1
        # 开盘按目标买入（涨停/停牌不可买）
        blocked = ~np.isfinite(open_chg_f[t]) | (open_chg_f[t] >= 0.095)
        can = target_idx & (~blocked)
        n_buy = int(can.sum())
        if n_buy > 0 and self.cash > 1e4:
            budget = self.cash / n_buy
            self.mv[can] += budget * (1 - COST_BUY)
            self.cash -= budget * n_buy
            self.hold[can] = True
            self.trades += n_buy
        # 日终收益
        day_r = np.where(self.hold, r_cc_f[t], 0.0)
        day_r = np.where(np.isfinite(day_r), day_r, 0.0)
        self.mv *= (1.0 + day_r)
        self.equity.append(self.cash + self.mv.sum())

    def hold_day(self, t):
        """非调仓日：仅日终收益。"""
        day_r = np.where(self.hold, r_cc_f[t], 0.0)
        day_r = np.where(np.isfinite(day_r), day_r, 0.0)
        self.mv *= (1.0 + day_r)
        self.equity.append(self.cash + self.mv.sum())

    def stats(self):
        eq = np.array(self.equity)
        rets = eq[1:] / eq[:-1] - 1.0
        total = eq[-1] / eq[0] - 1.0
        ann = (1 + total) ** (242 / max(len(eq) - 1, 1)) - 1
        peak = np.maximum.accumulate(eq)
        dd = eq / peak - 1.0
        sharpe = rets.mean() / rets.std() * np.sqrt(242) if rets.std() > 0 else 0.0
        win = (rets > 0).mean() if len(rets) else 0.0
        return {"total": total, "annual": ann, "max_dd": dd.min(), "sharpe": sharpe,
                "daily_win": win, "trades": self.trades, "equity": eq.tolist()}

# 月初边界（正式区间内）
month_boundaries = []
for k in range(1, len(dates_f)):
    if dates_f[k][:7] != dates_f[k - 1][:7]:
        month_boundaries.append(k)

Z = np.zeros(n_a, dtype=bool)

# ---- S2 科技等权买入持有（月初再平衡）----
sim = Sim()
ALL = np.ones(n_a, dtype=bool)
for t in range(len(dates_f)):
    if t == 0 or t in month_boundaries:
        sim.rebalance(t, ALL)
    else:
        sim.hold_day(t)
results["科技等权持有(月再平衡)"] = sim.stats()

# ---- S3 上证MA20择时 + 科技等权（切换日再平衡）----
sim = Sim()
sim.rebalance(0, ALL)  # 初始满仓科技等权（与 S2 同一起点，非信号）
state = 1
for t in range(1, len(dates_f)):
    sig = i0 + t - 1
    target = state
    if np.isfinite(ma20_sh[sig]) and np.isfinite(sh_close[sig]):
        target = 1 if sh_close[sig] > ma20_sh[sig] else 0
    if target != state:
        if target == 1:
            sim.rebalance(t, ALL)
        else:
            if sim.hold.any():
                mv_sell = sim.mv[sim.hold].sum()
                sim.cash += mv_sell * (1 - COST_SELL)
                sim.mv[sim.hold] = 0.0
                sim.hold[:] = False
                sim.trades += 1
            sim.equity.append(sim.cash)
        state = target
    else:
        sim.hold_day(t)
results["上证MA20择时+科技等权"] = sim.stats()

# ---- S4 科技双均线趋势（周频调仓，信号 T-1，全量再平衡）----
week_bound = list(range(0, len(dates_f), 5))
sim = Sim()
for t in range(len(dates_f)):
    if t in week_bound:
        sig = i0 + t - 1
        cond_up = np.isfinite(ma20[sig]) & np.isfinite(ma60[sig]) & (close_m[sig] > ma20[sig]) & (ma20[sig] > ma60[sig])
        sim.rebalance(t, cond_up)
    else:
        sim.hold_day(t)
results["科技双均线趋势(周频)"] = sim.stats()

# ---- S5 科技动量轮动（月频，过去20日涨幅前25%，信号 T-1，全量再平衡）----
sim = Sim()
for t in range(len(dates_f)):
    if t == 0 or t in month_boundaries:
        sig = i0 + t - 1
        r20 = ret20[sig]
        valid = np.isfinite(r20) & (r20 > -0.5)
        q = int(np.ceil(valid.sum() * 0.25))
        target = np.zeros(n_a, dtype=bool)
        if q > 0 and valid.any():
            top_idx = np.argsort(-np.where(valid, r20, -np.inf))[:q]
            target[top_idx] = True
        sim.rebalance(t, target)
    else:
        sim.hold_day(t)
results["科技动量轮动(月频前25%)"] = sim.stats()

# ---- S6 科技低吸（等权指数乖离，信号 T-1）----
tech_ew = np.full(len(dates_arr), np.nan)
base_valid = np.isfinite(close_m[i0]) & (close_m[i0] > 0)
for t in range(len(dates_arr)):
    valid = np.isfinite(close_m[t]) & (close_m[t] > 0) & base_valid
    if valid.sum() >= 50:
        tech_ew[t] = (close_m[t][valid] / close_m[i0][valid]).mean()
tech_ew_n = tech_ew / tech_ew[i0] * 1_000_000.0
ma60_ew = np.full(len(tech_ew_n), np.nan)
ma20_ew = np.full(len(tech_ew_n), np.nan)
for t in range(60, len(tech_ew_n)):
    seg = tech_ew_n[t - 59 : t + 1]
    if np.isfinite(seg).all():
        ma60_ew[t] = seg.mean()
for t in range(20, len(tech_ew_n)):
    seg = tech_ew_n[t - 19 : t + 1]
    if np.isfinite(seg).all():
        ma20_ew[t] = seg.mean()

cash, pos, state = 1_000_000.0, 0.0, 0
eq = []
for t in range(len(dates_f)):
    tt = i0 + t
    sig = tt - 1
    if sig >= 0 and np.isfinite(ma60_ew[sig]) and np.isfinite(ma20_ew[sig]) and np.isfinite(tech_ew_n[sig]):
        bias = tech_ew_n[sig] / ma60_ew[sig] - 1.0
        if state == 0 and bias <= -0.08:
            spend = 500_000.0
            pos = spend * (1 - COST_BUY)
            cash -= spend
            state = 1
        elif state == 1 and tech_ew_n[sig] >= ma20_ew[sig]:
            cash += pos * (1 - COST_SELL)
            pos = 0.0
            state = 0
    day_r = tech_ew_n[tt + 1] / tech_ew_n[tt] - 1.0 if tt + 1 < len(tech_ew_n) else 0.0
    if state == 1:
        pos *= 1.0 + day_r
    eq.append(cash + pos)
eq = np.array(eq)
rets = eq[1:] / eq[:-1] - 1
total = eq[-1] / eq[0] - 1
ann = (1 + total) ** (242 / max(len(eq) - 1, 1)) - 1
peak = np.maximum.accumulate(eq)
sharpe = rets.mean() / rets.std() * np.sqrt(242) if rets.std() > 0 else 0
results["科技低吸(乖离-8%/MA20离场)"] = {
    "total": total, "annual": ann, "max_dd": (eq / peak - 1).min(),
    "sharpe": sharpe, "daily_win": (rets > 0).mean(), "trades": 0, "equity": eq.tolist()}

print()
print("=" * 104)
print(f"科技股非打板策略横向对比（{START} ~ {END}, {len(dates_f)} 交易日, 全成本含印花税滑点, T-1信号/T日开盘成交）")
print("=" * 104)
for name, r in results.items():
    print(f"{name:24s} 总收益{r['total']*100:8.2f}% 年化{r['annual']*100:8.2f}% 最大回撤{r['max_dd']*100:8.2f}% 夏普{r['sharpe']:6.2f} 日胜率{r['daily_win']*100:5.1f}% 换手{r['trades']:5d}")

out = {"period": [str(START), str(END)], "n_days": len(dates_f), "pool_size": n_a, "results": results}
with open(r"D:\MyTickFlowStockPanel\output\tech_compare.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, default=float)
print("saved output/tech_compare.json")
