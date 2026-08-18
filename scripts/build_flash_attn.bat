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

rem locate project root: run from scripts/ (one level up) or project root (copy)
if exist ".venv\Scripts\python.exe" goto :found
cd /d %~dp0..
if exist ".venv\Scripts\python.exe" goto :found
echo [ERROR] .venv not found (looked in %CD% and %~dp0). Run from project root.
pause & exit /b 1
:found
echo [INFO] project root: %CD%

rem ---- 0. sanity checks ----
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Create it first.
    pause & exit /b 1
)

set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
rem NOTE: VCVARS contains "(x86)" - never expand it inside a parenthesized
rem if-block, cmd parses the parens as block nesting and dies on "\Microsoft".
if not exist "%VCVARS%" goto :err_vcvars

rem ---- 1. enter MSVC env ----
call "%VCVARS%" >nul 2>&1
if errorlevel 1 goto :err_vcvars
echo [OK] MSVC env ready
goto :after_vcvars

:err_vcvars
echo [ERROR] VS Build Tools not found: %VCVARS%
echo Install Visual Studio Build Tools with C++ desktop workload.
pause & exit /b 1

:after_vcvars

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
rem Parallel compile cap. CRITICAL: flash_bwd .cu files are template monsters
rem (5-8GB RAM each during nvcc). MAX_JOBS=8 + --threads 8 OOMs on 32GB RAM
rem ("catastrophic error: out of memory" in cute/layout.hpp). 2 is safe.
set "MAX_JOBS=2"
rem NO NVCC_APPEND_FLAGS: nvcc --threads N multiplies per-file RAM too.
rem System has CUDA 12.6/12.9/13.2 but torch is cu128 (needs 12.8).
rem Use closest 12.9: minor-version compatible (12.9 build runs on 12.8 runtime).
set "CUDA_HOME=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9"
set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9"
set "PATH=%CUDA_HOME%\bin;%PATH%"
rem Hermes desktop injects PYTHONPATH into child processes; clear it so
rem pip/torch resolve to THIS venv, not the agent's.
set "PYTHONPATH="
rem torch: use the activated VC env without re-activation
set "DISTUTILS_USE_SDK=1"

rem ---- 3.5 patch torch CUDA version check (raise -> warning) ----
rem torch requires exact CUDA match (12.8); we use 12.9. Patch is idempotent.
.venv\Scripts\python.exe scripts\patch_torch_cuda_check.py
if errorlevel 1 (
    echo [ERROR] torch patch failed
    pause & exit /b 1
)

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
