@echo off
rem Veranima 桌宠 launcher（Electron 壳 + 核心 + TTS 三进程，无控制台窗口）
rem 双击启动：pythonw 无窗口跑 run_pet.py，本窗口立即关闭
cd /d "%~dp0"

rem Hermes-style PYTHONPATH pollution guard (harmless otherwise)
set PYTHONPATH=

if not exist ".venv\Scripts\pythonw.exe" (
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

rem 后台无窗口启动（pythonw = 无控制台；start 分离后本窗口立即退出）
start "" ".venv\Scripts\pythonw.exe" scripts\run_pet.py
exit /b 0
