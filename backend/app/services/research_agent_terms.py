"""内置研究术语知识库。

答案强调“如何理解”和“不能单独说明什么”，避免把指标解释成固定交易规则。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import re

from app.services.research_agent_models import ResearchTerm


def _term(
    id: str,
    name: str,
    aliases: list[str],
    definition: str,
    interpretation: str,
    limitation: str,
    combine_with: list[str],
) -> ResearchTerm:
    return ResearchTerm(
        id=id,
        name=name,
        aliases=aliases,
        definition=definition,
        interpretation=interpretation,
        limitation=limitation,
        combine_with=combine_with,
    )


_TERMS = [
    _term(
        "ma_golden",
        "均线金叉",
        ["金叉", "MA金叉", "MA5上穿MA20"],
        "短周期均线从下方向上穿过长周期均线。",
        "表示近期价格重心相对中期价格重心改善，配合趋势和成交量时更有参考价值。",
        "金叉通常滞后，震荡市会反复出现，不能单独等同于买入。",
        ["成交量", "中期趋势", "市场环境"],
    ),
    _term(
        "ma_dead",
        "均线死叉",
        ["死叉", "MA死叉", "MA5下穿MA20"],
        "短周期均线从上方向下穿过长周期均线。",
        "表示近期价格重心相对中期走弱，需要观察趋势是否进一步破坏。",
        "死叉同样滞后，急跌后的低位死叉可能反映已经发生的风险。",
        ["支撑位", "波动率", "基本面变化"],
    ),
    _term(
        "macd",
        "MACD",
        ["指数平滑异同移动平均线"],
        "用快慢指数移动平均线的差值及其信号线观察趋势和动量变化。",
        "DIF、DEA、柱体与零轴的位置组合比单看一次交叉更有意义。",
        "MACD 属于滞后指标，震荡阶段容易反复发出交叉信号，不能单独作为买卖依据。",
        ["中期趋势", "零轴位置", "成交量", "价格背离"],
    ),
    _term(
        "macd_golden",
        "MACD 金叉",
        ["MACD金叉", "DIF上穿DEA"],
        "MACD 的 DIF 线从下方向上穿过 DEA 线。",
        "零轴附近或上方、柱体同步增强且量价配合时，动量改善更可信。",
        "低级别金叉可能只是反弹，不能忽略中期趋势、位置与量能。",
        ["MACD零轴", "成交量", "均线结构"],
    ),
    _term(
        "macd_dead",
        "MACD 死叉",
        ["MACD死叉", "DIF下穿DEA"],
        "MACD 的 DIF 线从上方向下穿过 DEA 线。",
        "反映动量边际转弱，若同时跌破趋势支撑，风险更明确。",
        "高位死叉与低位钝化含义不同，单次交叉不能确定趋势反转。",
        ["关键位", "趋势", "量价背离"],
    ),
    _term(
        "rsi",
        "RSI",
        ["相对强弱指标", "RSI14", "RSI6"],
        "衡量一段时间内上涨与下跌力度的动量指标，常见范围为 0～100。",
        "持续位于 50 上方通常代表动量偏强，极端区间需要结合趋势判断。",
        "超买不等于立即下跌，超卖也不等于立即上涨，强趋势中会长期钝化。",
        ["趋势", "背离", "波动率"],
    ),
    _term(
        "boll",
        "布林带",
        ["BOLL", "布林上轨", "布林下轨"],
        "以移动平均线为中轨、价格波动标准差构造上下轨。",
        "带宽反映波动变化，沿上轨运行与冲高回落的含义不同。",
        "突破轨道不天然是反转或追涨信号，需要看收盘位置和量价确认。",
        ["带宽", "成交量", "趋势"],
    ),
    _term(
        "volume_surge",
        "放量",
        ["成交量放大", "量比", "放量上涨"],
        "当期成交量明显高于近期平均水平。",
        "放量说明交易分歧或参与度上升，方向要结合价格、位置和后续承接判断。",
        "放量既可能是资金进入，也可能是高位派发，不能只看量不看价。",
        ["价格方向", "换手率", "关键位"],
    ),
    _term(
        "breakout",
        "突破",
        ["向上突破", "突破MA20", "突破前高", "创60日新高"],
        "价格有效越过均线、前高或区间上沿等观察位。",
        "收盘确认、量能适度和突破后承接稳定会提高可信度。",
        "盘中触及不等于有效突破，过度放量或远离均线可能增加追高风险。",
        ["收盘确认", "量价", "市场环境"],
    ),
    _term(
        "breakdown",
        "跌破",
        ["向下跌破", "跌破MA20", "跌破支撑", "创60日新低"],
        "价格有效下穿均线、前低或区间下沿等观察位。",
        "若收盘无法收回且量能放大，原有趋势假设可能减弱或失效。",
        "短暂假跌破和除权影响需要排除，不能脱离复权口径和位置判断。",
        ["收盘确认", "支撑位", "成交量"],
    ),
    _term(
        "limit_up",
        "涨停",
        ["封板", "涨停板", "连板"],
        "股票价格达到当日交易所允许的涨幅上限。",
        "封单稳定、换手健康和板块联动可反映短期强度，但需要考虑可交易性。",
        "涨停不代表次日一定上涨，连续涨停会显著提高波动和兑现风险。",
        ["封单", "换手", "板块强度"],
    ),
    _term(
        "broken_limit",
        "炸板",
        ["涨停炸板", "开板", "封板失败"],
        "盘中触及涨停但随后打开，收盘未能保持封板。",
        "反映涨停价附近供给和分歧增加，修复速度与承接决定后续含义。",
        "炸板不必然转弱，强势换手板也可能重新封住，需要结合位置和市场情绪。",
        ["回封", "换手率", "市场情绪"],
    ),
    _term(
        "market_breadth",
        "市场宽度",
        ["涨跌家数", "市场广度", "breadth"],
        "用上涨、下跌、平盘家数及其分布衡量行情参与范围。",
        "指数上涨且多数股票同步走强，通常比少数权重拉动更健康。",
        "宽度是市场环境变量，不能直接替代个股趋势和基本面判断。",
        ["指数表现", "成交额", "涨跌停结构"],
    ),
    _term(
        "relative_strength",
        "相对强弱",
        ["RPS", "跑赢指数", "行业强度"],
        "比较股票或行业在同一窗口内相对基准的表现。",
        "持续跑赢基准可说明资金偏好，但需要区分稳健趋势与短期过热。",
        "历史强势不保证继续强势，窗口选择会显著影响结论。",
        ["趋势稳定性", "波动率", "估值"],
    ),
    _term(
        "max_drawdown",
        "最大回撤",
        ["回撤", "MDD"],
        "观察期净值从历史峰值到后续低点的最大跌幅。",
        "用于衡量路径中的下行风险，与最终收益一起看更完整。",
        "回撤是历史路径统计，不代表未来最大损失，也不是实际成交盈亏。",
        ["累计收益", "波动率", "基准表现"],
    ),
]


def list_terms() -> list[ResearchTerm]:
    return list(_TERMS)


def _normalize(text: str) -> str:
    return re.sub(r"[\s_\-，。？！,.?！：:（）()]", "", text).casefold()


def find_term(query: str) -> ResearchTerm | None:
    normalized = _normalize(query)
    if not normalized:
        return None
    best: tuple[int, ResearchTerm] | None = None
    for item in _TERMS:
        names = [item.name, *item.aliases]
        for name in names:
            needle = _normalize(name)
            if normalized == needle:
                return item
            if len(needle) >= 2 and needle in normalized:
                score = len(needle)
                if best is None or score > best[0]:
                    best = (score, item)
    return best[1] if best else None


def term_to_markdown(item: ResearchTerm) -> str:
    combined = "、".join(item.combine_with) or "其他证据"
    return (
        f"### {item.name}\n\n"
        f"**定义：** {item.definition}\n\n"
        f"**通常如何理解：** {item.interpretation}\n\n"
        f"**不能单独说明什么：** {item.limitation}\n\n"
        f"**建议结合：** {combined}。"
    )
