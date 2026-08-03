@echo off
rem Veranima QQ launcher (NapCatQQ OneBot v11)
rem Double-click to start: .venv\Scripts\python -m veranima.qq
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

echo Starting Veranima QQ (ws://127.0.0.1:8099/ws) ... Ctrl+C to stop
.venv\Scripts\python.exe -m veranima.qq
echo.
echo Veranima QQ exited.
pause
