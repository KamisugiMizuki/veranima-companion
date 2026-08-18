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
rem CRITICAL: flash-attn 2.8.3 reads FLASH_ATTN_CUDA_ARCHS (NOT
rem TORCH_CUDA_ARCH_LIST). Default "80;90;100;120" builds 4 archs -> 4x time
rem + nvcc 12.9 cicc crashes on sm80 (ACCESS_VIOLATION, observed). RTX 4070
rem Ti SUPER is Ada sm_89; build only that.
set "FLASH_ATTN_CUDA_ARCHS=89"
set "TORCH_CUDA_ARCH_LIST=8.9"
rem Parallel compile cap. CRITICAL: flash_bwd .cu files are template monsters
rem (5-8GB RAM each during nvcc). MAX_JOBS=8 + --threads 8 OOMs on 32GB RAM
rem ("catastrophic error: out of memory" in cute/layout.hpp). 4 is safe with
rem bwd disabled.
set "MAX_JOBS=4"
rem NO NVCC_APPEND_FLAGS: nvcc --threads N multiplies per-file RAM too.
rem System has CUDA 12.6/12.9/13.2 but torch is cu128 (needs 12.8).
rem VERIFIED: use 12.6 (NOT 12.9) - nvcc 12.9's cicc crashes on flash-attn
rem template files (0xC0000005 / 0xC0000409, both observed); 12.6 builds
rem clean in ~50 min. 12.6 output runs on 12.8 runtime (backward compatible).
set "CUDA_HOME=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6"
set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6"
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
rem flash-attn 2.8.3 has no env switch for skipping backward; patch setup.py
rem to enable -DFLASHATTENTION_DISABLE_BACKWARD (bwd templates OOM 32GB RAM;
rem qwen-tts inference only needs forward). Then install from local dir.
set "FA_URL=https://pypi.tuna.tsinghua.edu.cn/packages/01/7a/92a46e7cd6bbb4d7b2855a457c3b855df54a97af5656d98fc92e58e61065/flash_attn-2.8.3.post1.tar.gz"
set "FA_SRC=.cache-flash-attn"
if not exist "%FA_SRC%" mkdir "%FA_SRC%"
if not exist "%FA_SRC%\flash_attn-2.8.3.post1\setup.py" (
    echo [INFO] downloading flash-attn source...
    .venv\Scripts\python.exe -c "import urllib.request; urllib.request.urlretrieve(r'%FA_URL%', r'%FA_SRC%\flash_attn.tar.gz')" || (
        echo [ERROR] download failed
        pause & exit /b 1
    )
    .venv\Scripts\python.exe -c "import tarfile; tarfile.open(r'%FA_SRC%\flash_attn.tar.gz').extractall(r'%FA_SRC%')" || (
        echo [ERROR] extract failed
        pause & exit /b 1
    )
)
echo [INFO] patching setup.py (disable backward)...
.venv\Scripts\python.exe scripts\patch_flash_attn_no_bwd.py "%FA_SRC%\flash_attn-2.8.3.post1\setup.py" || (
    echo [ERROR] patch failed
    pause & exit /b 1
)
echo [INFO] building flash-attn (~40 min), log: build_flash_attn.log
echo start: %date% %time% >> build_flash_attn.log
.venv\Scripts\python.exe -m pip install "%FA_SRC%\flash_attn-2.8.3.post1" ^
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
