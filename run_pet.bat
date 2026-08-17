@echo off
rem Veranima 桌宠 launcher（Electron 壳 + 核心 + TTS 三进程）
rem 双击启动：.venv\Scripts\python scripts/run_pet.py（壳自动 spawn 核心 + TTS）
cd /d "%~dp0"

rem Hermes-style PYTHONPATH pollution guard (harmless otherwise)
set PYTHONPATH=

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -e .
    pause
    exit /b 1
)

if not exist "pet\node_modules\electron\cli.js" (
    echo [ERROR] pet\node_modules 缺失（gitignore 排除）。
    echo 请建立 junction 复用 koodo-reader 的 node_modules：
    echo   cmd /c mklink /J "%~dp0pet\node_modules\electron" "D:\Hermes_workspace\koodo-reader\node_modules\electron"
    echo   cmd /c mklink /J "%~dp0pet\node_modules\ws" "D:\Hermes_workspace\koodo-reader\node_modules\ws"
    pause
    exit /b 1
)

echo Starting Veranima 桌宠（壳 spawn 核心 + TTS）... Ctrl+C to stop
.venv\Scripts\python.exe scripts\run_pet.py
echo.
echo Veranima 桌宠 exited.
pause
