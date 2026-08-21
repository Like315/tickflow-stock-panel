"""验证近期脚本的跨机器与生成产物边界。"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

# 仓库根目录，用于读取不属于后端包的启动脚本与忽略规则。
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "relative_path",
    [
        "start-dev.sh",
        "backend/scripts/bt_ma20_screen.py",
        "backend/scripts/bt_tech_compare.py",
    ],
)
def test_recent_scripts_do_not_embed_machine_paths(relative_path: str) -> None:
    """近期脚本不得依赖开发者机器的盘符或用户目录。"""
    content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    assert "D:\\MyTickFlowStockPanel" not in content
    assert "C:/Users/" not in content
    assert "C:\\Users\\" not in content


def test_start_dev_delegates_to_supported_launcher() -> None:
    """兼容启动入口应复用已维护的跨平台启动器。"""
    content = (REPOSITORY_ROOT / "start-dev.sh").read_text(encoding="utf-8")

    assert 'BACKEND_PORT="${BACKEND_PORT:-3020}"' in content
    assert 'exec "$REPOSITORY_ROOT/dev.sh"' in content


def test_generated_output_directory_is_ignored() -> None:
    """本地生成的研究产物目录不得继续进入 Git。"""
    patterns = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "/output/" in patterns


def test_ruff_allows_chinese_documentation_punctuation() -> None:
    """Ruff 配置应允许中文文档使用正常的全角标点。"""
    config_path = REPOSITORY_ROOT / "backend" / "pyproject.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    allowed = set(config["tool"]["ruff"]["lint"]["allowed-confusables"])

    assert {"，", "：", "（", "）", "；", "？"} <= allowed
