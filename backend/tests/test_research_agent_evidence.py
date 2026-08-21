from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.services.research_agent_evidence import build_stock_evidence


class OfflineAnnouncementProvider:
    def fetch(self, symbol, *, end_date):
        return {
            "available": False,
            "source": "offline-test",
            "search_url": None,
            "announcements": [],
            "news": [],
            "message": f"{symbol}@{end_date.isoformat()}",
        }


class FakeRepo:
    def __init__(self, frame: pl.DataFrame, data_dir: Path) -> None:
        self.frame = frame
        self.store = type("Store", (), {"data_dir": data_dir})()

    def get_enriched_latest(self):
        latest_date = self.frame["date"].max()
        latest = (
            self.frame.filter(pl.col("date") == latest_date)
            if latest_date is not None
            else self.frame
        )
        return latest, latest_date

    def get_enriched_range(self, start, end, symbols=None):
        frame = self.frame.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        if symbols:
            frame = frame.filter(pl.col("symbol").is_in(symbols))
        return frame

    def get_daily(self, symbol, start, end):
        return self.get_enriched_range(start, end, [symbol])

    def get_name_map(self, symbols=None):
        return {"600000.SH": "浦发银行"}


def test_evidence_records_units_signals_and_missing_dimensions(tmp_path) -> None:
    start = date(2026, 5, 1)
    frame = pl.DataFrame(
        {
            "symbol": ["600000.SH"] * 70,
            "date": [start + timedelta(days=i) for i in range(70)],
            "close": [10 + i * 0.01 for i in range(70)],
            "raw_close": [10 + i * 0.01 for i in range(70)],
            "change_pct": [0.01] * 70,
            "ma20": [10.2] * 70,
            "ma60": [9.8] * 70,
            "rsi_14": [56.0] * 70,
            "signal_macd_golden": [False] * 69 + [True],
        }
    )
    evidence = build_stock_evidence(
        FakeRepo(frame, tmp_path),
        "600000.sh",
        announcement_provider=OfflineAnnouncementProvider(),
    )
    assert evidence.name == "浦发银行"
    assert evidence.technical["change_pct"] == 0.01
    assert evidence.technical["change_pct_unit"] == "decimal"
    assert evidence.technical["adjustment"] == "forward"
    assert "signal_macd_golden" in evidence.technical["signals"]
    assert "公告元数据" in evidence.missing_data
    assert "普通新闻" in evidence.missing_data


def test_evidence_adds_local_dimension_strength(tmp_path) -> None:
    concepts = tmp_path / "ext_data" / "ext_gn_ths"
    industries = tmp_path / "ext_data" / "ext_hy_ths"
    concepts.mkdir(parents=True)
    industries.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600000.SH", "000001.SZ"],
            "所属概念": ["银行;金融科技", "银行"],
        }
    ).write_parquet(concepts / "part.parquet")
    pl.DataFrame(
        {
            "symbol": ["600000.SH", "000001.SZ"],
            "所属同花顺行业": ["金融-银行", "金融-银行"],
        }
    ).write_parquet(industries / "part.parquet")
    latest = pl.DataFrame(
        {
            "symbol": ["600000.SH", "000001.SZ"],
            "date": [date(2026, 8, 11)] * 2,
            "close": [10.0, 11.0],
            "raw_close": [10.0, 11.0],
            "change_pct": [0.02, -0.01],
        }
    )
    evidence = build_stock_evidence(
        FakeRepo(latest, tmp_path),
        "600000.SH",
        announcement_provider=OfflineAnnouncementProvider(),
    )
    assert evidence.industry["concepts"] == ["银行", "金融科技"]
    assert evidence.industry["industries"] == ["金融", "银行"]
    bank = evidence.industry["concept_strength"][0]
    assert bank["sample_size"] == 2
    assert bank["mean_change_pct"] == pytest.approx(0.005)
    assert evidence.industry["change_pct_unit"] == "decimal"


def test_evidence_fails_closed_without_history(tmp_path) -> None:
    empty = pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date})
    with pytest.raises(ValueError, match="缺少可用日线"):
        build_stock_evidence(
            FakeRepo(empty, tmp_path),
            "600000.SH",
            date(2026, 8, 11),
            announcement_provider=OfflineAnnouncementProvider(),
        )


def test_historical_evidence_excludes_future_cross_section_and_financials(tmp_path) -> None:
    financial_dir = tmp_path / "financials" / "metrics"
    financial_dir.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600000.SH", "600000.SH"],
            "ann_date": ["2026-08-09", "2026-08-12"],
            "roe": [0.08, 0.99],
        }
    ).write_parquet(financial_dir / "part.parquet")
    frame = pl.DataFrame(
        {
            "symbol": ["600000.SH", "000001.SZ", "600000.SH", "000001.SZ"],
            "date": [date(2026, 8, 10)] * 2 + [date(2026, 8, 12)] * 2,
            "close": [10.0, 11.0, 10.5, 11.5],
            "raw_close": [10.0, 11.0, 10.5, 11.5],
            "change_pct": [-0.02, -0.01, 0.20, 0.30],
        }
    )
    evidence = build_stock_evidence(
        FakeRepo(frame, tmp_path),
        "600000.SH",
        date(2026, 8, 10),
        announcement_provider=OfflineAnnouncementProvider(),
    )
    assert evidence.sentiment["as_of"] == "2026-08-10"
    assert evidence.sentiment["median_change_pct"] == pytest.approx(-0.015)
    assert evidence.fundamental["tables"]["metrics"]["roe"] == 0.08
