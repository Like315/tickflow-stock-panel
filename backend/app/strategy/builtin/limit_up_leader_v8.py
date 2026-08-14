"""龙头打板 v8 — 科技子题材轮动 + 小资金支持。

v8 相对 v6 的优化（第八步迭代）：
  12. 科技子题材轮动：把科技池细分为 7 个子题材（AI算力/AI应用/半导体/机器人/
      消费电子/软件信创/低空经济），每日识别"当日最强子题材"（涨停家数最多且
      ≥ subtheme_min_limits），只做最强子题材内的股票 —— 跟随主线轮动，
      而不是全科技池一把抓。
  13. 小资金支持：max_price 股价上限（T 日收盘价过滤，默认 50 元，一手 100 股
      成本 ≤ 5000 元，10 万资金分 4 仓可买 5 手/仓）+ initial_capital 可下调到
      10 万；引擎天然处理"买不起一手跳过"（buy_lot_size）。

其余规则继承 v6：大盘指数过滤、题材集中度、情绪双维度、行业合力、空间龙
（容差0）、连板≤3、不板即走、次日开盘卖、-6% 止损、全成本模型。
"""

import numpy as np
import polars as pl

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix, matrix_feature

_DATA_ROOT = __file__.replace("\\", "/").rsplit("/", 4)[0] + "/../data"

_caches: dict = {}

# 科技子题材 → 概念关键词（命中任一即属于该子题材）
SUBTHEMES: dict[str, tuple[str, ...]] = {
    "AI算力": ("算力", "服务器", "液冷", "CPO", "光模块", "数据中心", "IDC", "云计算", "GPU"),
    "AI应用": ("人工智能", "AI", "AIGC", "大模型", "ChatGPT", "DeepSeek", "智能驾驶", "无人驾驶", "车联网"),
    "半导体": ("芯片", "半导体", "集成电路", "存储芯片", "先进封装", "光刻机", "PCB", "封测", "晶圆"),
    "机器人": ("机器人", "人形机器人", "机器视觉", "减速器"),
    "消费电子": ("消费电子", "华为概念", "苹果概念", "折叠屏", "智能穿戴", "5G", "6G", "卫星互联网", "卫星导航"),
    "软件信创": ("国产软件", "信创", "操作系统", "数据库", "数据要素", "数字经济", "大数据"),
    "低空经济": ("低空经济",),
}


def _subtheme_map() -> tuple[dict[str, tuple[str, ...]], dict[str, bool]]:
    """返回 (symbol → 子题材名列表, symbol → 是否科技股)。"""
    key = "subtheme"
    if key in _caches:
        return _caches[key]
    asset_themes: dict[str, tuple[str, ...]] = {}
    tech_flags: dict[str, bool] = {}
    df = pl.read_parquet(_DATA_ROOT + "/ext_data/ext_gn_ths/part.parquet")
    for row in df.iter_rows(named=True):
        sym = str(row.get("symbol"))
        concepts = str(row.get("所属概念") or "")
        matched = tuple(st for st, kws in SUBTHEMES.items() if any(k in concepts for k in kws))
        asset_themes[sym] = matched
        tech_flags[sym] = bool(matched)
    _caches[key] = (asset_themes, tech_flags)
    return _caches[key]


def _index_series(index_symbol: str) -> dict[str, tuple[float, float, float]]:
    key = "idx:" + index_symbol
    if key in _caches:
        return _caches[key]
    df = (
        pl.scan_parquet(_DATA_ROOT + "/kline_index_daily/**/*.parquet")
        .filter(pl.col("symbol") == index_symbol)
        .sort("date")
        .collect()
    )
    if df.is_empty():
        _caches[key] = {}
        return {}
    df = df.with_columns([
        pl.col("close").rolling_mean(20).alias("ma20"),
        (pl.col("close") / pl.col("close").shift(5) - 1).alias("mom5"),
    ])
    out: dict[str, tuple[float, float, float]] = {}
    for row in df.iter_rows(named=True):
        c = row["close"]
        if c is None or c != c:
            continue
        ma20 = row["ma20"]
        mom5 = row["mom5"]
        out[str(row["date"])[:10]] = (
            float(c),
            float(ma20) if ma20 is not None and ma20 == ma20 else float("nan"),
            float(mom5) if mom5 is not None and mom5 == mom5 else float("nan"),
        )
    _caches[key] = out
    return out


def _matrix_dates(market: MarketDataMatrix) -> list[str]:
    ts = np.asarray(market.timestamps)
    if ts.dtype.kind == "i":
        return [str(np.datetime_as_string(ts.astype("datetime64[ms]")[i], unit="D")) for i in range(ts.size)]
    return [str(ts[i])[:10] for i in range(ts.size)]


META = {
    "id": "limit_up_leader_v8",
    "name": "龙头打板v8-科技轮动",
    "description": "科技子题材轮动(当日最强子题材) + 大盘过滤 + 题材集中度 + 小资金支持(股价上限)",
    "tags": ["涨停", "连板", "打板", "龙头", "科技", "轮动", "小资金"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {"id": "subtheme_mode", "label": "轮动模式(top1=只做当日最强子题材/any=任一达标即可)", "type": "str", "default": "top1"},
        {"id": "subtheme_min_limits", "label": "子题材涨停家数下限", "type": "int", "default": 3, "min": 1, "max": 30, "step": 1},
        {"id": "max_price", "label": "股价上限(元, 0=不限, 小资金一手成本控制)", "type": "float", "default": 0.0, "min": 0.0, "max": 500.0, "step": 5.0},
        {"id": "use_index_filter", "label": "启用大盘指数过滤", "type": "bool", "default": True},
        {"id": "index_symbol", "label": "参考指数", "type": "str", "default": "000001.SH"},
        {"id": "index_mode", "label": "指数过滤模式(ma20/mom5/both)", "type": "str", "default": "ma20"},
        {"id": "min_tech_share", "label": "科技涨停占比下限(题材集中度)", "type": "float", "default": 0.3, "min": 0.0, "max": 1.0, "step": 0.05},
        {"id": "min_boards", "label": "最少连板数", "type": "int", "default": 1, "min": 1, "max": 6, "step": 1},
        {"id": "max_boards", "label": "最多连板数(0=不限)", "type": "int", "default": 3, "min": 0, "max": 12, "step": 1},
        {"id": "use_emotion_filter", "label": "启用情绪过滤", "type": "bool", "default": True},
        {"id": "min_daily_limit_ups", "label": "全市场涨停家数下限(广度)", "type": "int", "default": 80, "min": 5, "max": 200, "step": 5},
        {"id": "min_leader_height", "label": "当日最高连板下限(高度)", "type": "int", "default": 3, "min": 1, "max": 10, "step": 1},
        {"id": "min_amount_yi", "label": "最低成交额(亿)", "type": "float", "default": 3.0, "min": 0.5, "max": 30.0, "step": 0.5},
        {"id": "space_leader_only", "label": "只打当日最高板(空间龙)", "type": "bool", "default": True},
        {"id": "leader_tolerance", "label": "空间龙容差", "type": "int", "default": 0, "min": 0, "max": 3, "step": 1},
        {"id": "exit_on_not_locked", "label": "不板即走", "type": "bool", "default": True},
        {"id": "use_theme_filter", "label": "启用行业板块效应过滤", "type": "bool", "default": True},
        {"id": "min_theme_limits", "label": "行业涨停家数下限", "type": "int", "default": 5, "min": 1, "max": 30, "step": 1},
        {"id": "use_industry_strength", "label": "启用行业涨跌强度过滤", "type": "bool", "default": True},
        {"id": "min_industry_change_pct", "label": "行业平均涨幅下限%", "type": "float", "default": 1.0, "min": -3.0, "max": 8.0, "step": 0.5},
        {"id": "skip_long_gap", "label": "规避长假后首日跳空", "type": "bool", "default": True},
    ],
    "scoring": {"consecutive_limit_ups": 0.5, "amount": 0.3, "turnover_rate": 0.2},
    "order_by": "score",
    "descending": True,
    "limit": 50,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_limit_up"]
EXIT_SIGNALS = []
STOP_LOSS = -0.06
MAX_HOLD_DAYS = 6
ALERTS = []


class LimitUpLeaderV8MatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "consecutive_limit_ups", "amount", "turnover_rate", "raw_close"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        shape = market.shape
        n_t = shape[0]
        n_a = shape[1]
        entry = market.limit_up_locked.astype(bool).copy()

        # 1) 科技池 + 子题材映射
        asset_themes, tech_flags = _subtheme_map()
        tech_mask = np.array([tech_flags.get(s, False) for s in market.symbols], dtype=bool)
        entry &= tech_mask[None, :]

        # 2) 连板数过滤
        boards = matrix_feature(market, "consecutive_limit_ups")
        entry &= boards >= int(params.get("min_boards", 1))
        max_boards = int(params.get("max_boards", 0))
        if max_boards > 0:
            entry &= boards <= max_boards

        # 3) 成交额过滤
        min_amount = float(params.get("min_amount_yi", 3.0)) * 1e8
        if min_amount > 0:
            amount = market.field("amount")
            valid = np.isfinite(amount)
            entry &= np.where(valid, amount >= min_amount, False)

        # 3.5) 股价上限（小资金：一手 100 股成本可控）
        max_price = float(params.get("max_price", 0.0))
        if max_price > 0:
            if "raw_close" in market.fields:
                px = market.field("raw_close")
            else:
                px = market.close
            px_valid = np.isfinite(px)
            entry &= np.where(px_valid, px <= max_price, False)

        # 4) 情绪双维度
        if params.get("use_emotion_filter", True):
            daily_limits = np.sum(market.limit_up_locked.astype(bool), axis=1)
            daily_max_height = np.max(np.where(np.isfinite(boards), boards, 0), axis=1)
            ok_day = (
                (daily_limits >= int(params.get("min_daily_limit_ups", 80)))
                & (daily_max_height >= int(params.get("min_leader_height", 3)))
            )
            entry &= ok_day[:, None]

        # 5) 大盘指数过滤
        if params.get("use_index_filter", True):
            idx = _index_series(str(params.get("index_symbol", "000001.SH")))
            mode = str(params.get("index_mode", "ma20"))
            dates = _matrix_dates(market)
            index_ok = np.zeros(n_t, dtype=bool)
            for t, d in enumerate(dates):
                rec = idx.get(d)
                if rec is None:
                    continue
                close, ma20, mom5 = rec
                if mode == "ma20":
                    index_ok[t] = bool(np.isfinite(ma20)) and close > ma20
                elif mode == "mom5":
                    index_ok[t] = bool(np.isfinite(mom5)) and mom5 > 0
                elif mode == "both":
                    index_ok[t] = (bool(np.isfinite(ma20)) and close > ma20) and (bool(np.isfinite(mom5)) and mom5 > 0)
            entry &= index_ok[:, None]

        # 6) 题材集中度
        min_share = float(params.get("min_tech_share", 0.3))
        if min_share > 0:
            locked = market.limit_up_locked.astype(bool)
            tech_limits = locked[:, tech_mask].sum(axis=1).astype(np.float64)
            total_limits = locked.sum(axis=1).astype(np.float64)
            share = np.where(total_limits > 0, tech_limits / np.maximum(total_limits, 1), 0.0)
            entry &= (share >= min_share)[:, None]

        # 7) 子题材轮动（v8 核心）
        mode = str(params.get("subtheme_mode", "top1"))
        min_st = int(params.get("subtheme_min_limits", 3))
        subtheme_names = sorted(SUBTHEMES.keys())
        n_sub = len(subtheme_names)
        # asset → 所属子题材索引列表（支持多归属）
        asset_st_list: list[list[int]] = []
        asset_st_primary = np.full(n_a, -1, dtype=np.int32)
        for ai, s in enumerate(market.symbols):
            themes = asset_themes.get(s, ())
            idxs = [subtheme_names.index(t) for t in themes if t in subtheme_names]
            asset_st_list.append(idxs)
            if idxs:
                asset_st_primary[ai] = idxs[0]
        if mode in ("top1", "any"):
            locked = market.limit_up_locked.astype(bool)
            st_limits = np.zeros((n_t, n_sub), dtype=np.float32)
            for ti in range(n_t):
                for si in range(n_sub):
                    members = np.flatnonzero(asset_st_primary == si)
                    if members.size:
                        st_limits[ti, si] = float(locked[ti, members].sum())
            if mode == "top1":
                best = np.argmax(st_limits, axis=1)
                best_count = st_limits[np.arange(n_t), best]
                ok_day = best_count >= min_st
                keep = np.zeros((n_t, n_a), dtype=bool)
                for ti in range(n_t):
                    if ok_day[ti]:
                        for ai in range(n_a):
                            if best[ti] in asset_st_list[ai]:
                                keep[ti, ai] = True
                entry &= keep
            else:  # any：任一所属子题材达标即可
                st_ok = st_limits >= min_st
                keep = np.zeros((n_t, n_a), dtype=bool)
                for ti in range(n_t):
                    for ai in range(n_a):
                        if any(st_ok[ti, si] for si in asset_st_list[ai]):
                            keep[ti, ai] = True
                entry &= keep

        # 8) 行业板块效应 + 行业强度
        if params.get("use_theme_filter", True) or params.get("use_industry_strength", True):
            hy_map = _hy_map()
            ind_change, ind_limits = _industry_daily(market, hy_map)
            if params.get("use_theme_filter", True):
                entry &= ind_limits >= float(params.get("min_theme_limits", 5))
            if params.get("use_industry_strength", True):
                min_ichg = float(params.get("min_industry_change_pct", 1.0)) / 100.0
                entry &= np.where(np.isfinite(ind_change), ind_change >= min_ichg, False)

        # 9) 空间龙收敛：top1 模式下用"当日最强子题材内最高连板"（题材内龙头），
        #    否则用全市场最高板。避免与连板≤3 冲突导致信号为空。
        if params.get("space_leader_only", False):
            tolerance = int(params.get("leader_tolerance", 0))
            if mode == "top1" and ok_day.any():
                st_max_height = np.zeros((n_t, n_sub), dtype=np.float32)
                for ti in range(n_t):
                    for si in range(n_sub):
                        members = np.flatnonzero(asset_st_primary == si)
                        if members.size:
                            seg = boards[ti, members]
                            seg = seg[np.isfinite(seg)]
                            if seg.size:
                                st_max_height[ti, si] = float(seg.max())
                target = np.zeros(n_t, dtype=np.float32)
                for ti in range(n_t):
                    if ok_day[ti]:
                        target[ti] = st_max_height[ti, best[ti]]
                entry &= boards >= np.maximum(target - tolerance, 1)[:, None]
            else:
                daily_max_height = np.max(np.where(np.isfinite(boards), boards, 0), axis=1)
                entry &= boards >= np.maximum(daily_max_height - tolerance, 1)[:, None]
            entry &= boards >= 1

        # 10) 长假规避
        if params.get("skip_long_gap", True):
            try:
                ts = np.asarray(market.timestamps)
                if ts.size > 1 and ts.dtype.kind == "i":
                    days = ts.astype("datetime64[ms]").astype("datetime64[D]").astype(np.int64)
                    gaps = np.diff(days)
                    skip_mask = np.zeros(n_t, dtype=bool)
                    skip_mask[:-1] = gaps > 4
                    entry &= (~skip_mask)[:, None]
            except Exception:
                pass

        # 11) 卖出：不板即走
        exit_sig = None
        if params.get("exit_on_not_locked", True):
            exit_sig = (~market.limit_up_locked.astype(bool)).astype(np.uint8)

        return make_signal_matrix(
            shape,
            entry=entry.astype(np.uint8),
            exit=exit_sig,
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_sig, 0, -1).astype(np.int16) if exit_sig is not None else None,
            entry_signal_ids=("signal_limit_up",),
            exit_signal_ids=("not_locked",),
        )


def _hy_map() -> dict[str, tuple[str, ...]]:
    if "hy" in _caches:
        return _caches["hy"]
    out: dict[str, tuple[str, ...]] = {}
    df = pl.read_parquet(_DATA_ROOT + "/ext_data/ext_hy_ths/part.parquet")
    for row in df.iter_rows(named=True):
        raw = str(row.get("所属同花顺行业") or "")
        first = raw.split("-")[0].strip()
        if first:
            out[str(row.get("symbol"))] = (first,)
    _caches["hy"] = out
    return out


def _industry_daily(market: MarketDataMatrix, hy_map: dict) -> tuple[np.ndarray, np.ndarray]:
    symbols = market.symbols
    industries = sorted({v[0] for v in hy_map.values() if v})
    i2i = {ind: i for i, ind in enumerate(industries)}
    n_i = len(industries)
    n_a = len(symbols)
    rows, cols = [], []
    asset_ind: list[int] = []
    for ai, s in enumerate(symbols):
        inds = hy_map.get(s, ())
        if not inds or inds[0] not in i2i:
            asset_ind.append(-1)
            continue
        ii = i2i[inds[0]]
        asset_ind.append(ii)
        rows.append(ii)
        cols.append(ai)
    ind_asset = np.zeros((n_i, n_a), dtype=np.float32)
    ind_asset[rows, cols] = 1.0
    counts = ind_asset.sum(axis=1).reshape(1, -1)
    change = matrix_feature(market, "change_pct")
    change_valid = np.where(np.isfinite(change), change, 0.0)
    ind_change_sum = change_valid @ ind_asset.T
    ind_change = np.where(counts > 0, ind_change_sum / np.maximum(counts, 1), np.nan)
    locked = market.limit_up_locked.astype(np.float32)
    ind_limits = locked @ ind_asset.T
    strength = np.full((market.shape[0], n_a), np.nan, dtype=np.float32)
    limits = np.zeros((market.shape[0], n_a), dtype=np.float32)
    for ai, ii in enumerate(asset_ind):
        if ii >= 0:
            strength[:, ai] = ind_change[:, ii]
            limits[:, ai] = ind_limits[:, ii]
    return strength, limits


MATRIX_STRATEGY = LimitUpLeaderV8MatrixStrategy()
