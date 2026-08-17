#!/bin/bash
# LM Studio 模型加载/卸载脚本（显存友好配置 + 游戏模式）
#
# 目标：与游戏共存（16GB 显存 → 模型占 ~9.2GB，剩 ~7GB 给游戏）
# 参数实测（2026-08，qwen3-8b）：
#   160K context + parallel 4 → 13.9GB（不可游戏）
#   32K  context + parallel 1 → 11.6GB（勉强）
#   16K  context + parallel 1 →  9.2GB（推荐，剩 7GB）
# 16K 对陪伴对话足够（实际 prompt 仅几百 token，历史 20 条 + 记忆注入）
# qwen3.5-9b 多模态（q4_k_m）：16K + parallel 1 → 6.55GB，总占用 ~12.3GB
#
# 用法：
#   bash scripts/run_lmstudio.sh            # 加载默认多模态模型（16K，推荐）
#   bash scripts/run_lmstudio.sh 32768      # 自定义 context
#   bash scripts/run_lmstudio.sh off        # 游戏模式：卸载模型释放显存（~2.3GB）
# 游戏前卸载、游戏后加载即可无缝切换——agent 状态在 SQLite，模型无状态不丢对话。
#
# 需先启动 LM Studio 服务（lms server start 已在脚本内处理）

LMS="$HOME/.lmstudio/bin/lms.exe"
# 默认多模态模型（DESIGN 8.6 图像输入）；可用 VERANIMA_LMS_MODEL 覆盖
MODEL="${VERANIMA_LMS_MODEL:-qwen3.5-9b-uncensored-hauhaucs-aggressive@q4_k_m}"
CTX="${1:-16384}"

if [ ! -f "$LMS" ]; then
    echo "lms CLI 不存在: $LMS"
    exit 1
fi

# 游戏模式：卸载（幂等：未加载时提示退出）
if [ "$1" = "off" ]; then
    if ! "$LMS" ps | grep -q "$MODEL"; then
        echo "当前没有已加载的模型（$MODEL），无需卸载。"
        exit 0
    fi
    echo "==> 卸载模型（显存将释放到 ~2.3GB）"
    if ! "$LMS" unload --all; then
        echo "[错误] 模型卸载失败，请检查 LM Studio 状态后重试。"
        exit 1
    fi
    nvidia-smi --query-gpu=memory.used --format=csv,noheader
    echo "==> 可以开始游戏了。结束后运行：bash scripts/run_lmstudio.sh"
    exit 0
fi

echo "==> 检查当前加载状态"
if "$LMS" ps | grep -q "$MODEL"; then
    echo "模型已在运行，无需重复加载。当前实例："
    "$LMS" ps | grep "$MODEL"
    echo "提示：如需强制重载（改参数），先跑 off 再加载。"
    exit 0
fi

echo "==> 确保 LM Studio 服务器运行"
"$LMS" server start >/dev/null 2>&1 || true

echo "==> 清理其他实例"
"$LMS" unload --all 2>&1 | tail -1

echo "==> 加载 $MODEL (context=$CTX, parallel=1)"
if ! "$LMS" load "$MODEL" -c "$CTX" --parallel 1 -y; then
    echo "[错误] 模型加载失败，请检查 LM Studio 状态后重试。"
    exit 1
fi

echo "==> 当前实例"
"$LMS" ps | grep -E "IDENT|$MODEL"
