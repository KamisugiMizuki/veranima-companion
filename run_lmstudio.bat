@echo off
rem ============================================================
rem  LM Studio 模型一键加载/卸载（游戏共存）
rem  本文件以 GBK(ANSI) 编码保存，适配中文 Windows cmd。
rem
rem  加载：多模态模型 16K context + parallel 1（约 6.6GB，
rem        总占用 ~12.3GB，游戏可共存）
rem  卸载：释放显存到 ~2.3GB —— 游戏前卸载、游戏后加载即可
rem        无缝切换（agent 状态在 SQLite，模型无状态不丢对话）
rem
rem  容错（2026-08-04）：
rem    - 模型已加载时选 1：检测到已加载，跳过加载（不中断服务）
rem    - 模型未加载时选 2：检测到无模型，提示后退出（幂等）
rem    - lms 命令失败：检查 errorlevel，明确提示而非假装成功
rem
rem  模型可用环境变量 VERANIMA_LMS_MODEL 覆盖
rem ============================================================

set "LMS=%USERPROFILE%\.lmstudio\bin\lms.exe"
if not defined VERANIMA_LMS_MODEL set "VERANIMA_LMS_MODEL=qwen3.5-9b-uncensored-hauhaucs-aggressive@q4_k_m"

if not exist "%LMS%" (
    echo [错误] 未找到 lms CLI: %LMS%
    echo 请确认 LM Studio 已安装，或手动设置 LMS 路径。
    pause
    exit /b 1
)

echo.
echo ============================================
echo   LM Studio 模型管理
echo   模型: %VERANIMA_LMS_MODEL%
echo ============================================
echo   1. 加载模型（16K context，可游戏）
echo   2. 卸载模型（游戏模式，释放显存）
echo   3. 退出
echo ============================================

rem choice 原生处理单键输入：无效键自动重试；EOF/无输入直接退出
choice /c 123 /n /m "请输入 1 / 2 / 3: "
if errorlevel 3 exit /b 0
if errorlevel 2 goto unload

:load
echo.
echo ==^> 检查当前加载状态
"%LMS%" ps | findstr /i "%VERANIMA_LMS_MODEL%" >nul
if not errorlevel 1 (
    echo 模型已在运行，无需重复加载。当前实例：
    "%LMS%" ps | findstr /i "%VERANIMA_LMS_MODEL%"
    echo 提示：如需强制重载（改参数），请先选 2 卸载，再选 1 加载。
    pause
    exit /b 0
)
echo ==^> 确保 LM Studio 服务器运行
"%LMS%" server start >nul 2>&1
echo ==^> 清理其他实例
"%LMS%" unload --all
echo ==^> 加载 %VERANIMA_LMS_MODEL% (context=16384, parallel=1)
"%LMS%" load "%VERANIMA_LMS_MODEL%" -c 16384 --parallel 1 -y
if errorlevel 1 (
    echo [错误] 模型加载失败，请检查 LM Studio 状态后重试。
    pause
    exit /b 1
)
echo ==^> 当前实例
"%LMS%" ps | findstr /i "%VERANIMA_LMS_MODEL%"
echo.
echo 加载完成。游戏结束后可再次运行本脚本选 2 卸载。
pause
exit /b 0

:unload
echo.
echo ==^> 检查当前加载状态
"%LMS%" ps | findstr /i "%VERANIMA_LMS_MODEL%" >nul
if errorlevel 1 (
    echo 当前没有已加载的模型（%VERANIMA_LMS_MODEL%），无需卸载。
    pause
    exit /b 0
)
echo ==^> 卸载模型（显存将释放到 ~2.3GB）
"%LMS%" unload --all
if errorlevel 1 (
    echo [错误] 模型卸载失败，请检查 LM Studio 状态后重试。
    pause
    exit /b 1
)
nvidia-smi --query-gpu=memory.used --format=csv,noheader
echo ==^> 可以开始游戏了。结束后再次运行本脚本选 1 加载。
pause
exit /b 0
