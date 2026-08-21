from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.research_agent_models import RecommendationPick
from app.services.research_agent_terms import find_term, list_terms, term_to_markdown


def test_find_term_supports_alias_and_sentence() -> None:
    assert find_term("MACD").id == "macd"
    assert find_term("MACD金叉") is not None
    assert find_term("请解释一下放量上涨意味着什么").id == "volume_surge"
    assert find_term("完全未知的术语") is None
    assert len(list_terms()) >= 10


def test_term_answer_contains_limits() -> None:
    text = term_to_markdown(find_term("金叉"))
    assert "不能单独说明什么" in text
    assert "不能单独等同于买入" in text


def test_recommendation_requires_counter_evidence_and_valid_confidence() -> None:
    base = {
        "symbol": " 600000.sh ",
        "name": "浦发银行",
        "stance": "观察",
        "confidence": 60,
        "thesis": "趋势改善但仍需确认",
        "evidence": [
            {
                "dimension": "技术面",
                "conclusion": "均线改善",
                "supports": ["收盘价位于 MA20 上方"],
                "source": "TickFlow enriched",
                "evidence_refs": ["technical.ma20"],
                "as_of": "2026-08-11",
            }
        ],
        "counter_evidence": ["成交量不足"],
        "risks": ["市场转弱"],
    }
    item = RecommendationPick.model_validate(base)
    assert item.symbol == "600000.SH"

    with pytest.raises(ValidationError):
        RecommendationPick.model_validate({**base, "counter_evidence": []})
    with pytest.raises(ValidationError):
        RecommendationPick.model_validate({**base, "confidence": 101})
