@echo off
rem ============================================================
rem flash-attn 源码编译脚本（veranima venv，Windows）
rem 用法：双击运行 或 cmd 里执行本文件
rem 产物：.venv 里安装 flash_attn（验证：python -c "import flash_attn"）
rem 预计耗时：~40 分钟（TORCH_CUDA_ARCH_LIST=8.9 只编 Ada 内核）
rem 日志：build_flash_attn.log（同目录）
rem ============================================================
setlocal

cd /d %~dp0

rem ---- 0. 环境检查 ----
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv 不存在，请先创建虚拟环境
    pause & exit /b 1
)

rem 找 VS BuildTools（vcvars64.bat）
set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VCVARS%" (
    echo [ERROR] 未找到 VS BuildTools: %VCVARS%
    echo 请安装 Visual Studio Build Tools（勾选 C++ 桌面开发）
    pause & exit /b 1
)

rem ---- 1. 进入 MSVC 环境（cl.exe / link.exe / nmake）----
call "%VCVARS%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] vcvars64.bat 执行失败
    pause & exit /b 1
)
echo [OK] MSVC 环境就绪

rem ---- 2. ninja（pip 装的，加到 PATH）----
set "NINJA_DIR=.venv\Lib\site-packages\ninja"
if exist "%NINJA_DIR%" set "PATH=%CD%\%NINJA_DIR%;%PATH%"
where ninja >nul 2>&1 || (
    echo [INFO] ninja 未找到，用 pip 安装...
    .venv\Scripts\python.exe -m pip install ninja -i https://pypi.tuna.tsinghua.edu.cn/simple || (
        echo [ERROR] ninja 安装失败
        pause & exit /b 1
    )
    set "PATH=%CD%\.venv\Lib\site-packages\ninja;%PATH%"
)
echo [OK] ninja: 
where ninja

rem ---- 3. 关键参数（决定编译时长）----
rem RTX 4070 Ti SUPER = Ada Lovelace sm_89。只编这个架构，不编全系。
set "TORCH_CUDA_ARCH_LIST=8.9"
rem 并行编译上限：内存够就 8，编译报 OOM 就降到 4
set "MAX_JOBS=8"
rem 让 nvcc 并行编译（默认单线程极慢）
set "NVCC_APPEND_FLAGS=--threads 8"

rem ---- 4. 编译安装（日志落盘，崩溃也能看进度）----
echo [INFO] 开始编译 flash-attn（~40 分钟），日志: build_flash_attn.log
echo 开始时间: %date% %time% >> build_flash_attn.log
.venv\Scripts\python.exe -m pip install flash-attn ^
    --no-build-isolation ^
    --no-cache-dir ^
    2>> build_flash_attn.log
set "PIP_EXIT=%errorlevel%"

echo 结束时间: %date% %time% >> build_flash_attn.log
if not "%PIP_EXIT%"=="0" (
    echo [ERROR] 编译失败（exit=%PIP_EXIT%），看 build_flash_attn.log 末尾
    pause & exit /b 1
)

rem ---- 5. 验证 ----
echo [OK] 安装完成，验证中...
.venv\Scripts\python.exe -c "import flash_attn; print('flash_attn', flash_attn.__version__, 'OK')" || (
    echo [WARN] import 失败——可能被 pip 装成了别的版本，见日志
)
pause
