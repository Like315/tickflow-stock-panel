from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.main import _reject_api_fallback


def test_api_path_is_never_served_by_spa_fallback() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _reject_api_fallback("api/stock-portfolio")

    assert exc_info.value.status_code == 404
    assert "重启" in str(exc_info.value.detail)


def test_frontend_route_can_use_spa_fallback() -> None:
    assert _reject_api_fallback("holdings") is None
