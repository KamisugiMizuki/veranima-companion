"""M5 桌面 Agent 薄壳（M5_SPEC 3.3）：工单 → dsh headless CLI → 结果。

veranima 侧唯一接触 dsh 的文件。dsh 装在项目 dsh/ 目录（gitignore，
clone 后需 npm install @deepseek-ai/dsh@0.1.0-rc.6 到该目录）。
API/配置完全独立：DEEPSEEK_BASE_URL / DEEPSEEK_API_KEY 环境变量（不读 veranima config.yaml）。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

DSH_DIR = Path(__file__).resolve().parents[3] / "dsh"  # 项目根/dsh
DSH_BIN = DSH_DIR / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
DEFAULT_TIMEOUT = 600  # 10min（M5_SPEC 3.3 默认超时）


def dsh_available() -> bool:
    """dsh 是否已安装（dsh/bin.js 存在）。"""
    return DSH_BIN.exists()


def run_dsh_task(workorder: dict, *, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """工单 → dsh headless 子进程 → 结果。

    返回 {"task_id", "output", "exit_code", "ok"}；超时/未安装返回错误码。
    dsh 会话 JSONL 落盘（dsh 自身管理），与 veranima 记忆库隔离。
    """
    result = {"task_id": "", "output": "", "exit_code": -1, "ok": False}
    if isinstance(workorder, str):
        import json
        try:
            workorder = json.loads(workorder)
        except json.JSONDecodeError:
            result["output"] = "工单 JSON 解析失败"
            return result
    result["task_id"] = workorder.get("task_id", "")
    if not dsh_available():
        result["output"] = "dsh 未安装（项目 dsh/ 目录为空）——请先 npm install @deepseek-ai/dsh@0.1.0-rc.6"
        logger.warning(result["output"])
        return result
    # 工单 → dsh prompt（goal + constraints 拼自然语言；M5_SPEC 2.2）
    prompt = workorder.get("goal", "")
    if workorder.get("context"):
        prompt += f"\n补充：{workorder['context']}"
    if workorder.get("constraints"):
        prompt += f"\n约束：{workorder['constraints']}"
    if workorder.get("fallback"):
        prompt += f"\n异常时：{workorder['fallback']}"
    try:
        # Windows：.js 无 shebang，需 node 显式调用（M5_SPEC 3.3）
        node = os.environ.get("NODE_BIN", "") or shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
        cmd = [node, str(DSH_BIN), "--profile", "headless"]
        patch_file = DSH_DIR / "patch.yml"
        if patch_file.exists():
            cmd += ["--patch", str(patch_file)]
        cmd.append(prompt)
        proc = subprocess.run(
            cmd,
            cwd=str(DSH_DIR), capture_output=True, text=True, timeout=timeout,
        )
        result["exit_code"] = proc.returncode
        result["output"] = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        result["ok"] = proc.returncode == 0
        if not result["ok"]:
            logger.warning("dsh task failed (exit %d): %s", proc.returncode, result["output"][:300])
    except subprocess.TimeoutExpired:
        result["output"] = f"任务超时（>{timeout}s）"
        logger.warning(result["output"])
    except OSError as e:
        result["output"] = f"dsh 启动失败: {e}"
        logger.error(result["output"])
    return result
