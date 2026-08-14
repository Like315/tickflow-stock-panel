"""Structured, privacy-conscious research context for local fund portfolios."""
# ruff: noqa: RUF001
from __future__ import annotations

import json
import math
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Protocol

import httpx

from app.services.fund_portfolio import parse_localized_number

_PERIOD_DAYS = {"1m": 30, "3m": 90, "6m": 180, "1y": 365}
_MARKET_INDICES = {
    "000001.SH": "上证指数",
    "000300.SH": "沪深300",
    "399006.SZ": "创业板指",
    "000680.SH": "科创综指",
}
_OPERATION_POLICY = {
    "version": "fund-risk-policy-v1",
    "concentration_review_weight_pct": 35.0,
    "short_term_weakness_pct": -10.0,
    "disclosed_holdings_weakness_pct": -8.0,
    "high_volatility_pct": 50.0,
    "sell_review_rule": "用户持仓亏损，且基金近3月与近6月收益同时为负",
    "tiers": ["继续持有观察", "降低风险暴露", "进入卖出评估", "信息不足"],
    "scope_note": "面板启发式风险政策，不是收益预测或自动交易指令",
}


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _number(value: Any) -> float | None:
    parsed = parse_localized_number(value)
    return float(parsed) if parsed is not None else None


def _extract_json_variable(text: str, name: str) -> Any:
    match = re.search(rf"var\s+{re.escape(name)}\s*=\s*([\s\S]*?);", text)
    if not match:
        raise ValueError(f"公开数据缺少 {name}")
    return json.loads(match.group(1))


def _market_segment(code: str) -> str:
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if len(code) == 6 and code.startswith(("4", "8", "9")):
        return "北交所"
    if len(code) == 6 and code.startswith(("5", "6")):
        return "沪市证券"
    if len(code) == 6 and code.isdigit():
        return "深市证券"
    return "境外或其他市场"


def _a_share_symbol(code: str) -> str | None:
    if not re.fullmatch(r"\d{6}", code):
        return None
    if code.startswith(("4", "8", "9")):
        return f"{code}.BJ"
    if code.startswith(("5", "6")):
        return f"{code}.SH"
    return f"{code}.SZ"


class _HoldingsTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _parse_holdings_archive(payload: str) -> tuple[str | None, list[dict[str, Any]]]:
    content_match = re.search(r'content\s*:\s*("(?:\\.|[^"\\])*")\s*,', payload)
    if not content_match:
        raise ValueError("公开数据未返回基金持仓明细")
    html = json.loads(content_match.group(1))
    date_match = re.search(r"截止至[\s\S]*?(\d{4}-\d{2}-\d{2})", html)
    parser = _HoldingsTableParser()
    parser.feed(html)
    holdings: list[dict[str, Any]] = []
    for cells in parser.rows:
        if len(cells) < 9 or not cells[0].isdigit() or not cells[1].strip():
            continue
        code = cells[1].strip()
        weight = _number(cells[6])
        holdings.append({
            "rank": int(cells[0]),
            "security_code": code,
            "security_name": cells[2].strip(),
            "market_segment": _market_segment(code),
            "nav_weight_pct": _rounded(weight),
            "shares_10k": _rounded(_number(cells[7])),
            "market_value_10k_cny": _rounded(_number(cells[8])),
        })
        if len(holdings) >= 10:
            break
    return (date_match.group(1) if date_match else None), holdings


def _parse_asset_allocation(text: str) -> dict[str, Any] | None:
    try:
        document = _extract_json_variable(text, "Data_assetAllocation")
    except (json.JSONDecodeError, ValueError):
        return None
    categories = document.get("categories") or []
    series = document.get("series") or []
    if not categories or not series:
        return None
    latest_index = len(categories) - 1
    result: dict[str, Any] = {"as_of": str(categories[latest_index])}
    field_by_name = {
        "股票占净比": "stock_pct",
        "债券占净比": "bond_pct",
        "现金占净比": "cash_pct",
    }
    for item in series:
        field = field_by_name.get(str(item.get("name") or ""))
        values = item.get("data") or []
        if field and len(values) > latest_index:
            result[field] = _rounded(_number(values[latest_index]))
    return result


def _market_trend_snapshot(symbol: str, name: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    values = [
        (row.get("date"), _number(row.get("close")))
        for row in rows
        if row.get("date") is not None and _number(row.get("close")) is not None
    ]
    if len(values) < 20:
        return None
    closes = [value for _, value in values if value is not None]
    latest = closes[-1]
    ma20 = statistics.mean(closes[-20:])
    ma60 = statistics.mean(closes[-60:]) if len(closes) >= 60 else None
    return_20d = (latest / closes[-21] - 1) * 100 if len(closes) >= 21 else None
    return_60d = (latest / closes[-61] - 1) * 100 if len(closes) >= 61 else None
    recent = closes[-60:]
    peak = recent[0]
    max_drawdown = 0.0
    for close in recent:
        peak = max(peak, close)
        max_drawdown = min(max_drawdown, close / peak - 1)
    if ma60 is not None and latest > ma20 > ma60 and (return_20d or 0) > 0:
        trend = "上行"
    elif ma60 is not None and latest < ma20 < ma60 and (return_20d or 0) < 0:
        trend = "下行"
    elif latest > ma20 and (return_20d or 0) > 0:
        trend = "短期走强"
    elif latest < ma20 and (return_20d or 0) < 0:
        trend = "短期走弱"
    else:
        trend = "震荡或方向不明"
    return {
        "symbol": symbol,
        "name": name,
        "as_of": str(values[-1][0]),
        "close": _rounded(latest, 3),
        "trend": trend,
        "return_20d_pct": _rounded(return_20d),
        "return_60d_pct": _rounded(return_60d),
        "price_vs_ma20_pct": _rounded((latest / ma20 - 1) * 100),
        "price_vs_ma60_pct": _rounded((latest / ma60 - 1) * 100) if ma60 else None,
        "max_drawdown_60d_pct": _rounded(max_drawdown * 100),
    }


def _operation_assessment(
    position: dict[str, Any],
    research: dict[str, Any] | None,
) -> dict[str, Any]:
    code = str(position.get("code") or "")
    if research is None or int(research.get("sample_days") or 0) < 120:
        return {
            "code": code,
            "tier": "信息不足",
            "reasons": ["公开净值历史不足 120 个样本，无法运行基金风险政策"],
            "review_triggers": ["补足净值历史与最新定期报告后重新评估"],
            "invalidation_conditions": ["数据补齐后本结论自动失效"],
        }

    performance = research.get("performance_pct") or {}
    profit_pct = _number(position.get("holding_profit_pct"))
    weight_pct = _number(position.get("weight_pct"))
    return_1m = _number(performance.get("1m"))
    return_3m = _number(performance.get("3m"))
    return_6m = _number(performance.get("6m"))
    volatility = _number(research.get("annualized_volatility_pct"))
    holding_trend = research.get("disclosed_holdings_trend") or {}
    holdings_return = _number(holding_trend.get("weighted_return_20d_pct"))
    reasons: list[str] = []

    sell_review = (
        profit_pct is not None
        and profit_pct < 0
        and return_3m is not None
        and return_3m < 0
        and return_6m is not None
        and return_6m < 0
    )
    if sell_review:
        reasons.extend([
            f"用户当前持有收益率为 {profit_pct:.2f}%",
            f"基金近3月收益为 {return_3m:.2f}%，近6月收益为 {return_6m:.2f}%",
        ])
        return {
            "code": code,
            "tier": "进入卖出评估",
            "reasons": reasons,
            "review_triggers": ["刷新数据后若持仓仍亏损且近3月、近6月收益仍同时为负，维持卖出评估"],
            "invalidation_conditions": ["持仓转为非亏损，或近3月、近6月任一区间收益转为非负时重新评估"],
        }

    if weight_pct is not None and weight_pct >= _OPERATION_POLICY["concentration_review_weight_pct"]:
        reasons.append(f"组合权重 {weight_pct:.2f}% 达到政策集中度阈值 35.00%")
    if return_1m is not None and return_1m <= _OPERATION_POLICY["short_term_weakness_pct"]:
        reasons.append(f"近1月收益 {return_1m:.2f}% 低于或等于政策阈值 -10.00%")
    if (
        holdings_return is not None
        and holdings_return <= _OPERATION_POLICY["disclosed_holdings_weakness_pct"]
    ):
        reasons.append(
            f"披露持仓加权20日收益 {holdings_return:.2f}% 低于或等于政策阈值 -8.00%"
        )
    if volatility is not None and volatility >= _OPERATION_POLICY["high_volatility_pct"]:
        reasons.append(f"年化波动率 {volatility:.2f}% 达到政策高波动阈值 50.00%")

    if reasons:
        return {
            "code": code,
            "tier": "降低风险暴露",
            "reasons": reasons,
            "review_triggers": ["任一已命中的风险阈值在后续刷新中继续满足时，维持风险暴露评估"],
            "invalidation_conditions": ["所有已命中的政策阈值均解除后，重新评估为继续持有观察"],
        }
    return {
        "code": code,
        "tier": "继续持有观察",
        "reasons": ["未命中基金风险政策 v1 的集中度、短期走弱、高波动或卖出评估规则"],
        "review_triggers": ["后续刷新若命中任一政策阈值，转入对应风险评估"],
        "invalidation_conditions": ["基金或用户持仓数据发生变化后重新运行政策"],
    }


def calculate_nav_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate trailing returns and risk metrics from dated unit NAV values."""
    normalized: dict[date, float] = {}
    for row in rows:
        try:
            nav_date = date.fromisoformat(str(row.get("date") or "")[:10])
            nav = float(row.get("nav"))
        except (TypeError, ValueError):
            continue
        if nav > 0 and math.isfinite(nav):
            normalized[nav_date] = nav
    values = sorted(normalized.items())
    if len(values) < 2:
        return {
            "performance_pct": {period: None for period in _PERIOD_DAYS},
            "annualized_volatility_pct": None,
            "max_drawdown_1y_pct": None,
            "positive_day_ratio_pct": None,
            "sample_days": len(values),
        }

    latest_date, latest_nav = values[-1]
    performance: dict[str, float | None] = {}
    for period, days in _PERIOD_DAYS.items():
        cutoff = latest_date - timedelta(days=days)
        eligible = [(nav_date, nav) for nav_date, nav in values if nav_date <= cutoff]
        if not eligible or (cutoff - eligible[-1][0]).days > 10:
            performance[period] = None
            continue
        base_nav = eligible[-1][1]
        performance[period] = _rounded((latest_nav / base_nav - 1) * 100)

    one_year = [(nav_date, nav) for nav_date, nav in values if nav_date >= latest_date - timedelta(days=365)]
    daily_returns = [
        one_year[index][1] / one_year[index - 1][1] - 1
        for index in range(1, len(one_year))
        if one_year[index - 1][1] > 0
    ]
    volatility = (
        statistics.stdev(daily_returns) * math.sqrt(250) * 100
        if len(daily_returns) >= 2
        else None
    )
    peak = 0.0
    max_drawdown = 0.0
    for _, nav in one_year:
        peak = max(peak, nav)
        if peak > 0:
            max_drawdown = min(max_drawdown, nav / peak - 1)
    positive_ratio = (
        sum(value > 0 for value in daily_returns) / len(daily_returns) * 100
        if daily_returns
        else None
    )
    return {
        "performance_pct": performance,
        "annualized_volatility_pct": _rounded(volatility),
        "max_drawdown_1y_pct": _rounded(max_drawdown * 100),
        "positive_day_ratio_pct": _rounded(positive_ratio),
        "sample_days": len(values),
    }


class FundMarketResearchProvider(Protocol):
    def research_snapshot(self, code: str) -> dict[str, Any]: ...

    def close(self) -> None: ...


class EastmoneyFundResearchProvider:
    """Public fund metadata and official NAV history, isolated behind an adapter."""

    name = "eastmoney_public_fund_data"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=10.0,
            headers={
                "User-Agent": "Mozilla/5.0 TickFlowFundResearch/1.0",
                "Referer": "https://fund.eastmoney.com/",
            },
        )

    def _base_snapshot(
        self,
        code: str,
        *,
        include_history: bool,
    ) -> dict[str, Any]:
        if len(code) != 6 or not code.isdigit():
            raise ValueError("基金代码必须是 6 位数字")
        search_response = self._client.get(
            "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx",
            params={"m": 1, "key": code},
        )
        search_response.raise_for_status()
        matches = search_response.json().get("Datas") or []
        exact = next((row for row in matches if str(row.get("CODE") or "") == code), None)
        if exact is None:
            raise ValueError(f"没有查询到基金 {code}")
        base = exact.get("FundBaseInfo") or {}

        nav_response = self._client.get(
            f"https://fund.eastmoney.com/pingzhongdata/{code}.js",
            headers={"Referer": f"https://fund.eastmoney.com/{code}.html"},
        )
        nav_response.raise_for_status()
        match = re.search(
            r"var\s+Data_netWorthTrend\s*=\s*(\[[\s\S]*?\]);",
            nav_response.text,
        )
        if not match:
            raise RuntimeError("公开行情源未返回完整净值历史")
        nav_rows = json.loads(match.group(1))
        history = []
        for row in nav_rows:
            timestamp = _number(row.get("x"))
            if timestamp is None:
                continue
            nav_date = datetime.fromtimestamp(
                timestamp / 1000,
                tz=timezone(timedelta(hours=8)),
            ).date()
            history.append({"date": nav_date.isoformat(), "nav": row.get("y")})
        if len(history) < 2:
            raise RuntimeError("公开行情源的净值历史样本不足")
        metrics = calculate_nav_metrics(history)
        managers = [value.strip() for value in str(base.get("JJJL") or "").split(",") if value.strip()]
        result = {
            "code": code,
            "name": str(exact.get("NAME") or "").strip(),
            "fund_type": str(base.get("FTYPE") or exact.get("CATEGORYDESC") or "").strip() or None,
            "company": str(base.get("JJGS") or "").strip() or None,
            "managers": managers,
            "nav_as_of": history[-1]["date"] if history else None,
            "asset_allocation": _parse_asset_allocation(nav_response.text),
            **metrics,
            "source": self.name,
        }
        if include_history:
            result["history"] = history
        return result

    def research_snapshot(self, code: str) -> dict[str, Any]:
        result = self._base_snapshot(code, include_history=False)
        code = str(result["code"])
        disclosure_gap = None
        try:
            holdings_response = self._client.get(
                "https://fundf10.eastmoney.com/FundArchivesDatas.aspx",
                params={"type": "jjcc", "code": code, "topline": 10},
                headers={"Referer": f"https://fundf10.eastmoney.com/ccmx_{code}.html"},
            )
            holdings_response.raise_for_status()
            holdings_as_of, top_holdings = _parse_holdings_archive(holdings_response.text)
        except Exception as exc:
            holdings_as_of, top_holdings = None, []
            disclosure_gap = f"最新定期报告持仓明细暂不可用：{str(exc)[:100]}"
        result.update({
            "top_holdings_as_of": holdings_as_of,
            "top_holdings": top_holdings,
            "top_holdings_total_weight_pct": _rounded(
                sum(item.get("nav_weight_pct") or 0 for item in top_holdings)
            ),
            "holdings_disclosure_note": "仅为最新定期报告披露，不代表当前实时持仓",
            "disclosure_gap": disclosure_gap,
        })
        return result

    def market_snapshot(
        self,
        code: str,
        *,
        include_history: bool = False,
    ) -> dict[str, Any]:
        """轻量快照：基金元数据 + 官方净值历史 + 风险收益指标。

        与 research_snapshot 的区别：不抓取定期报告持仓明细，适合对基金池批量研究。
        """
        return self._base_snapshot(code, include_history=include_history)

    def close(self) -> None:
        self._client.close()


class FundResearchService:
    """Build an auditable AI context from the local ledger and public NAV data."""

    def __init__(
        self,
        portfolio_service,
        *,
        repo=None,
        global_market_service=None,
        market_provider: FundMarketResearchProvider | None = None,
        max_market_positions: int = 8,
    ) -> None:
        self._portfolio_service = portfolio_service
        self._repo = repo
        self._global_market_service = global_market_service
        self._market_provider = market_provider or EastmoneyFundResearchProvider()
        self._max_market_positions = max(1, max_market_positions)

    def _market_context(self, data_gaps: list[str]) -> list[dict[str, Any]]:
        if self._repo is None:
            data_gaps.append("未接入本地大盘指数数据，无法验证市场趋势")
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

    def _global_market_context(self, data_gaps: list[str]) -> dict[str, Any] | None:
        if self._global_market_service is None:
            data_gaps.append("未接入海外市场数据，QDII 的对应市场趋势无法验证")
            return None
        try:
            overview = self._global_market_service.get_overview(force=False)
        except Exception as exc:
            data_gaps.append(f"海外市场趋势暂不可用：{str(exc)[:100]}")
            return None

        def public_row(row: dict[str, Any]) -> dict[str, Any]:
            change = _number(row.get("change_pct"))
            return {
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "last_price": _rounded(_number(row.get("last_price")), 3),
                "day_change_pct": _rounded(change * 100) if change is not None else None,
            }

        return {
            "status": overview.get("status"),
            "as_of": overview.get("beijing_time") or overview.get("market_time"),
            "session": overview.get("session"),
            "benchmarks": [public_row(row) for row in (overview.get("benchmarks") or [])],
            "leading_sectors": [public_row(row) for row in (overview.get("sectors") or [])[:3]],
            "lagging_sectors": [public_row(row) for row in (overview.get("sectors") or [])[-3:]],
            "scope_note": "仅提供最新市场快照，不能替代海外指数的中长期趋势历史",
        }

    def _enrich_disclosed_holdings(
        self,
        market_research: list[dict[str, Any]],
        data_gaps: list[str],
    ) -> None:
        if self._repo is None:
            return
        symbol_meta: dict[str, tuple[str, str]] = {}
        for fund in market_research:
            for holding in fund.get("top_holdings") or []:
                symbol = _a_share_symbol(str(holding.get("security_code") or ""))
                if symbol:
                    symbol_meta[symbol] = (
                        str(holding.get("security_code") or ""),
                        str(holding.get("security_name") or symbol),
                    )
        if not symbol_meta:
            return
        end = date.today()
        start = end - timedelta(days=120)
        try:
            frame = self._repo.get_daily_batch(
                list(symbol_meta),
                start,
                end,
                columns=["symbol", "date", "close"],
            )
        except Exception as exc:
            data_gaps.append(f"披露持仓的本地行情暂不可用：{str(exc)[:100]}")
            return
        rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
        for row in frame.to_dicts():
            rows_by_symbol.setdefault(str(row.get("symbol") or ""), []).append(row)
        signals: dict[str, dict[str, Any]] = {}
        for symbol, (_code, name) in symbol_meta.items():
            snapshot = _market_trend_snapshot(symbol, name, rows_by_symbol.get(symbol, []))
            if snapshot:
                signals[symbol] = snapshot

        for fund in market_research:
            covered_weight = 0.0
            weighted_return = 0.0
            positive_weight = 0.0
            negative_weight = 0.0
            for holding in fund.get("top_holdings") or []:
                symbol = _a_share_symbol(str(holding.get("security_code") or ""))
                signal = signals.get(symbol or "")
                if not signal:
                    continue
                holding["local_market_signal"] = signal
                weight = _number(holding.get("nav_weight_pct")) or 0.0
                return_20d = _number(signal.get("return_20d_pct"))
                covered_weight += weight
                if return_20d is not None:
                    weighted_return += weight * return_20d
                    if return_20d > 0:
                        positive_weight += weight
                    elif return_20d < 0:
                        negative_weight += weight
            fund["disclosed_holdings_trend"] = {
                "coverage_nav_weight_pct": _rounded(covered_weight),
                "weighted_return_20d_pct": _rounded(weighted_return / covered_weight)
                if covered_weight > 0
                else None,
                "positive_20d_nav_weight_pct": _rounded(positive_weight),
                "negative_20d_nav_weight_pct": _rounded(negative_weight),
                "scope_note": "仅覆盖最新定期报告披露且本地行情可识别的 A 股持仓",
            }

    @staticmethod
    def _position_context(
        position: dict[str, Any],
        *,
        total_market_value: float,
        total_holding_profit: float,
    ) -> dict[str, Any]:
        market_value = _number(position.get("market_value")) or 0.0
        holding_profit = _number(position.get("holding_profit"))
        weight = market_value / total_market_value * 100 if total_market_value > 0 else None
        contribution = (
            holding_profit / total_holding_profit * 100
            if holding_profit is not None and total_holding_profit != 0
            else None
        )
        return {
            "code": str(position.get("code") or ""),
            "name": str(position.get("name") or ""),
            "market_value_cny": _rounded(market_value),
            "cost_amount_cny": _rounded(_number(position.get("cost_amount"))),
            "holding_profit_cny": _rounded(holding_profit),
            "holding_profit_pct": _rounded(_number(position.get("holding_profit_pct"))),
            "holding_profit_contribution_pct": _rounded(contribution),
            "day_profit_cny": _rounded(_number(position.get("day_profit"))),
            "day_profit_estimated": bool(position.get("day_profit_estimated")),
            "weight_pct": _rounded(weight),
            "official_nav": _number(position.get("official_nav")),
            "official_nav_date": position.get("official_nav_date"),
            "estimated_nav": _number(position.get("estimated_nav")),
            "estimated_change_pct": _rounded(_number(position.get("estimated_change_pct"))),
            "quote_status": position.get("quote_status"),
        }

    def build_context(self, fund_code: str | None = None) -> dict[str, Any]:
        portfolio = self._portfolio_service.get_portfolio()
        raw_positions = portfolio.get("positions") or []
        if not raw_positions:
            raise ValueError("基金账本为空，请先添加或同步基金持仓")
        summary = portfolio.get("summary") or {}
        total_market = _number(summary.get("total_market_value")) or 0.0
        total_profit = _number(summary.get("total_holding_profit")) or 0.0
        positions = [
            self._position_context(
                position,
                total_market_value=total_market,
                total_holding_profit=total_profit,
            )
            for position in raw_positions
        ]
        positions.sort(key=lambda row: row["market_value_cny"] or 0, reverse=True)
        selected = None
        if fund_code is not None:
            if len(fund_code) != 6 or not fund_code.isdigit():
                raise ValueError("基金代码必须是 6 位数字")
            selected = next((row for row in positions if row["code"] == fund_code), None)
            if selected is None:
                raise ValueError(f"基金 {fund_code} 不在当前基金账本中")

        research_targets = [selected] if selected else positions[: self._max_market_positions]
        market_research: list[dict[str, Any]] = []
        data_gaps: list[str] = []
        for position in research_targets:
            if position is None:
                continue
            try:
                market_research.append(self._market_provider.research_snapshot(position["code"]))
            except Exception as exc:
                data_gaps.append(f"基金 {position['code']} 的公开净值研究数据暂不可用：{str(exc)[:120]}")
        self._enrich_disclosed_holdings(market_research, data_gaps)
        for item in market_research:
            if item.get("disclosure_gap"):
                data_gaps.append(f"基金 {item['code']}：{item['disclosure_gap']}")
        if selected is None and len(positions) > self._max_market_positions:
            data_gaps.append(f"公开净值研究仅补充了市值最大的 {self._max_market_positions} 只基金")
        if any(position.get("official_nav_date") is None for position in positions):
            data_gaps.append("部分持仓缺少正式净值日期")
        official_dates = {
            str(position["official_nav_date"])
            for position in positions
            if position.get("official_nav_date")
        }
        if len(official_dates) > 1:
            data_gaps.append("各持仓正式净值日期不一致，组合单日收益不可直接按同一时点比较")
        market_context = self._market_context(data_gaps)
        global_market_context = self._global_market_context(data_gaps)

        as_of_dates = [
            str(item.get("nav_as_of"))
            for item in market_research
            if item.get("nav_as_of")
        ]
        as_of = max(as_of_dates, default=portfolio.get("quotes_refreshed_at") or portfolio.get("synced_at"))
        research_by_code = {str(item.get("code") or ""): item for item in market_research}
        operation_assessments = [
            _operation_assessment(position, research_by_code.get(position["code"]))
            for position in ([selected] if selected else positions)
            if position is not None
        ]
        if selected is not None:
            return {
                "scope": "fund",
                "fund_code": fund_code,
                "as_of": as_of,
                "currency": "CNY",
                "position": selected,
                "market_research": market_research,
                "market_context": market_context,
                "global_market_context": global_market_context,
                "operation_policy": _OPERATION_POLICY,
                "operation_assessments": operation_assessments,
                "data_gaps": data_gaps,
                "privacy": "不包含支付宝账号、密码、Cookie 或交易指令",
            }

        weights = sorted((row["weight_pct"] or 0 for row in positions), reverse=True)
        hhi = sum((weight / 100) ** 2 for weight in weights)
        analytics = {
            "top1_weight_pct": _rounded(sum(weights[:1])),
            "top3_weight_pct": _rounded(sum(weights[:3])),
            "hhi": _rounded(hhi, 4),
            "profitable_position_count": sum((row["holding_profit_cny"] or 0) > 0 for row in positions),
            "loss_position_count": sum((row["holding_profit_cny"] or 0) < 0 for row in positions),
            "quoted_position_count": sum(bool(row.get("official_nav_date")) for row in positions),
        }
        return {
            "scope": "portfolio",
            "as_of": as_of,
            "currency": "CNY",
            "source": portfolio.get("source"),
            "synced_at": portfolio.get("synced_at"),
            "quotes_refreshed_at": portfolio.get("quotes_refreshed_at"),
            "summary": {
                "position_count": len(positions),
                "total_market_value_cny": _rounded(total_market),
                "total_cost_amount_cny": _rounded(_number(summary.get("total_cost_amount"))),
                "total_holding_profit_cny": _rounded(total_profit),
                "holding_profit_pct": _rounded(_number(summary.get("holding_profit_pct"))),
                "total_day_profit_cny": _rounded(_number(summary.get("total_day_profit"))),
            },
            "analytics": analytics,
            "positions": positions,
            "market_research": market_research,
            "market_context": market_context,
            "global_market_context": global_market_context,
            "operation_policy": _OPERATION_POLICY,
            "operation_assessments": operation_assessments,
            "data_gaps": data_gaps,
            "privacy": "不包含支付宝账号、密码、Cookie 或交易指令",
        }

    def close(self) -> None:
        self._market_provider.close()
