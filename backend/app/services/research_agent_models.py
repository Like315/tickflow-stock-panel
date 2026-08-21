"""AI 研究 Agent 的稳定数据契约。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

Stance = Literal["偏买入", "观察", "偏卖出", "回避"]
ThesisState = Literal["增强", "维持", "减弱", "失效"]
EvidenceDimension = Literal["技术面", "情绪面", "行业面", "基本面", "信息面"]


class ResearchTerm(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    definition: str
    interpretation: str
    limitation: str
    combine_with: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    dimension: EvidenceDimension
    conclusion: str
    supports: list[str] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    source: str
    evidence_refs: list[str] = Field(min_length=1)
    source_url: str | None = None
    as_of: date

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in {
            "www.cninfo.com.cn",
            "static.cninfo.com.cn",
        }:
            raise ValueError("证据链接必须来自允许的巨潮资讯 HTTPS 域名")
        return value


class RecommendationPick(BaseModel):
    symbol: str
    name: str
    stance: Stance
    confidence: int = Field(ge=0, le=100)
    thesis: str
    horizon_days: str = "5-20个交易日"
    evidence: list[EvidenceItem] = Field(min_length=1)
    counter_evidence: list[str] = Field(min_length=1)
    catalysts: list[str] = Field(default_factory=list)
    risks: list[str] = Field(min_length=1)
    watch_zone: str | None = None
    risk_level: str | None = None
    invalidation_conditions: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class RecommendationBatch(BaseModel):
    id: str | None = None
    as_of: date
    created_at: datetime | None = None
    trigger: Literal["automatic", "manual"] = "manual"
    version: int = Field(default=1, ge=1)
    parent_batch_id: str | None = None
    model: str = ""
    prompt_version: str = "research-agent-v1"
    screening_version: str = "conservative-v1"
    market_snapshot: dict[str, Any] = Field(default_factory=dict)
    screen_summary: dict[str, Any] = Field(default_factory=dict)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    picks: list[RecommendationPick] = Field(default_factory=list, max_length=5)
    status: Literal["official", "degraded"] = "official"
    message: str | None = None


class DailyReview(BaseModel):
    batch_id: str
    symbol: str
    trade_date: date
    holding_day: int = Field(ge=1, le=20)
    daily_return: float | None = None
    cumulative_return: float | None = None
    max_gain: float | None = None
    max_drawdown: float | None = None
    benchmark_return: float | None = None
    relative_return: float | None = None
    thesis_state: ThesisState = "维持"
    support_changes: list[str] = Field(default_factory=list)
    counter_changes: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    reflection: str = ""
    analysis_status: Literal["succeeded", "degraded", "backfill"] = "degraded"
    is_backfill: bool = False
    created_at: datetime | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class StageReview(BaseModel):
    batch_id: str
    symbol: str
    stage_day: Literal[5, 10, 20]
    trade_date: date
    summary: str
    thesis_state: ThesisState
    created_at: datetime | None = None


class CandidateScreenResult(BaseModel):
    as_of: date | None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    eligible_count: int = 0
    excluded: dict[str, int] = Field(default_factory=dict)
    message: str | None = None


class StockEvidence(BaseModel):
    symbol: str
    name: str
    as_of: date
    snapshot: dict[str, Any]
    technical: dict[str, Any]
    sentiment: dict[str, Any]
    industry: dict[str, Any]
    fundamental: dict[str, Any]
    information: dict[str, Any]
    missing_data: list[str] = Field(default_factory=list)
