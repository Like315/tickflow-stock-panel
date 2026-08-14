"""龙头打板 v6 — 科技主线版（响应"板块聚焦 + 行情参考 + 题材动向"反馈）。

v6 相对 V5 的优化（第七步迭代）：
  9. 科技板块聚焦：股票池限定为科技概念股（AI/芯片/算力/机器人/消费电子/软件/通信等
     关键词白名单，从 ext_gn_ths 概念映射提取）——只做科技主线的打板。
  10. 大盘行情参考：用指数日线（默认上证 000001.SH）做市场状态过滤
      （close>MA20 / 5日动量>0），T 日数据判断、T+1 成交，无前视。
  11. 题材集中度（消息面发酵的量化代理）：科技池涨停家数占全市场涨停家数比例
      >= min_tech_share 才出手——市场主线在科技（资金聚焦）时才参与，主线
      不在科技（题材分散/轮动到他处）时空仓。历史新闻文本无数据源，
      以"概念热度集中度"作为消息面动向前瞻代理（报告中披露）。

其余规则继承 V5 最优：情绪双维度、行业合力(≥5家/≥1%)、空间龙、连板≤3、
不板即走、次日开盘卖、-6% 止损、全成本模型。
"""

import numpy as np
import polars as pl

from app.backtest.matrix import MarketDataMatrix, SignalMatrix, make_signal_matrix, matrix_feature

_DATA_ROOT = __file__.replace("\\", "/").rsplit("/", 4)[0] + "/../data"

_caches: dict = {}


def _tech_pool(keywords: str) -> dict[str, bool]:
    """科技概念白名单：symbol → 是否命中任一科技关键词。"""
    key = "tech:" + keywords
    if key in _caches:
        return _caches[key]
    kws = [k.strip() for k in keywords.split(";") if k.strip()]
    out: dict[str, bool] = {}
    df = pl.read_parquet(_DATA_ROOT + "/ext_data/ext_gn_ths/part.parquet")
    for row in df.iter_rows(named=True):
        sym = str(row.get("symbol"))
        concepts = str(row.get("所属概念") or "")
        hit = any(kw in concepts for kw in kws)
        out[sym] = hit
    _caches[key] = out
    return out


def _index_series(index_symbol: str) -> dict[str, tuple[float, float, float]]:
    """指数日线：date(str) → (close, ma20, mom5)。"""
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
    "id": "limit_up_leader_v6",
    "name": "龙头打板v6-科技主线",
    "description": "科技板块聚焦 + 大盘指数过滤 + 题材集中度 + 情绪双维度 + 行业合力 + 空间龙 + 不板即走",
    "tags": ["涨停", "连板", "打板", "龙头", "科技", "AI", "半导体"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {"id": "tech_filter", "label": "启用科技板块聚焦", "type": "bool", "default": True},
        {"id": "tech_keywords", "label": "科技概念关键词(分号分隔)", "type": "str",
         "default": "人工智能;AI;AIGC;大模型;ChatGPT;DeepSeek;芯片;半导体;集成电路;存储芯片;先进封装;光刻机;算力;服务器;液冷;CPO;光模块;数据中心;IDC;机器人;人形机器人;机器视觉;消费电子;华为概念;苹果概念;折叠屏;智能穿戴;国产软件;信创;操作系统;数据库;数据要素;数字经济;云计算;大数据;5G;6G;卫星互联网;卫星导航;PCB;GPU;智能驾驶;无人驾驶;车联网;低空经济"},
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
        {"id": "leader_tolerance", "label": "空间龙容差(0=仅最高板,1=最高或次高)", "type": "int", "default": 0, "min": 0, "max": 3, "step": 1},
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


class LimitUpLeaderV6MatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "consecutive_limit_ups", "amount", "turnover_rate"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        shape = market.shape
        n_t = shape[0]
        entry = market.limit_up_locked.astype(bool).copy()

        # 1) 科技板块聚焦（股票池白名单）
        if params.get("tech_filter", True):
            pool = _tech_pool(str(params.get("tech_keywords", "")))
            tech_mask = np.array([pool.get(s, False) for s in market.symbols], dtype=bool)
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

        # 4) 情绪双维度
        if params.get("use_emotion_filter", True):
            daily_limits = np.sum(market.limit_up_locked.astype(bool), axis=1)
            daily_max_height = np.max(np.where(np.isfinite(boards), boards, 0), axis=1)
            ok_day = (
                (daily_limits >= int(params.get("min_daily_limit_ups", 80)))
                & (daily_max_height >= int(params.get("min_leader_height", 3)))
            )
            entry &= ok_day[:, None]

        # 5) 大盘指数过滤（T 日收盘数据）
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
                    index_ok[t] = (
                        (bool(np.isfinite(ma20)) and close > ma20)
                        and (bool(np.isfinite(mom5)) and mom5 > 0)
                    )
            entry &= index_ok[:, None]

        # 6) 题材集中度：科技池涨停占比（消息面发酵的量化代理）
        min_share = float(params.get("min_tech_share", 0.3))
        if min_share > 0 and params.get("tech_filter", True):
            locked = market.limit_up_locked.astype(bool)
            tech_locked = locked[:, tech_mask]
            tech_limits = tech_locked.sum(axis=1).astype(np.float64)
            total_limits = locked.sum(axis=1).astype(np.float64)
            share = np.where(total_limits > 0, tech_limits / np.maximum(total_limits, 1), 0.0)
            entry &= (share >= min_share)[:, None]

        # 7) 行业板块效应 + 行业强度（继承 v3/v4；模块化复用 v4 的实现思路）
        if params.get("use_theme_filter", True) or params.get("use_industry_strength", True):
            hy_map = _hy_map()
            ind_change, ind_limits = _industry_daily(market, hy_map)
            if params.get("use_theme_filter", True):
                entry &= ind_limits >= float(params.get("min_theme_limits", 5))
            if params.get("use_industry_strength", True):
                min_ichg = float(params.get("min_industry_change_pct", 1.0)) / 100.0
                entry &= np.where(np.isfinite(ind_change), ind_change >= min_ichg, False)

        # 8) 空间龙收敛（leader_tolerance=1 允许"最高板或次高板"，避免与连板≤3冲突导致过严）
        if params.get("space_leader_only", False):
            daily_max_height = np.max(np.where(np.isfinite(boards), boards, 0), axis=1)
            tolerance = int(params.get("leader_tolerance", 0))
            entry &= boards >= np.maximum(daily_max_height - tolerance, 1)[:, None]
            entry &= boards >= 1

        # 9) 长假风险规避
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

        # 10) 卖出：不板即走
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


MATRIX_STRATEGY = LimitUpLeaderV6MatrixStrategy()
