"""龙头板块分析 service 测试 — 评分口径、每日龙头、区间冠军、行业层级、降级路径。"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.leading_sectors import router as leading_sectors_router
from app.services import leading_sector, rps_rotation
from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField


class _Repo:
    """最小化 fake repo: 只提供 leading_sector 依赖的 store / enriched 历史缓存。"""

    def __init__(self, data_dir, cache_df: pl.DataFrame | None):
        self.store = SimpleNamespace(data_dir=data_dir)
        self._enriched_history_cache = cache_df

    def get_enriched_range(self, start, end, symbols=None, columns=None):
        cache = self._enriched_history_cache
        if cache is None or cache.is_empty() or "date" not in cache.columns:
            return None
        df = cache.filter(
            (pl.col("date") >= start) & (pl.col("date") <= end)
        )
        if symbols is not None:
            df = df.filter(pl.col("symbol").is_in(symbols))
        if columns and not df.is_empty():
            existing = [c for c in columns if c in df.columns]
            if "symbol" not in existing:
                existing.insert(0, "symbol")
            if "date" not in existing:
                existing.insert(1, "date")
            df = df.select(existing)
        return df.sort(["symbol", "date"])


def _write_concept_ext(tmp_path, rows: list[tuple[str, str]]) -> None:
    """写入一张概念成分股扩展 parquet (symbol, concept)。"""
    config = ExtConfig(
        id="concept_test",
        label="概念测试",
        mode="snapshot",
        fields=[
            ExtField("symbol", "string", "标的代码"),
            ExtField("concept", "string", "所属概念"),
        ],
    )
    ExtConfigStore(tmp_path).upsert(config)
    ext_dir = tmp_path / "ext_data" / config.id
    pl.DataFrame({"symbol": [r[0] for r in rows], "concept": [r[1] for r in rows]}).write_parquet(
        ext_dir / "part.parquet"
    )


def _write_industry_ext(tmp_path, rows: list[tuple[str, str]]) -> None:
    """写入一张行业成分股扩展 parquet (symbol, industry 全路径名)。"""
    config = ExtConfig(
        id="industry_test",
        label="行业测试",
        mode="snapshot",
        fields=[
            ExtField("symbol", "string", "标的代码"),
            ExtField("industry", "string", "所属行业"),
        ],
    )
    ExtConfigStore(tmp_path).upsert(config)
    ext_dir = tmp_path / "ext_data" / config.id
    pl.DataFrame({"symbol": [r[0] for r in rows], "industry": [r[1] for r in rows]}).write_parquet(
        ext_dir / "part.parquet"
    )


def _enriched_cache() -> pl.DataFrame:
    """3 个交易日 x 3 概念的 enriched 历史缓存 (日期用 date 类型)。"""
    d1, d2, d3 = date(2026, 6, 26), date(2026, 6, 29), date(2026, 6, 30)
    # 概念: AI 强势三连, 半导体中性, 地产弱势
    rows = [
        # (symbol, name, date, change_pct, amount, consecutive_limit_ups, signal_limit_up, concept)
        ("600001.SH", "AI甲", d1, 0.10, 1_000_000_000.0, 2, True, "人工智能"),
        ("600002.SH", "AI乙", d1, 0.06, 800_000_000.0, 1, True, "人工智能"),
        ("600003.SH", "AI丙", d1, 0.04, 500_000_000.0, 0, False, "人工智能"),
        ("600011.SH", "半甲", d1, 0.02, 300_000_000.0, 0, False, "半导体"),
        ("600012.SH", "半乙", d1, -0.01, 200_000_000.0, 0, False, "半导体"),
        ("600021.SH", "地甲", d1, -0.03, 100_000_000.0, 0, False, "房地产"),
        ("600022.SH", "地乙", d1, -0.05, 80_000_000.0, 0, False, "房地产"),
        ("600001.SH", "AI甲", d2, 0.09, 1_200_000_000.0, 3, True, "人工智能"),
        ("600002.SH", "AI乙", d2, 0.07, 900_000_000.0, 2, True, "人工智能"),
        ("600003.SH", "AI丙", d2, 0.03, 400_000_000.0, 0, False, "人工智能"),
        ("600011.SH", "半甲", d2, 0.01, 350_000_000.0, 0, False, "半导体"),
        ("600012.SH", "半乙", d2, -0.02, 150_000_000.0, 0, False, "半导体"),
        ("600021.SH", "地甲", d2, -0.04, 120_000_000.0, 0, False, "房地产"),
        ("600022.SH", "地乙", d2, -0.06, 90_000_000.0, 0, False, "房地产"),
        ("600001.SH", "AI甲", d3, 0.08, 1_100_000_000.0, 4, True, "人工智能"),
        ("600002.SH", "AI乙", d3, 0.05, 850_000_000.0, 3, True, "人工智能"),
        ("600003.SH", "AI丙", d3, 0.02, 450_000_000.0, 0, False, "人工智能"),
        ("600011.SH", "半甲", d3, 0.00, 320_000_000.0, 0, False, "半导体"),
        ("600012.SH", "半乙", d3, -0.01, 210_000_000.0, 0, False, "半导体"),
        ("600021.SH", "地甲", d3, -0.05, 110_000_000.0, 0, False, "房地产"),
        ("600022.SH", "地乙", d3, -0.07, 95_000_000.0, 0, False, "房地产"),
    ]
    return pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "name": [r[1] for r in rows],
            "date": [r[2] for r in rows],
            "change_pct": [r[3] for r in rows],
            "amount": [r[4] for r in rows],
            "consecutive_limit_ups": [r[5] for r in rows],
            "signal_limit_up": [r[6] for r in rows],
            "concept": [r[7] for r in rows],
        }
    )


@pytest.fixture(autouse=True)
def _clear_caches():
    """每个用例前清空进程级缓存, 避免跨用例串数据。"""
    leading_sector.invalidate_cache()
    rps_rotation._map_cache.clear()
    rps_rotation._map_ts.clear()
    yield
    leading_sector.invalidate_cache()
    rps_rotation._map_cache.clear()
    rps_rotation._map_ts.clear()


def test_empty_cache_returns_empty(tmp_path):
    repo = _Repo(tmp_path, None)
    result = leading_sector.build_leading_sectors(repo, days=12, kind="concept")
    assert result["sectors"] == []
    assert result["sector_count"] == 0
    assert result["as_of"] is None


def test_no_dimension_data_returns_empty(tmp_path):
    # 无扩展数据: 只有 enriched 缓存, 没有概念映射
    repo = _Repo(tmp_path, _enriched_cache())
    result = leading_sector.build_leading_sectors(repo, days=12, kind="concept")
    assert result["sectors"] == []
    assert result["sector_count"] == 0


def test_basic_ranking_and_factors(tmp_path):
    _write_concept_ext(
        tmp_path,
        [
            ("600001.SH", "人工智能"), ("600002.SH", "人工智能"), ("600003.SH", "人工智能"),
            ("600011.SH", "半导体"), ("600012.SH", "半导体"),
            ("600021.SH", "房地产"), ("600022.SH", "房地产"),
        ],
    )
    repo = _Repo(tmp_path, _enriched_cache())
    result = leading_sector.build_leading_sectors(repo, days=12, kind="concept", top=10)

    assert result["days"] == 3
    assert result["as_of"] == "2026-06-30"
    assert result["sector_count"] == 3

    names = [s["name"] for s in result["sectors"]]
    assert names[0] == "人工智能"      # 强势主线排第一
    assert names[1] == "半导体"
    assert names[2] == "房地产"

    ai = result["sectors"][0]
    # 三因子齐全
    assert set(ai["parts"].keys()) == {"persistence", "capital", "leader"}
    # 人工智能 3 天全进前 10 (只有 3 个概念), 持续性满格
    assert ai["top10_days"] == 3
    assert ai["parts"]["persistence"] == pytest.approx(100.0, abs=0.1)
    # 资金强度: 人工智能金额最大 → 100
    assert ai["parts"]["capital"] == pytest.approx(100.0, abs=0.1)
    # 龙头股强度: 冠军连续 4 板、3 天领涨、累计涨幅 ~29% → 接近满分
    assert ai["parts"]["leader"] > 75.0
    assert ai["score"] > 90.0

    # 房地产应垫底: 平均排名最差, 综合分最低 (仅 3 个概念时都在前 10, 持续性主要靠平均排名区分)
    estate = result["sectors"][2]
    assert estate["avg_rank"] > ai["avg_rank"]
    assert estate["parts"]["persistence"] < ai["parts"]["persistence"]
    assert estate["score"] < ai["score"]


def test_daily_leaders_and_champion(tmp_path):
    _write_concept_ext(
        tmp_path,
        [
            ("600001.SH", "人工智能"), ("600002.SH", "人工智能"), ("600003.SH", "人工智能"),
        ],
    )
    repo = _Repo(tmp_path, _enriched_cache())
    result = leading_sector.build_leading_sectors(repo, days=12, kind="concept", top=10)

    ai = result["sectors"][0]
    # 每日龙头: 3 天都是 AI甲 (涨幅最大), 最新在前
    assert len(ai["daily_leaders"]) == 3
    assert ai["daily_leaders"][0]["date"] == "2026-06-30"
    assert ai["daily_leaders"][0]["symbol"] == "600001.SH"
    assert ai["daily_leaders"][0]["is_limit_up"] is True
    assert ai["daily_leaders"][2]["date"] == "2026-06-26"

    # 区间冠军: AI甲 领涨 3 天
    champion = ai["champion"]
    assert champion["symbol"] == "600001.SH"
    assert champion["lead_days"] == 3
    # 累计涨幅 = (1.10 * 1.09 * 1.08) - 1 ≈ 0.295
    assert champion["cum_pct"] == pytest.approx(1.10 * 1.09 * 1.08 - 1, abs=0.0005)
    assert champion["max_boards"] == 4
    assert champion["name"] == "AI甲"


def test_top_slices_result(tmp_path):
    _write_concept_ext(
        tmp_path,
        [
            ("600001.SH", "人工智能"), ("600011.SH", "半导体"), ("600021.SH", "房地产"),
        ],
    )
    repo = _Repo(tmp_path, _enriched_cache())
    result = leading_sector.build_leading_sectors(repo, days=12, kind="concept", top=1)
    assert len(result["sectors"]) == 1
    assert result["sectors"][0]["name"] == "人工智能"


def test_cache_hit_returns_same_data(tmp_path):
    _write_concept_ext(
        tmp_path,
        [("600001.SH", "人工智能"), ("600011.SH", "半导体"), ("600021.SH", "房地产")],
    )
    repo = _Repo(tmp_path, _enriched_cache())
    first = leading_sector.build_leading_sectors(repo, days=12, kind="concept", top=10)
    second = leading_sector.build_leading_sectors(repo, days=12, kind="concept", top=10)
    assert first == second
    # 不同 top 从同一缓存截断
    sliced = leading_sector.build_leading_sectors(repo, days=12, kind="concept", top=1)
    assert len(sliced["sectors"]) == 1
    assert sliced["sectors"][0]["name"] == first["sectors"][0]["name"]


def test_industry_level_aggregation(tmp_path):
    # 行业全路径名: 两个三级行业在 level=2 归并为一个二级行业「银行」
    _write_industry_ext(
        tmp_path,
        [
            ("600031.SH", "银行-银行-股份制银行"),
            ("600032.SH", "银行-银行-城商行"),
        ],
    )
    d1 = date(2026, 6, 26)
    cache = pl.DataFrame({
        "symbol": ["600031.SH", "600032.SH"],
        "name": ["股份行", "城商行"],
        "date": [d1, d1],
        "change_pct": [0.05, 0.03],
        "amount": [1_000_000_000.0, 500_000_000.0],
        "consecutive_limit_ups": [0, 0],
        "signal_limit_up": [False, False],
        "industry": ["银行-银行-股份制银行", "银行-银行-城商行"],
    })
    repo = _Repo(tmp_path, cache)
    raw = leading_sector.build_leading_sectors(repo, days=12, kind="industry", level=None, top=10)
    assert raw["sector_count"] == 2  # 未分级: 两个全路径名

    leveled = leading_sector.build_leading_sectors(repo, days=12, kind="industry", level=2, top=10)
    assert leveled["sector_count"] == 2  # 去重成员总数 (映射表原始成员)
    assert len(leveled["sectors"]) == 1
    assert leveled["sectors"][0]["name"] == "银行"
    assert leveled["sectors"][0]["count"] == 2


def test_degraded_cache_without_optional_columns(tmp_path):
    """降级: 无 name/amount/signal_limit_up/consecutive_limit_ups 仍能出结果 (fail-safe)。"""
    _write_concept_ext(tmp_path, [("600001.SH", "人工智能"), ("600011.SH", "半导体")])
    d1 = date(2026, 6, 26)
    cache = pl.DataFrame({
        "symbol": ["600001.SH", "600011.SH"],
        "date": [d1, d1],
        "change_pct": [0.08, 0.01],
    })
    repo = _Repo(tmp_path, cache)
    result = leading_sector.build_leading_sectors(repo, days=12, kind="concept", top=10)
    assert len(result["sectors"]) == 2
    ai = result["sectors"][0]
    assert ai["name"] == "人工智能"
    # 无 amount → capital 0; 无连板/信号 → leader 仅靠领涨天数
    assert ai["parts"]["capital"] == 0.0
    assert ai["champion"]["max_boards"] == 0
    assert ai["daily_leaders"][0]["is_limit_up"] is False
    assert ai["daily_leaders"][0]["name"] == "600001.SH"  # 无 name 列退化为 symbol


# ================================================================
# API 契约测试
# ================================================================

def _api_client(tmp_path) -> TestClient:
    _write_concept_ext(
        tmp_path,
        [
            ("600001.SH", "人工智能"), ("600002.SH", "人工智能"), ("600003.SH", "人工智能"),
            ("600011.SH", "半导体"), ("600012.SH", "半导体"),
            ("600021.SH", "房地产"), ("600022.SH", "房地产"),
        ],
    )
    app = FastAPI()
    app.state.repo = _Repo(tmp_path, _enriched_cache())
    app.include_router(leading_sectors_router)
    return TestClient(app)


def test_api_returns_ranking_contract(tmp_path):
    client = _api_client(tmp_path)
    resp = client.get("/api/leading-sectors?days=12&kind=concept&top=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "concept"
    assert data["days"] == 3
    assert data["as_of"] == "2026-06-30"
    assert len(data["sectors"]) == 3

    top = data["sectors"][0]
    assert set(top.keys()) >= {
        "name", "count", "score", "parts", "avg_pct", "total_amount",
        "avg_rank", "top10_days", "champion", "daily_leaders",
    }
    assert set(top["parts"].keys()) == {"persistence", "capital", "leader"}
    assert top["champion"]["symbol"] == "600001.SH"
    assert len(top["daily_leaders"]) == 3
    # 响应可 JSON 序列化 (fastapi 已隐式校验), 且龙头分有界
    assert 0 <= top["score"] <= 100


def test_api_validates_dimension_and_level(tmp_path):
    client = _api_client(tmp_path)
    # 非法 kind → 422 (fastapi Query pattern 校验)
    resp = client.get("/api/leading-sectors?days=12&kind=banana")
    assert resp.status_code == 422
    # 非法 days 越界 → 422
    resp = client.get("/api/leading-sectors?days=999")
    assert resp.status_code == 422
    # level 仅 industry 生效: concept + level 会被忽略 (不报错, 正常返回)
    resp = client.get("/api/leading-sectors?days=12&kind=concept&level=2")
    assert resp.status_code == 200


def test_trade_plan_weekly_trend_and_risk_levels():
    dates = pl.date_range(date(2025, 10, 1), date(2026, 8, 14), interval="1d", eager=True)
    dates = dates.filter(dates.dt.weekday() <= 5)
    closes = [10.0 + i * 0.1 for i in range(len(dates))]
    history = pl.DataFrame({
        "symbol": ["600001.SH"] * len(dates),
        "date": dates,
        "close": closes,
    })

    plan = leading_sector._trade_plan(history, "600001.SH")

    assert plan is not None
    assert plan["weekly_trend"] is True
    assert plan["monthly_trend"] is True
    assert plan["above_ma5"] is True
    assert plan["eligible"] is True
    assert plan["drawdown_stop_price"] == pytest.approx(closes[-1] * 0.9, abs=0.001)
    assert plan["exit_ma5"] is False


def test_trade_plan_requires_sufficient_history():
    history = pl.DataFrame({
        "symbol": ["600001.SH"] * 20,
        "date": pl.date_range(date(2026, 1, 5), date(2026, 1, 24), interval="1d", eager=True),
        "close": [10.0] * 20,
    })
    assert leading_sector._trade_plan(history, "600001.SH") is None
