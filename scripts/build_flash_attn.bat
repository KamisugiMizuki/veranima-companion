@echo off
rem ============================================================
rem flash-attn source build script (veranima venv, Windows)
rem Usage: double-click or run from cmd
rem Output: flash_attn installed into .venv
rem   verify: .venv\Scripts\python.exe -c "import flash_attn"
rem Est. time: ~40 min (TORCH_CUDA_ARCH_LIST=8.9 = Ada only)
rem Log: build_flash_attn.log (same dir)
rem NOTE: ASCII only. cmd parses this file as GBK on zh-CN
rem       systems; non-ASCII bytes corrupt line endings.
rem ============================================================
setlocal

cd /d %~dp0

rem ---- 0. sanity checks ----
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Create it first.
    pause & exit /b 1
)

set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VCVARS%" (
    echo [ERROR] VS Build Tools not found: %VCVARS%
    echo Install Visual Studio Build Tools with C++ desktop workload.
    pause & exit /b 1
)

rem ---- 1. enter MSVC env ----
call "%VCVARS%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] vcvars64.bat failed
    pause & exit /b 1
)
echo [OK] MSVC env ready

rem ---- 2. ninja ----
set "NINJA_DIR=.venv\Lib\site-packages\ninja"
if exist "%NINJA_DIR%" set "PATH=%CD%\%NINJA_DIR%;%PATH%"
where ninja >nul 2>&1 || (
    echo [INFO] ninja missing, installing...
    .venv\Scripts\python.exe -m pip install ninja -i https://pypi.tuna.tsinghua.edu.cn/simple || (
        echo [ERROR] ninja install failed
        pause & exit /b 1
    )
    set "PATH=%CD%\.venv\Lib\site-packages\ninja;%PATH%"
)
echo [OK] ninja:
where ninja

rem ---- 3. key params (drive build time) ----
rem RTX 4070 Ti SUPER = Ada sm_89. Build only this arch.
set "TORCH_CUDA_ARCH_LIST=8.9"
rem parallel compile cap: 8 for 32GB RAM, drop to 4 on OOM
set "MAX_JOBS=8"
rem parallel nvcc threads
set "NVCC_APPEND_FLAGS=--threads 8"

rem ---- 4. build & install (log to file; survives crash) ----
echo [INFO] building flash-attn (~40 min), log: build_flash_attn.log
echo start: %date% %time% >> build_flash_attn.log
.venv\Scripts\python.exe -m pip install flash-attn ^
    --no-build-isolation ^
    --no-cache-dir ^
    2>> build_flash_attn.log
set "PIP_EXIT=%errorlevel%"

echo end: %date% %time% >> build_flash_attn.log
if not "%PIP_EXIT%"=="0" (
    echo [ERROR] build failed (exit=%PIP_EXIT%). See build_flash_attn.log tail.
    pause & exit /b 1
)

rem ---- 5. verify ----
echo [OK] installed. Verifying...
.venv\Scripts\python.exe -c "import flash_attn; print('flash_attn', flash_attn.__version__, 'OK')" || (
    echo [WARN] import failed. Check log.
)
pause
