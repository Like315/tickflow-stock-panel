"""龙头板块分析 API — 板块龙头评分 + 历史龙头股拆解。

供「龙头板块」页面调用。返回最近 N 个交易日的龙头板块排行,
每个板块附三因子评分拆解 (排名持续性/资金强度/龙头股强度)、
区间冠军股与每日龙头清单。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.services import leading_sector

router = APIRouter(prefix="/api/leading-sectors", tags=["leading-sectors"])


@router.get("")
def get_leading_sectors(
    request: Request,
    days: int = Query(12, ge=7, le=30, description="最近 N 个交易日(7-30)"),
    kind: str = Query(
        "concept", pattern="concept|industry", description="维度: concept 概念 / industry 行业"
    ),
    level: int | None = Query(None, ge=1, le=3, description="行业层级(仅 kind=industry): 1/2/3 级"),
    top: int = Query(30, ge=1, le=100, description="返回前 N 个龙头板块"),
) -> dict:
    """龙头板块排行 + 历史龙头股拆解。

    Returns:
        {
          as_of: 最新交易日
          kind: 维度
          days: 实际窗口交易日数
          sector_count: 去重维度成员总数
          sectors: 龙头板块列表 (按龙头分降序, 前 top 个), 每项含:
            name / count / score / parts{persistence,capital,leader} /
            avg_pct / total_amount / avg_rank / top10_days /
            champion{...} / daily_leaders[{date,symbol,name,change_pct,
            rank_in_sector,is_limit_up}]
        }
    """
    repo = request.app.state.repo
    kind = "industry" if kind == "industry" else "concept"
    level = level if (kind == "industry" and level in (1, 2, 3)) else None
    return leading_sector.build_leading_sectors(repo, days, kind, level, top)
