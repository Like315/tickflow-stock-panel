from __future__ import annotations

import polars as pl
import pytest
from fastapi import HTTPException

from app.api.backtest import _resolve_sector_symbols
from app.services import rps_rotation


@pytest.fixture
def concept_mapping(monkeypatch):
    mapping = pl.DataFrame({
        "_sym_up": ["600001.SH", "600002.SH", "600003.SH"],
        "concept": ["人工智能", "人工智能", "机器人"],
    })
    monkeypatch.setattr(rps_rotation, "_load_concept_map_df", lambda repo, kind: (mapping, 2))


def test_sector_filter_returns_members(concept_mapping):
    result = _resolve_sector_symbols(object(), None, "concept", "人工智能")
    assert result == ["600001.SH", "600002.SH"]


def test_sector_filter_intersects_explicit_pool(concept_mapping):
    result = _resolve_sector_symbols(
        object(), ["600002.sh", "600003.SH"], "concept", "人工智能"
    )
    assert result == ["600002.SH"]


def test_sector_filter_rejects_empty_intersection(concept_mapping):
    with pytest.raises(HTTPException, match="没有交集"):
        _resolve_sector_symbols(
            object(), ["600003.SH"], "concept", "人工智能"
        )


def test_empty_sector_preserves_pool():
    pool = ["600001.SH"]
    assert _resolve_sector_symbols(object(), pool, None, None) is pool
