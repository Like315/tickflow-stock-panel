"""基于历史净值与大盘趋势的基金市场研究——不依赖用户持仓。

本模块只依赖两类数据：
1. 公开基金净值历史（东财 pingzhongdata，经 adapter 隔离）；
2. 本地大盘指数历史（沪深300 / 上证指数 / 创业板指 / 科创综指）。

产出：对每个研究对象的四档研判（长期持有 / 减仓 / 可买入 / 观望），
档位由确定性规则给出（可审计），AI 层在此基础上做叙事解读。
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from itertools import pairwise
from typing import Any, Protocol

from app.services.fund_portfolio import parse_localized_number
from app.services.fund_research import (
    _MARKET_INDICES,
    EastmoneyFundResearchProvider,
    _market_trend_snapshot,
)

_BENCHMARK_SYMBOL = "000300.SH"
_BENCHMARK_NAME = "沪深300"

# 研究范围：覆盖宽基、行业主题、主动权益、债券、商品/海外等常见类别。
# 代码均为公开市场常见基金；个别代码查询失败只计入数据缺口，不影响其余基金。
FUND_UNIVERSE: list[dict[str, str]] = [
    {"code": "110020", "name": "易方达沪深300ETF联接A", "category": "宽基指数"},
    {"code": "000311", "name": "景顺长城沪深300指数增强A", "category": "宽基指数"},
    {"code": "000478", "name": "建信中证500指数增强A", "category": "宽基指数"},
    {"code": "110026", "name": "易方达创业板ETF联接A", "category": "宽基指数"},
    {"code": "110003", "name": "易方达上证50指数A", "category": "宽基指数"},
    {"code": "161725", "name": "招商中证白酒指数(LOF)A", "category": "行业主题"},
    {"code": "003095", "name": "中欧医疗健康混合A", "category": "行业主题"},
    {"code": "001594", "name": "天弘中证银行指数A", "category": "行业主题"},
    {"code": "100032", "name": "富国中证红利指数增强A", "category": "行业主题"},
    {"code": "005827", "name": "易方达蓝筹精选混合", "category": "主动权益"},
    {"code": "110011", "name": "易方达中小盘混合", "category": "主动权益"},
    {"code": "163402", "name": "兴全趋势投资混合(LOF)", "category": "主动权益"},
    {"code": "260108", "name": "景顺长城新兴成长混合", "category": "主动权益"},
    {"code": "001938", "name": "中欧时代先锋股票A", "category": "主动权益"},
    {"code": "005911", "name": "广发双擎升级混合A", "category": "主动权益"},
    {"code": "000171", "name": "易方达裕丰回报债券", "category": "债券"},
    {"code": "110017", "name": "易方达增强回报债券A", "category": "债券"},
    {"code": "050027", "name": "博时信用债纯债A", "category": "债券"},
    {"code": "000217", "name": "华安易富黄金ETF联接A", "category": "商品/海外"},
    {"code": "270042", "name": "广发纳斯达克100指数(QDII)A", "category": "商品/海外"},
]

_POLICY = {
    "version": "fund-market-research-v1",
    "min_sample_days": 120,
    "min_sample_days_hold": 250,
    "max_buy": 5,
    "thresholds": {
        "equity": {
            "vol_cap": 35.0,
            "buy_mdd_ge": -18.0,
            "hold_mdd_ge": -25.0,
            "hold_pos_ge": 50.0,
            "hold_r1y_ge": 0.0,
        },
        "bond": {
            "vol_cap": 8.0,
            "buy_mdd_ge": -4.0,
            "hold_mdd_ge": -5.0,
            "hold_pos_ge": 50.0,
            "hold_r1y_ge": 3.0,
        },
    },
    "reduce": {
        "r3m_lt": 0.0,
        "r6m_lt": 0.0,
        "alpha_6m_lt": -8.0,
        "r1m_lt": -10.0,
        "mdd_lt": -15.0,
    },
    "buy": {
        "r6m_gt": 0.0,
        "r1y_gt": 0.0,
        "alpha_6m_gt": 0.0,
    },
    "scope_note": "量化启发式研究框架，不是收益预测或自动交易指令",
}

_TIERS = ("长期持有", "减仓", "可买入", "观望")


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _number(value: Any) -> float | None:
    parsed = parse_localized_number(value)
    return float(parsed) if parsed is not None else None


def _as_dates(history: list[dict[str, Any]]) -> list[tuple[date, float]]:
    rows: list[tuple[date, float]] = []
    for row in history:
        try:
            nav_date = date.fromisoformat(str(row.get("date") or "")[:10])
            nav = float(row.get("nav"))
        except (TypeError, ValueError):
            continue
        if nav > 0 and math.isfinite(nav):
            rows.append((nav_date, nav))
    rows.sort(key=lambda item: item[0])
    return rows


def _market_regime(market_context: list[dict[str, Any]]) -> dict[str, Any]:
    hs300 = next((row for row in market_context if row.get("symbol") == _BENCHMARK_SYMBOL), None)
    if hs300 is None:
        return {
            "regime": "未知",
            "label": "数据不足，无法判断大盘环境",
            "as_of": None,
            "basis": _BENCHMARK_NAME,
        }
    trend = str(hs300.get("trend") or "")
    return_20d = _number(hs300.get("return_20d_pct"))
    if trend == "上行" and (return_20d or 0) > 0:
        regime, label = "上行", "偏多：均线多头排列且短中期动能向上"
    elif trend == "下行" and (return_20d or 0) < 0:
        regime, label = "下行", "偏空：均线空头排列且短中期动能向下"
    else:
        regime, label = "震荡", "结构分化：指数方向不明，宜精选品种控制仓位"
    return {
        "regime": regime,
        "label": label,
        "as_of": hs300.get("as_of"),
        "return_20d_pct": hs300.get("return_20d_pct"),
        "return_60d_pct": hs300.get("return_60d_pct"),
        "basis": _BENCHMARK_NAME,
    }


def _period_alpha(
    history_rows: list[tuple[date, float]],
    benchmark_rows: list[tuple[date, float]],
    days: int,
) -> float | None:
    """基金与基准在同一净值窗口内的区间收益差（百分点）。"""
    if not history_rows or not benchmark_rows:
        return None
    latest_date = history_rows[-1][0]
    cutoff = latest_date - timedelta(days=days)
    fund_eligible = [(d, nav) for d, nav in history_rows if d <= cutoff]
    if not fund_eligible or (cutoff - fund_eligible[-1][0]).days > 10:
        return None
    fund_return = history_rows[-1][1] / fund_eligible[-1][1] - 1
    benchmark_window = [(d, close) for d, close in benchmark_rows if cutoff <= d <= latest_date]
    if len(benchmark_window) < 20:
        return None
    benchmark_return = benchmark_window[-1][1] / benchmark_window[0][1] - 1
    return (fund_return - benchmark_return) * 100


def _benchmark_beta_correlation(
    history_rows: list[tuple[date, float]],
    benchmark_rows: list[tuple[date, float]],
) -> tuple[float | None, float | None]:
    """近 180 个自然日的基金日收益 vs 基准日收益的相关性与 Beta。"""
    if len(history_rows) < 40 or len(benchmark_rows) < 40:
        return None, None
    fund_map = dict(history_rows)
    benchmark_map = dict(benchmark_rows)
    common = sorted(set(fund_map) & set(benchmark_map))
    if len(common) < 40:
        return None, None
    cutoff = common[-1] - timedelta(days=180)
    window = [d for d in common if d >= cutoff]
    if len(window) < 30:
        window = common
    fund_returns: list[float] = []
    benchmark_returns: list[float] = []
    for prev, current in pairwise(window):
        fund_prev, fund_now = fund_map[prev], fund_map[current]
        bench_prev, bench_now = benchmark_map[prev], benchmark_map[current]
        if min(fund_prev, fund_now, bench_prev, bench_now) <= 0:
            continue
        fund_returns.append(fund_now / fund_prev - 1)
        benchmark_returns.append(bench_now / bench_prev - 1)
    if len(fund_returns) < 20:
        return None, None
    fund_mean = statistics.mean(fund_returns)
    benchmark_mean = statistics.mean(benchmark_returns)
    fund_var = sum((value - fund_mean) ** 2 for value in fund_returns)
    benchmark_var = sum((value - benchmark_mean) ** 2 for value in benchmark_returns)
    covariance = sum(
        (f - fund_mean) * (b - benchmark_mean)
        for f, b in zip(fund_returns, benchmark_returns, strict=True)
    )
    correlation = (
        covariance / math.sqrt(fund_var * benchmark_var)
        if fund_var > 0 and benchmark_var > 0
        else None
    )
    beta = covariance / benchmark_var if benchmark_var > 0 else None
    return (
        _rounded(correlation, 4),
        _rounded(beta, 3),
    )


def _classify(fund: dict[str, Any], regime: str) -> dict[str, Any]:
    """确定性规则研判，返回 recommendation 结构。"""
    sample = int(fund.get("sample_days") or 0)
    if sample < _POLICY["min_sample_days"]:
        return {
            "tier": "观望",
            "score": None,
            "reasons": [f"公开净值历史仅 {sample} 个样本，不足 120 个，无法形成可靠研判"],
            "triggers": ["补足净值历史后重新评估"],
            "invalidation": ["数据补齐后本结论自动失效"],
        }

    category = str(fund.get("category") or "主动权益")
    thresholds = (
        _POLICY["thresholds"]["bond"] if category == "债券" else _POLICY["thresholds"]["equity"]
    )
    performance = fund.get("performance_pct") or {}
    r1m = _number(performance.get("1m"))
    r3m = _number(performance.get("3m"))
    r6m = _number(performance.get("6m"))
    r1y = _number(performance.get("1y"))
    alpha_6m = _number(fund.get("alpha_6m_pct"))
    alpha_1y = _number(fund.get("alpha_1y_pct"))
    volatility = _number(fund.get("annualized_volatility_pct"))
    max_drawdown = _number(fund.get("max_drawdown_1y_pct"))
    positive_ratio = _number(fund.get("positive_day_ratio_pct"))

    reduce = _POLICY["reduce"]
    momentum_broken = (
        r3m is not None and r6m is not None and r3m < reduce["r3m_lt"] and r6m < reduce["r6m_lt"]
    )
    clear_underperformance = alpha_6m is not None and alpha_6m <= reduce["alpha_6m_lt"]
    sharp_recent_loss = (
        r1m is not None
        and r1m <= reduce["r1m_lt"]
        and max_drawdown is not None
        and max_drawdown <= reduce["mdd_lt"]
    )

    if momentum_broken or clear_underperformance or sharp_recent_loss:
        reasons = []
        if momentum_broken:
            reasons.append(f"近3月收益 {r3m:.2f}%、近6月收益 {r6m:.2f}% 同时为负，短期趋势走坏")
        if clear_underperformance:
            reasons.append(f"近6月相对沪深300 超额收益 {alpha_6m:.2f}%，显著跑输基准")
        if sharp_recent_loss:
            reasons.append(
                f"近1月收益 {r1m:.2f}% 且近1年最大回撤 {max_drawdown:.2f}%，风险释放未完成"
            )
        return {
            "tier": "减仓",
            "score": None,
            "reasons": reasons,
            "triggers": ["反弹至成本上方或动能修复（近3月转正）后重新评估"],
            "invalidation": ["近3月、近6月收益转正且超额收益回到 -8% 以上时，本结论自动失效"],
        }

    buy_allowed = regime != "下行"
    buy = _POLICY["buy"]
    buy_ok = (
        buy_allowed
        and r6m is not None
        and r6m > buy["r6m_gt"]
        and r1y is not None
        and r1y > buy["r1y_gt"]
        and (alpha_6m is None or alpha_6m > buy["alpha_6m_gt"])
        and volatility is not None
        and volatility <= thresholds["vol_cap"]
        and max_drawdown is not None
        and max_drawdown >= thresholds["buy_mdd_ge"]
    )
    hold_ok = (
        sample >= _POLICY["min_sample_days_hold"]
        and r1y is not None
        and r1y >= thresholds["hold_r1y_ge"]
        and (alpha_1y is None or alpha_1y >= 0 or category == "债券")
        and max_drawdown is not None
        and max_drawdown >= thresholds["hold_mdd_ge"]
        and volatility is not None
        and volatility <= thresholds["vol_cap"]
        and positive_ratio is not None
        and positive_ratio >= thresholds["hold_pos_ge"]
    )

    if buy_ok:
        reasons = [
            f"近6月收益 {r6m:.2f}%、近1年收益 {r1y:.2f}%，动量为正",
        ]
        if alpha_6m is not None:
            reasons.append(f"近6月相对沪深300 超额收益 {alpha_6m:.2f}%")
        reasons.append(f"年化波动率 {volatility:.2f}%、近1年最大回撤 {max_drawdown:.2f}%，风险可控")
        if regime == "上行":
            reasons.append("大盘处于上行环境，权益类配置窗口相对友好")
        else:
            reasons.append(f"大盘处于{regime}环境，可分批小仓位参与")
        return {
            "tier": "可买入",
            "score": None,
            "reasons": reasons,
            "triggers": ["大盘转下行或基金近6月收益转负时停止新增买入"],
            "invalidation": ["近6月收益转负，或近1年最大回撤跌破 -18% 时，本结论自动失效"],
        }

    if hold_ok:
        reasons = [
            f"近1年收益 {r1y:.2f}%",
        ]
        if alpha_1y is not None:
            reasons.append(f"近1年相对沪深300 超额收益 {alpha_1y:.2f}%")
        reasons.append(
            f"年化波动率 {volatility:.2f}%、近1年最大回撤 {max_drawdown:.2f}%、正收益天数占比 {positive_ratio:.1f}%，长期风险收益比良好"
        )
        if regime == "下行":
            reasons.append("大盘处于下行环境，但基金中长期质量未破坏，可继续持有观察")
        return {
            "tier": "长期持有",
            "score": None,
            "reasons": reasons,
            "triggers": ["出现动量破位（近3月、近6月收益同负）或基本面数据转弱时转入减仓评估"],
            "invalidation": ["近3月、近6月收益同时转负，或年化波动率超过阈值时，本结论自动失效"],
        }

    reasons = ["当前数据不满足可买入或长期持有条件，也未触发减仓信号，建议观望"]
    if regime == "下行":
        reasons.append("大盘处于下行环境，控制新增权益敞口")
    if r6m is not None:
        reasons.append(f"近6月收益 {r6m:.2f}%")
    return {
        "tier": "观望",
        "score": None,
        "reasons": reasons,
        "triggers": ["动能或超额收益指标改善后重新评估"],
        "invalidation": ["指标变化满足其它档位条件时，本结论自动失效"],
    }


def _score_funds(funds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """跨基金百分位加权评分（0-100），仅用于同类内排序，不改档位。"""

    def collect(metric: str) -> list[float]:
        result: list[float] = []
        for fund in funds:
            performance = fund.get("performance_pct") or {}
            value = _number(performance.get("1y")) if metric == "r1y" else _number(fund.get(metric))
            if value is not None:
                result.append(value)
        return result

    def percentile(series: list[float], value: float | None) -> float:
        if value is None or not series:
            return 0.0
        return sum(1 for item in series if item < value) / len(series) * 100

    r1y_all = collect("r1y")
    alpha_1y_all = collect("alpha_1y_pct")
    mdd_all = collect("max_drawdown_1y_pct")
    pos_all = collect("positive_day_ratio_pct")

    for fund in funds:
        performance = fund.get("performance_pct") or {}
        r1y = _number(performance.get("1y"))
        alpha_1y = _number(fund.get("alpha_1y_pct"))
        max_drawdown = _number(fund.get("max_drawdown_1y_pct"))
        positive_ratio = _number(fund.get("positive_day_ratio_pct"))
        score = (
            0.35 * percentile(r1y_all, r1y)
            + 0.25 * percentile(alpha_1y_all, alpha_1y if alpha_1y is not None else 0.0)
            + 0.20 * percentile(mdd_all, -max_drawdown if max_drawdown is not None else None)
            + 0.20 * percentile(pos_all, positive_ratio)
        )
        fund["score"] = _rounded(score, 1)
    return funds


def _apply_buy_cap(funds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """外部市场可买入名单上限 5 只，并尽量分散到不同类别。

    已持有的基金不占用外部买入名额：其“可买入”档保留原样，
    在前端展示为加仓候选（区别于外部市场的新增买入名单）。
    """
    cap = _POLICY["max_buy"]
    candidates = sorted(
        [
            fund
            for fund in funds
            if fund["recommendation"]["tier"] == "可买入" and not fund.get("held")
        ],
        key=lambda fund: fund.get("score") or 0,
        reverse=True,
    )
    if len(candidates) <= cap:
        return funds
    by_category: dict[str, list[dict[str, Any]]] = {}
    for fund in candidates:
        by_category.setdefault(str(fund.get("category") or "其他"), []).append(fund)
    picked: list[dict[str, Any]] = []
    categories = list(by_category)
    index = 0
    while len(picked) < cap and any(by_category[c] for c in categories):
        category = categories[index % len(categories)]
        if by_category[category]:
            picked.append(by_category[category].pop(0))
        index += 1
    picked_codes = {str(fund["code"]) for fund in picked}
    for fund in funds:
        if (
            not fund.get("held")
            and fund["recommendation"]["tier"] == "可买入"
            and str(fund["code"]) not in picked_codes
        ):
            fund["recommendation"] = {
                "tier": "观望",
                "score": fund.get("score"),
                "reasons": fund["recommendation"]["reasons"]
                + ["同类中排名靠后，未进入外部买入候选名单"],
                "triggers": ["排名进入同类前列或名单腾出空间后重新评估"],
                "invalidation": ["名单重新生成后本结论自动失效"],
            }
    return funds


class FundMarketResearchProvider(Protocol):
    def market_snapshot(self, code: str, *, include_history: bool = False) -> dict[str, Any]: ...

    def close(self) -> None: ...


class FundMarketResearchService:
    """构建不依赖持仓的基金市场研究上下文与结构化研判。"""

    def __init__(
        self,
        repo=None,
        *,
        market_provider: FundMarketResearchProvider | None = None,
        portfolio_service=None,
    ) -> None:
        self._repo = repo
        self._market_provider = market_provider or EastmoneyFundResearchProvider()
        self._portfolio_service = portfolio_service

    def _held_codes(self) -> list[str]:
        """读取本地账本持仓代码，仅用于展示区分，不参与分析依据。"""
        if self._portfolio_service is None:
            return []
        try:
            portfolio = self._portfolio_service.get_portfolio()
        except Exception:
            return []
        return [
            str(position.get("code") or "")
            for position in portfolio.get("positions") or []
            if position.get("code")
        ]

    def _index_context(self, data_gaps: list[str]) -> list[dict[str, Any]]:
        if self._repo is None:
            data_gaps.append("未接入本地大盘指数数据，无法判断大盘趋势")
            return []
        end = date.today()
        start = end - timedelta(days=180)
        result: list[dict[str, Any]] = []
        for symbol, name in _MARKET_INDICES.items():
            try:
                frame = self._repo.get_index_daily(
                    symbol,
                    start,
                    end,
                    columns=["symbol", "date", "close"],
                )
                snapshot = _market_trend_snapshot(symbol, name, frame.to_dicts())
                if snapshot:
                    result.append(snapshot)
            except Exception as exc:
                data_gaps.append(f"指数 {symbol} 趋势暂不可用：{str(exc)[:100]}")
        if not result:
            data_gaps.append("本地指数历史样本不足，无法判断大盘趋势")
        return result

    def _benchmark_rows(self, data_gaps: list[str]) -> list[tuple[date, float]] | None:
        if self._repo is None:
            data_gaps.append("未接入本地大盘指数数据，无法计算相对沪深300 的超额收益与相关性")
            return None
        end = date.today()
        start = end - timedelta(days=400)
        try:
            frame = self._repo.get_index_daily(
                _BENCHMARK_SYMBOL,
                start,
                end,
                columns=["symbol", "date", "close"],
            )
        except Exception as exc:
            data_gaps.append(f"基准指数 {_BENCHMARK_SYMBOL} 历史暂不可用：{str(exc)[:100]}")
            return None
        rows: list[tuple[date, float]] = []
        for row in frame.to_dicts():
            try:
                row_date = date.fromisoformat(str(row.get("date") or "")[:10])
                close = float(row.get("close"))
            except (TypeError, ValueError):
                continue
            if close > 0 and math.isfinite(close):
                rows.append((row_date, close))
        if len(rows) < 40:
            data_gaps.append("本地沪深300 历史样本不足，无法计算超额收益与相关性")
            return None
        rows.sort(key=lambda item: item[0])
        return rows

    def run_research(
        self,
        codes: list[str] | None = None,
        *,
        held_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        """运行基金市场研究。

        codes: 额外指定要研究的基金代码（覆盖外部池范围）；
        held_codes: 用户当前持有的基金代码，仅用于在前端区分「我持有的」与
        「外部市场」，不参与分析依据——研判仍完全基于历史净值与大盘趋势。
        为 None 时自动从注入的 portfolio_service 读取持仓。
        """
        data_gaps: list[str] = []
        market_context = self._index_context(data_gaps)
        regime = _market_regime(market_context)
        benchmark_rows = self._benchmark_rows(data_gaps)

        universe = list(FUND_UNIVERSE)
        if codes:
            requested = [str(code).strip() for code in codes if code]
            invalid = [code for code in requested if not (len(code) == 6 and code.isdigit())]
            if invalid:
                raise ValueError(f"基金代码必须是 6 位数字：{', '.join(invalid)}")
            known_codes = {item["code"] for item in FUND_UNIVERSE}
            universe = [item for item in universe if item["code"] in requested]
            universe += [
                {"code": code, "name": code, "category": "自定义"}
                for code in requested
                if code not in known_codes
            ]
        held_set = {
            str(code).strip()
            for code in (held_codes if held_codes is not None else self._held_codes())
            if code
        }
        universe_codes = {item["code"] for item in universe}
        universe += [
            {"code": code, "name": code, "category": "自定义"}
            for code in sorted(held_set - universe_codes)
        ]

        funds: list[dict[str, Any]] = []
        as_of_dates: list[str] = []
        for item in universe:
            try:
                snapshot = self._market_provider.market_snapshot(
                    item["code"],
                    include_history=True,
                )
            except Exception as exc:
                data_gaps.append(
                    f"基金 {item['code']} 的公开净值研究数据暂不可用：{str(exc)[:120]}"
                )
                continue
            history = snapshot.pop("history", [])
            history_rows = _as_dates(history)
            fund: dict[str, Any] = {
                **snapshot,
                "category": item["category"],
                "held": str(snapshot["code"]) in held_set,
            }
            if snapshot.get("nav_as_of"):
                as_of_dates.append(str(snapshot["nav_as_of"]))
            alpha_6m = _period_alpha(history_rows, benchmark_rows, 180) if benchmark_rows else None
            alpha_1y = _period_alpha(history_rows, benchmark_rows, 365) if benchmark_rows else None
            correlation, beta = (
                _benchmark_beta_correlation(history_rows, benchmark_rows)
                if benchmark_rows
                else (None, None)
            )
            fund["alpha_6m_pct"] = _rounded(alpha_6m)
            fund["alpha_1y_pct"] = _rounded(alpha_1y)
            fund["correlation_6m"] = correlation
            fund["beta_6m"] = beta
            if benchmark_rows is None:
                fund["benchmark_note"] = "本地沪深300 历史不可用，未计算相对基准的超额收益与相关性"
            elif item["category"] in {"债券", "商品/海外"}:
                fund["benchmark_note"] = (
                    f"{_BENCHMARK_NAME} 仅作境内权益参照，{item['category']}类基金的超额收益含义有限"
                )
            funds.append(fund)

        funds = _score_funds(funds)
        for fund in funds:
            fund["recommendation"] = _classify(fund, regime["regime"])
        funds = _apply_buy_cap(funds)

        summary = {
            tier: sum(1 for fund in funds if fund["recommendation"]["tier"] == tier)
            for tier in _TIERS
        }
        as_of = max(as_of_dates, default=None)
        return {
            "scope": "fund_market",
            "as_of": as_of,
            "currency": "CNY",
            "market_regime": regime,
            "market_context": market_context,
            "benchmark": {"symbol": _BENCHMARK_SYMBOL, "name": _BENCHMARK_NAME},
            "universe_count": len(funds),
            "summary": summary,
            "held_count": sum(1 for fund in funds if fund.get("held")),
            "funds": funds,
            "policy": _POLICY,
            "data_gaps": data_gaps,
            "privacy": "仅使用公开基金净值与本地大盘指数；held 标记仅用于区分展示，不参与分析依据",
        }

    def build_context(
        self,
        codes: list[str] | None = None,
        *,
        held_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.run_research(codes=codes, held_codes=held_codes)

    def close(self) -> None:
        self._market_provider.close()
