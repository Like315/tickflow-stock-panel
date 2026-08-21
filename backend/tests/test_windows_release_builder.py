from __future__ import annotations

import importlib.util
import json
import re
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_builder() -> ModuleType:
    """从仓库脚本路径加载 Windows 构建模块。"""
    script_path = PROJECT_ROOT / "packaging" / "build_windows.py"
    spec = importlib.util.spec_from_file_location("tickflow_windows_builder", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_launchers_start_and_close_only_current_release(tmp_path: Path) -> None:
    """启停脚本应使用 CRLF 且只匹配当前目录中的进程。"""
    builder = _load_builder()

    start_path, close_path = builder.write_windows_launchers(tmp_path)

    start_content = start_path.read_bytes()
    close_content = close_path.read_bytes()
    assert b"app\\TickFlowStockPanel\\TickFlowStockPanel.exe" in start_content
    assert b'set "DATA_DIR=%~dp0data"' in start_content
    assert b"CloseMainWindow" in close_content
    assert b"Stop-Process" in close_content
    assert b"[System.IO.Path]::GetFullPath($_.Path) -ieq $target" in close_content
    assert not re.search(rb"(?<!\r)\n", start_content)
    assert not re.search(rb"(?<!\r)\n", close_content)


def test_assemble_release_copies_runtime_and_creates_zip(tmp_path: Path) -> None:
    """发布组装应复制完整运行时并生成带清单的 ZIP。"""
    builder = _load_builder()
    application_root = tmp_path / "dist" / builder.APPLICATION_NAME
    internal = application_root / "_internal"
    internal.mkdir(parents=True)
    (application_root / f"{builder.APPLICATION_NAME}.exe").write_bytes(b"exe")
    (internal / "runtime.pyd").write_bytes(b"runtime")
    output_root = tmp_path / "release-packages"

    archive_path = builder.assemble_release(
        application_root,
        output_root=output_root,
        clean=True,
        python_version="3.12.99",
    )

    release_root = output_root / builder.PACKAGE_NAME
    assert (release_root / "start.bat").is_file()
    assert (release_root / "close.bat").is_file()
    assert (release_root / "app" / builder.APPLICATION_NAME / "_internal" / "runtime.pyd").is_file()
    manifest = json.loads((release_root / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["target_platform"] == "windows"
    assert manifest["architecture"] == "x64"
    assert manifest["python_version"] == "3.12.99"
    assert archive_path.is_file()
    with zipfile.ZipFile(archive_path) as archive:
        assert f"{builder.PACKAGE_NAME}/start.bat" in archive.namelist()
        assert f"{builder.PACKAGE_NAME}/close.bat" in archive.namelist()


def test_assemble_release_rejects_missing_executable(tmp_path: Path) -> None:
    """缺少 PyInstaller 主程序时应在创建输出前失败。"""
    builder = _load_builder()

    with pytest.raises(FileNotFoundError, match="PyInstaller"):
        builder.assemble_release(tmp_path, output_root=tmp_path / "output", clean=True)


def test_windows_builder_rejects_non_windows_host() -> None:
    """Windows 发布脚本应拒绝非 Windows 构建机。"""
    builder = _load_builder()

    with pytest.raises(RuntimeError, match="只能在 Windows"):
        builder.assert_supported_build_host("linux", "x86_64")


def test_windows_builder_preserves_development_dependencies() -> None:
    """后端同步应保留项目约定的开发与桌面依赖组。"""
    builder = _load_builder()

    extras = [
        builder.BACKEND_SYNC_COMMAND[index + 1]
        for index, argument in enumerate(builder.BACKEND_SYNC_COMMAND)
        if argument == "--extra"
    ]
    assert extras == ["dev", "desktop", "legacy-cpu"]
