#!/bin/bash
# LM Studio 模型加载脚本（显存友好配置）
#
# 目标：与游戏共存（16GB 显存 → 模型占 ~9.2GB，剩 ~7GB 给游戏）
# 参数实测（2026-08）：
#   160K context + parallel 4 → 13.9GB（不可游戏）
#   32K  context + parallel 1 → 11.6GB（勉强）
#   16K  context + parallel 1 →  9.2GB（推荐，剩 7GB）
# 16K 对陪伴对话足够（实际 prompt 仅几百 token，历史 20 条 + 记忆注入）
#
# 用法：
#   bash scripts/run_lmstudio.sh            # 加载 qwen3-8b（16K，推荐）
#   bash scripts/run_lmstudio.sh 32768      # 自定义 context
# 需先启动 LM Studio 服务（lms server start 已在脚本内处理）

LMS="$HOME/.lmstudio/bin/lms.exe"
MODEL="${VERANIMA_LMS_MODEL:-qwen3-8b}"
CTX="${1:-16384}"

if [ ! -f "$LMS" ]; then
    echo "lms CLI 不存在: $LMS"
    exit 1
fi

echo "==> 确保 LM Studio 服务器运行"
"$LMS" server start >/dev/null 2>&1 || true

echo "==> 卸载旧实例"
"$LMS" unload --all 2>&1 | tail -1

echo "==> 加载 $MODEL (context=$CTX, parallel=1)"
"$LMS" load "$MODEL" -c "$CTX" --parallel 1 -y 2>&1 | tail -1

echo "==> 当前实例"
"$LMS" ps | grep -E "IDENT|$MODEL"
