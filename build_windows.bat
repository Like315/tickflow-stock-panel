@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

cd /d "%~dp0"
if errorlevel 1 goto :invalid_directory

echo.
echo ============================================================
echo TickFlow 股票面板 Windows 发布包构建
echo 项目目录：%CD%
echo ============================================================

where uv.exe >nul 2>&1
if errorlevel 1 goto :missing_uv
where node.exe >nul 2>&1
if errorlevel 1 goto :missing_node
where pnpm.cmd >nul 2>&1
if errorlevel 1 goto :missing_pnpm

echo 正在构建，首次运行需要下载依赖，可能耗时数分钟...
uv run --no-project --python 3.12 python "%~dp0packaging\build_windows.py" --clean
if errorlevel 1 goto :build_failed

echo.
echo 构建完成：
echo   %~dp0release-packages\TickFlowStockPanel-windows-x64.zip
echo.
pause
exit /b 0

:invalid_directory
echo [错误] 无法切换到项目目录。
goto :failed

:missing_uv
echo [错误] 未找到 uv。请先安装：https://docs.astral.sh/uv/
goto :failed

:missing_node
echo [错误] 未找到 Node.js。请先安装 Node.js 20 或更高版本。
goto :failed

:missing_pnpm
echo [错误] 未找到 pnpm。请执行：npm install -g pnpm@9
goto :failed

:build_failed
echo [错误] 构建失败，请查看上方日志。

:failed
echo.
pause
exit /b 1
