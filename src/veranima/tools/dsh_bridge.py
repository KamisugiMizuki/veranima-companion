"""R5 桌面 Agent 薄壳（R5_SPEC 4.dsh bridge）：工单 → dsh headless CLI → 结果。

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
DEFAULT_TIMEOUT = 600  # 10min（R5_SPEC 3.3 默认超时）


def dsh_available() -> bool:
    """dsh 是否已安装（dsh/bin.js 存在）。"""
    return DSH_BIN.exists()


def run_dsh_task(workorder: dict, cancel_event=None, *, timeout: int = DEFAULT_TIMEOUT,
                 output_max_chars: int = 12000) -> dict:
    """工单 → dsh headless 子进程 → 结果（R5_SPEC 4）。

    返回 {"task_id", "status", "output", "exit_code", "ok"}；status ∈
    running/succeeded/failed/cancelled/timed_out（R5_SPEC 3 生命周期）。
    - 独立 cwd/env/超时；argv 列表调用，不拼 shell 字符串
    - 输出截断到 output_max_chars（原始日志由 dsh 自身落盘）
    - cancel_event：Threading.Event；置位后终止子进程返回 cancelled
    - 不阻塞核心对话线程：调用方需在 to_thread/async 边界使用
    """
    result = {"task_id": "", "status": "failed", "output": "", "exit_code": -1, "ok": False}
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
    # 工单 → dsh prompt（goal + constraints 拼自然语言；R5_SPEC 2.2）
    prompt = workorder.get("goal", "")
    if workorder.get("context"):
        prompt += f"\n补充：{workorder['context']}"
    if workorder.get("constraints"):
        prompt += f"\n约束：{workorder['constraints']}"
    if workorder.get("fallback"):
        prompt += f"\n异常时：{workorder['fallback']}"
    try:
        # Windows：.js 无 shebang，需 node 显式调用（R5_SPEC 3.3）
        node = os.environ.get("NODE_BIN", "") or shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
        cmd = [node, str(DSH_BIN), "--profile", "headless"]
        patch_file = DSH_DIR / "patch.yml"
        if patch_file.exists():
            cmd += ["--patch", str(patch_file)]
        cmd.append(prompt)
        # Popen + 轮询：支持取消与超时（R5_SPEC 4：非零退出/超时/取消都结构化返回）
        proc = subprocess.Popen(
            cmd,
            cwd=str(DSH_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        result["status"] = "running"
        import time
        deadline = time.monotonic() + timeout
        while True:
            try:
                rc = proc.wait(timeout=0.2)
                break  # 进程结束
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    proc.kill()
                    proc.wait()
                    result["status"] = "cancelled"
                    result["output"] = "任务已取消"
                    logger.info("dsh task cancelled: %s", result["task_id"])
                    return result
                if time.monotonic() > deadline:
                    proc.kill()
                    proc.wait()
                    result["status"] = "timed_out"
                    result["output"] = f"任务超时（>{timeout}s）"
                    logger.warning(result["output"])
                    return result
        result["exit_code"] = rc
        out = ""
        if proc.stdout:
            try:
                out = proc.stdout.read().strip()
            except Exception:
                out = ""
        if len(out) > output_max_chars:
            out = out[:output_max_chars] + f"\n…（截断，共 {len(out)} 字符）"
        result["output"] = out
        result["ok"] = rc == 0
        result["status"] = "succeeded" if result["ok"] else "failed"
        if not result["ok"]:
            logger.warning("dsh task failed (exit %d): %s", rc, out[:300])
    except OSError as e:
        result["output"] = f"dsh 启动失败: {e}"
        logger.error(result["output"])
    return result
