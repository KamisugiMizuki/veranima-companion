@echo off
rem Compatibility launcher. Double-click run_pet.vbs for zero console flash.
cd /d "%~dp0"
if not exist "run_pet.vbs" (
    echo [ERROR] run_pet.vbs is missing.
    pause
    exit /b 1
)
wscript.exe "%~dp0run_pet.vbs"
exit /b 0
