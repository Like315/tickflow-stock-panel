"""构建可解压即用的 Windows 发布包。"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

# 项目根目录, 所有构建输入都从这里解析。
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
# 前端工程目录。
FRONTEND_DIR: Final[Path] = PROJECT_ROOT / "frontend"
# 后端工程目录。
BACKEND_DIR: Final[Path] = PROJECT_ROOT / "backend"
# PyInstaller 应用与进程名称。
APPLICATION_NAME: Final[str] = "TickFlowStockPanel"
# Windows x64 发布包目录和压缩包基础名称。
PACKAGE_NAME: Final[str] = f"{APPLICATION_NAME}-windows-x64"
# PyInstaller onedir 构建输出目录。
PYINSTALLER_OUTPUT: Final[Path] = BACKEND_DIR / "dist" / APPLICATION_NAME
# 本地发布产物目录, 不应提交到 Git。
OUTPUT_ROOT: Final[Path] = PROJECT_ROOT / "release-packages"
# 构建桌面程序所需的后端可选依赖集合。
BACKEND_SYNC_COMMAND: Final[tuple[str, ...]] = (
    "uv.exe",
    "sync",
    "--extra",
    "dev",
    "--extra",
    "desktop",
    "--extra",
    "legacy-cpu",
)


def assert_supported_build_host(
    platform_name: str | None = None,
    machine_name: str | None = None,
) -> None:
    """发布包必须在 64 位 Windows 和稳定版 Python 3.11+ 上原生构建。"""
    current_platform = platform_name or sys.platform
    current_machine = (machine_name or platform.machine()).lower()
    if current_platform != "win32":
        raise RuntimeError("Windows 发布包只能在 Windows 上原生构建")
    if current_machine not in {"amd64", "x86_64"}:
        raise RuntimeError(f"当前仅支持 Windows x64,检测到架构: {current_machine}")
    if sys.version_info < (3, 11) or sys.version_info.releaselevel != "final":
        raise RuntimeError("构建解释器必须是稳定版 Python 3.11+")


def _run_step(label: str, command: Sequence[str], cwd: Path) -> None:
    """执行一个构建步骤并在失败时保留原始异常。"""
    print(f"{label}: {' '.join(command)}")
    subprocess.run(list(command), cwd=cwd, check=True)


def _version() -> str:
    """读取前端包中声明的应用版本。"""
    package_json = json.loads(
        (FRONTEND_DIR / "package.json").read_text(encoding="utf-8")
    )
    return str(package_json["version"])


def _backend_python_version() -> str:
    """读取当前后端虚拟环境实际使用的 Python 版本。"""
    result = subprocess.run(
        [
            "uv.exe",
            "run",
            "--no-sync",
            "python",
            "-c",
            "import platform; print(platform.python_version())",
        ],
        cwd=BACKEND_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _start_launcher_content(executable_path: Path) -> bytes:
    """生成带 CRLF 的 Windows 启动脚本内容。"""
    return (
        "@echo off\r\n"
        "setlocal EnableExtensions DisableDelayedExpansion\r\n"
        "chcp 65001 >nul\r\n"
        'cd /d "%~dp0"\r\n'
        'set "DATA_DIR=%~dp0data"\r\n'
        'if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"\r\n'
        f'start "" "%~dp0{executable_path}" %*\r\n'
    ).encode()


def _close_launcher_content(executable_path: Path) -> bytes:
    """生成只关闭当前发布目录进程的 Windows 脚本内容。"""
    process_query = (
        f"Get-Process -Name '{APPLICATION_NAME}' -ErrorAction SilentlyContinue | "
        "Where-Object { try { $_.Path -and "
        "([System.IO.Path]::GetFullPath($_.Path) -ieq $target) } catch { $false } }"
    )
    command = (
        "$ErrorActionPreference = 'Stop'; try { "
        "$target = [System.IO.Path]::GetFullPath($env:TICKFLOW_TARGET); "
        f"$processes = @({process_query}); "
        "if ($processes.Count -eq 0) { Write-Host '当前发布包的服务未运行。'; exit 0 }; "
        "$processes | ForEach-Object { if ($_.CloseMainWindow()) { "
        "$_.WaitForExit(15000) | Out-Null } }; "
        f"$remaining = @({process_query}); "
        "$remaining | ForEach-Object { Stop-Process -Id $_.Id -Force }; "
        "Write-Host ('已关闭当前发布包的服务进程: ' + "
        "($processes.Id -join ', ')); exit 0 } catch { "
        "Write-Host ('关闭服务失败: ' + $_.Exception.Message); exit 1 }"
    )
    return (
        "@echo off\r\n"
        "setlocal EnableExtensions DisableDelayedExpansion\r\n"
        "chcp 65001 >nul\r\n"
        f'set "TICKFLOW_TARGET=%~dp0{executable_path}"\r\n'
        f'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "{command}"\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    ).encode()


def write_windows_launchers(release_root: Path) -> tuple[Path, Path]:
    """生成最终用户双击使用的 start.bat 与 close.bat。"""
    executable_path = Path("app") / APPLICATION_NAME / f"{APPLICATION_NAME}.exe"
    start_path = release_root / "start.bat"
    close_path = release_root / "close.bat"
    start_path.write_bytes(_start_launcher_content(executable_path))
    close_path.write_bytes(_close_launcher_content(executable_path))
    return start_path, close_path


def _write_release_readme(release_root: Path) -> None:
    """写入面向最终用户的简体中文使用说明。"""
    (release_root / "使用说明.txt").write_text(
        "TickFlow 股票面板 Windows 发布包\n"
        "\n"
        "1. 双击 start.bat 启动服务和桌面窗口。\n"
        "2. 双击 close.bat 关闭由当前目录启动的服务。\n"
        "3. 用户数据保存在本目录的 data 文件夹,请勿随意删除。\n"
        "4. 运行日志保存在 data\\desktop.log。\n"
        "5. 发布包已包含 Python 与前端资源,使用方无需安装 Python、Node.js 或 pnpm。\n",
        encoding="utf-8",
    )


def _remove_existing_output(path: Path, output_root: Path, *, clean: bool) -> None:
    """按 clean 选项安全清理发布目录的直接子项。"""
    if not path.exists():
        return
    if not clean:
        raise FileExistsError(f"发布产物已存在: {path}")
    if path.resolve().parent != output_root.resolve():
        raise ValueError(f"拒绝清理发布目录之外的路径: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _write_release_manifest(release_root: Path, python_version: str | None) -> None:
    """写入发布包版本、平台和运行时清单。"""
    manifest = {
        "package_name": PACKAGE_NAME,
        "target_platform": "windows",
        "architecture": "x64",
        "application_version": _version(),
        "python_version": python_version or platform.python_version(),
        "runtime_mode": "local",
    }
    (release_root / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _populate_release_tree(
    application_root: Path,
    release_root: Path,
    python_version: str | None,
) -> None:
    """复制运行时并补齐启动脚本、说明与清单。"""
    packaged_application = release_root / "app" / APPLICATION_NAME
    packaged_application.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(application_root, packaged_application)
    write_windows_launchers(release_root)
    _write_release_readme(release_root)
    shutil.copy2(PROJECT_ROOT / ".env.example", release_root / ".env.example")
    _write_release_manifest(release_root, python_version)


def assemble_release(
    application_root: Path,
    *,
    output_root: Path = OUTPUT_ROOT,
    clean: bool = False,
    python_version: str | None = None,
) -> Path:
    """把 PyInstaller onedir 产物整理成带启停脚本的 ZIP。"""
    executable = application_root / f"{APPLICATION_NAME}.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"找不到 PyInstaller 可执行文件: {executable}")
    output_root.mkdir(parents=True, exist_ok=True)
    release_root = output_root / PACKAGE_NAME
    archive_path = output_root / f"{PACKAGE_NAME}.zip"
    _remove_existing_output(release_root, output_root, clean=clean)
    _remove_existing_output(archive_path, output_root, clean=clean)
    _populate_release_tree(application_root, release_root, python_version)
    output_root.mkdir(parents=True, exist_ok=True)
    shutil.make_archive(
        str(output_root / PACKAGE_NAME),
        "zip",
        root_dir=output_root,
        base_dir=PACKAGE_NAME,
    )
    return archive_path


def _build_frontend() -> None:
    """安装锁定的前端依赖并生成静态资源。"""
    _run_step(
        "[1/6] 安装前端依赖",
        ["pnpm.cmd", "install", "--frozen-lockfile"],
        FRONTEND_DIR,
    )
    _run_step("[2/6] 构建前端", ["pnpm.cmd", "build"], FRONTEND_DIR)


def _build_backend_application() -> None:
    """同步桌面依赖并生成 PyInstaller onedir 应用。"""
    _run_step("[3/6] 同步后端依赖", BACKEND_SYNC_COMMAND, BACKEND_DIR)
    _run_step(
        "[4/6] 安装 PyInstaller",
        ["uv.exe", "pip", "install", "pyinstaller"],
        BACKEND_DIR,
    )
    _run_step(
        "[5/6] 构建应用",
        [
            "uv.exe",
            "run",
            "pyinstaller",
            "../packaging/tickflow.spec",
            "--noconfirm",
            "--clean",
        ],
        BACKEND_DIR,
    )


def build_windows_release(*, clean: bool) -> Path:
    """校验构建机并编排前端、后端和发布包生成流程。"""
    assert_supported_build_host()
    print(f"开始构建 {PACKAGE_NAME} 发布包。")
    _build_frontend()
    _build_backend_application()
    print("[6/6] 生成 start.bat、close.bat 和 ZIP")
    return assemble_release(
        PYINSTALLER_OUTPUT,
        clean=clean,
        python_version=_backend_python_version(),
    )


def parse_arguments() -> argparse.Namespace:
    """解析 Windows 构建脚本命令行参数。"""
    parser = argparse.ArgumentParser(
        description="构建 TickFlow 股票面板 Windows 发布包"
    )
    parser.add_argument("--clean", action="store_true", help="覆盖已有的同名发布产物")
    return parser.parse_args()


def main() -> int:
    """执行构建并把可预期失败转换为进程退出码。"""
    args = parse_arguments()
    try:
        archive_path = build_windows_release(clean=args.clean)
    except (
        RuntimeError,
        FileNotFoundError,
        FileExistsError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"构建失败: {exc}", file=sys.stderr)
        return 1
    print(f"构建成功: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
