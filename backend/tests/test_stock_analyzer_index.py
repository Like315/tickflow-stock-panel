"""AI 个股分析的财务数据与 prompt 测试。"""

from __future__ import annotations

import json
from datetime import date

import polars as pl

from app.services import stock_analyzer
from app.services.stock_analyzer import _build_user_prompt


def test_user_prompt_index_no_financials():
    prompt = _build_user_prompt(
        kline_tail=[{"date": "2026-07-24", "close": 3000.0}],
        fins={"metrics": [], "income": []},
        levels={},
        close=3000.0,
        symbol="000001.SH",
        focus="",
        asset_type="index",
    )
    assert "指数" in prompt
    assert "Free 模式" not in prompt  # 指数无财务是常态, 不走 Free 文案


def test_load_financials_includes_four_statements_and_latest_four_periods(
    tmp_path,
    monkeypatch,
):
    periods = [
        date(2024, 3, 31),
        date(2024, 6, 30),
        date(2024, 9, 30),
        date(2024, 12, 31),
        date(2025, 3, 31),
    ]
    frames = {
        "metrics": pl.DataFrame(
            {
                "symbol": ["600000.SH"] * 5,
                "period_end": periods,
                "roe": [8.0, 8.5, 9.0, 9.5, 10.0],
            }
        ),
        "income": pl.DataFrame(
            {
                "symbol": ["600000.SH"] * 5,
                "period_end": periods,
                "net_income": [1.0, 2.0, 3.0, 4.0, 5.0],
            }
        ),
        "balance_sheet": pl.DataFrame(
            {
                "symbol": ["600000.SH"] * 5,
                "period_end": periods,
                "total_liabilities": [10.0, 11.0, 12.0, 13.0, 14.0],
            }
        ),
        "cash_flow": pl.DataFrame(
            {
                "symbol": ["600000.SH"] * 5,
                "period_end": periods,
                "net_operating_cash_flow": [1.0, 2.0, float("nan"), 4.0, 5.0],
            }
        ),
    }
    monkeypatch.setattr(
        stock_analyzer,
        "get_financial_df",
        lambda _data_dir, table: frames[table],
    )

    result = stock_analyzer._load_financials(tmp_path, "600000.SH")

    assert set(result) == {"metrics", "income", "balance_sheet", "cash_flow"}
    assert all(len(rows) == 4 for rows in result.values())
    assert result["metrics"][0]["period_end"] == "2025-03-31"
    assert result["cash_flow"][2]["net_operating_cash_flow"] is None
    json.dumps(result, ensure_ascii=False)  # 日期与 NaN 必须可安全注入 prompt


def test_load_financials_returns_explicit_empty_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(
        stock_analyzer,
        "get_financial_df",
        lambda _data_dir, _table: pl.DataFrame(),
    )

    assert stock_analyzer._load_financials(tmp_path, "600000.SH") == {
        "metrics": [],
        "income": [],
        "balance_sheet": [],
        "cash_flow": [],
    }


def test_user_prompt_describes_partial_financial_data():
    prompt = _build_user_prompt(
        kline_tail=[{"date": "2026-07-24", "close": 10.0}],
        fins={
            "metrics": [{"period_end": "2025-12-31", "roe": 12.3}],
            "income": [],
            "balance_sheet": [],
            "cash_flow": [
                {
                    "period_end": "2025-12-31",
                    "net_operating_cash_flow": 100.0,
                }
            ],
        },
        levels={},
        close=10.0,
        symbol="600000.SH",
        focus="",
    )

    assert "比率类指标为百分点" in prompt
    assert "income: 无数据" in prompt
    assert "balance_sheet: 无数据" in prompt
    assert "cash_flow: 1期" in prompt


def test_user_prompt_stock_without_financials_points_to_sync():
    prompt = _build_user_prompt(
        kline_tail=[{"date": "2026-07-24", "close": 10.0}],
        fins={
            "metrics": [],
            "income": [],
            "balance_sheet": [],
            "cash_flow": [],
        },
        levels={},
        close=10.0,
        symbol="600000.SH",
        focus="",
    )

    assert "财务分析" in prompt
    assert "同步" in prompt
    assert "Free 模式" not in prompt
