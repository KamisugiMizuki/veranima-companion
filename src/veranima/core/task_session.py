"""QQ 任务会话管理（HERMES_AGENT_INTEGRATION_SPEC 阶段 4）。

职责：任务分流 → 确认流 → 提交 Hermes → 审批转发/硬匹配 → 超时 deny
→ 终态补报 → brief/detail 分层发送。

边界（SPEC §6.2/§15.5）：
- 普通聊天消息永远不被当作审批；审批只认 once/session/always/deny 硬匹配
- 闲聊不触发任务管道（is_task_request 规则判定，LLM 无权限）
- Hermes 离线 fail-closed，不影响陪伴主链
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from ..core.workorder import (
    WorkOrder,
    build_workorder,
    build_workorder_llm,
    clarification_question,
    is_task_request,
    validate_workorder,
)
from ..tools.hermes_bridge import (
    APPROVAL_CHOICES,
    HermesBridgeError,
    HermesExecutionBridge,
    TaskRun,
    load_bridge_config,
)

logger = logging.getLogger(__name__)

BRIEF_MAX_CHARS = 120
DETAIL_CHUNK_CHARS = 2000
DETAIL_MAX_MESSAGES = 3


def make_brief(run: TaskRun) -> str:
    """≤120 字的摘要，进聊天气泡/TTS（SPEC §15.3）。"""
    if run.status == "succeeded":
        first = (run.output or "").strip().splitlines()
        what = ""
        for line in first:
            if line.strip().startswith(("1.", "1、")):
                what = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                break
        text = f"办好了：{what or '任务完成'}"
    elif run.status == "failed":
        text = f"没办成：{(run.error or '执行器报告失败')[:60]}"
    elif run.status == "cancelled":
        text = "那个任务取消了。"
    elif run.status in ("timed_out", "orphaned"):
        text = "任务超时了，结果没能确认，回头我再看看。"
    else:
        return ""
    if run.warnings and "workspace_violation" in run.warnings:
        text += "（注意：发现越界改动，需要你确认回滚）"
    return text[:BRIEF_MAX_CHARS]


def split_detail(detail: str, *, chunk: int = DETAIL_CHUNK_CHARS) -> list[str]:
    """detail 安全分片（SPEC §15.3）：超过 max 条数时只返回首条+按需拉取提示。"""
    detail = (detail or "").strip()
    if not detail:
        return []
    total_chunks = (len(detail) + chunk - 1) // chunk
    if total_chunks > DETAIL_MAX_MESSAGES:
        return [detail[:chunk] + "\n…（内容较长，回复「详情」查看剩余）"]
    return [detail[i * chunk:(i + 1) * chunk] for i in range(total_chunks)]


class QQTaskSessionManager:
    """每个白名单用户一个任务流状态机：idle → awaiting_confirm → running。"""

    def __init__(self, agent, bridge: HermesExecutionBridge | None):
        self.agent = agent
        self.bridge = bridge  # None = tasks 未启用
        self.pending_confirm: dict[str, WorkOrder] = {}   # uid → 待确认工单
        self.running: dict[str, TaskRun] = {}             # uid → 最近一次运行
        self.awaiting_approval: dict[str, dict] = {}      # uid → approval 上下文
        self._tasks: set[asyncio.Task] = set()
        self.approval_timeout_seconds = int(
            ((getattr(agent, "config", {}) or {}).get("tasks", {}) or {}).get("approval_timeout_seconds", 600)
        )
        # 断线补报：启动时扫描未推送终态
        self.unreported: list[dict] = [
            r for r in (agent.memory.task_runs_unfinished() if bridge else [])
        ]

    @property
    def enabled(self) -> bool:
        return self.bridge is not None

    # ---------- 入口分流 ----------

    def route(self, uid: str, text: str, *, is_task: bool | None = None) -> dict | None:
        """返回需要特殊处理的任务动作；None = 交给普通对话链。

        优先级：审批硬匹配 > 确认流 > 任务指令 > 状态查询。
        is_task=统一判断点的语义裁决（"把那份报告转成 PDF"不带"帮我"也算
        任务）；None=未裁决，退回 _TASK_TRIGGERS 词表兜底。"""
        t = (text or "").strip()
        # 1. 审批硬匹配（SPEC §15.5）：只在 awaiting_approval 时生效
        ctx = self.awaiting_approval.get(uid)
        if ctx is not None:
            choice = HermesExecutionBridge.match_approval_choice(t, strict=False)
            if choice is not None:
                return {"action": "approve", "choice": choice, "run_id": ctx["run_id"], "task_id": ctx["task_id"]}
            # 在等待审批时说了别的话：不当作审批，但也不打断——提示一次
            return {"action": "approval_reminder", "task_id": ctx["task_id"]}
        # 2. 确认流
        if uid in self.pending_confirm:
            wo = self.pending_confirm.get(uid)
            if any(k in t for k in ("取消", "算了", "不做了")):
                self.pending_confirm.pop(uid, None)
                return {"action": "cancelled_pending"}
            if any(k in t for k in ("确认", "可以", "执行吧", "开始吧")):
                self.pending_confirm.pop(uid, None)
                return {"action": "submit", "workorder": wo}
            # 其他回复视为补充信息 → 重新构建工单
            return {"action": "rebuild", "text": t}
        # 3. 任务指令
        task_hit = is_task if is_task is not None else is_task_request(t)
        if self.enabled and task_hit:
            return {"action": "new_task", "text": t}
        # 4. 状态查询
        if self.enabled and re.fullmatch(r"(任务|task)(状态|怎么样了|进度)[?？]?", t):
            run = self.running.get(uid)
            return {"action": "status", "run": run}
        return None

    # ---------- 工单构建与确认 ----------

    def build(self, text: str) -> WorkOrder:
        """LLM 意图补全 → 降级规则版（与 CLI 同源）。"""
        try:
            llm = self.agent.llm
            wo = build_workorder_llm(llm, text) if llm.is_available() else build_workorder(text)
        except Exception:
            wo = build_workorder(text)
        return wo

    def propose(self, uid: str, wo: WorkOrder) -> str | None:
        """校验+澄清；通过后进入待确认，返回给用户看的确认文案。"""
        q = clarification_question(wo)
        if q:
            return f"需要先说清楚：{q}\n（补充信息后我再重新整理工单）"
        issues = validate_workorder(wo)
        if issues:
            return "工单有问题：" + "；".join(issues[:2]) + "\n（改一下说法再发我）"
        # 阶段 3 门禁：代码修改任务默认拒绝
        if self.bridge and not self.bridge.worktree_for_code and self.bridge.classify_code_task(wo.goal):
            return (
                "代码修改类任务现在还做不了——执行隔离还没实测通过，我不能直接动仓库。"
                "只读检索、临时目录里的文件操作可以正常交给我。"
            )
        self.pending_confirm[uid] = wo
        return (
            f"我理解的任务：{wo.goal}\n"
            f"类型：{wo.task_type}｜编号：{wo.task_id}\n"
            f"（会保存任务记录和结果摘要；回复「确认」执行，「取消」放弃）"
        )

    # ---------- 提交与监控 ----------

    async def submit_and_watch(self, uid: str, wo: WorkOrder, send) -> None:
        """提交 Hermes 并后台轮询到终态。send 是 async 发送回调。

        - 审批出现 → 转发原文 + 四关键词，进入硬匹配等待
        - 审批超时 → 自动 deny（SPEC §15.6）
        - 终态 → brief + detail 分层发送
        """
        assert self.bridge is not None
        try:
            run = await asyncio.to_thread(self.bridge.submit, wo.to_json())
        except HermesBridgeError as e:
            await send(f"没提交成功：{e}")
            return
        self.running[uid] = run
        self.agent.memory.task_run_upsert(run.task_id, run.run_id, run.status, run.raw_status)
        await send(f"已安排（编号 {run.task_id}），跑完告诉你。")

        deadline = time.monotonic() + self.bridge.timeout
        approval_deadline: float | None = None
        prior: TaskRun | None = None
        while True:
            try:
                run = await asyncio.to_thread(
                    self.bridge.status, run.task_id, run.run_id, prior,
                )
            except HermesBridgeError as e:
                logger.warning("task %s status failed: %s", run.task_id, e)
                run = TaskRun(task_id=run.task_id, run_id=run.run_id,
                              status="orphaned", error=str(e))
            prior = run
            self.running[uid] = run
            self.agent.memory.task_run_upsert(
                run.task_id, run.run_id, run.status, run.raw_status,
                run.output, run.error,
                {"changed_files": list(run.changed_files), "test_summary": run.test_summary,
                 "warnings": list(run.warnings)},
            )
            now = time.monotonic()
            # 审批转发（只发一次）
            if run.status == "waiting_for_approval" and uid not in self.awaiting_approval:
                self.awaiting_approval[uid] = {"run_id": run.run_id, "task_id": run.task_id}
                approval_deadline = now + self.approval_timeout_seconds
                req = run.approval_request or {}
                desc = req.get("command") or req.get("description") or json_dumps_safe(req)
                await send(
                    f"任务 {run.task_id} 需要你审批：\n{desc}\n\n"
                    "回复 once / session / always / deny 之一。"
                )
            # 审批超时自动 deny（安全方向）
            if approval_deadline is not None and now > approval_deadline and uid in self.awaiting_approval:
                logger.info("approval timeout for %s → auto deny", run.task_id)
                try:
                    run = await asyncio.to_thread(self.bridge.approve, run.task_id, run.run_id, "deny")
                except (HermesBridgeError, ValueError) as e:
                    logger.warning("auto-deny failed: %s", e)
                self.awaiting_approval.pop(uid, None)
                approval_deadline = None
                continue
            if run.status in ("succeeded", "failed", "cancelled", "timed_out", "orphaned"):
                break
            if now > deadline:
                stopped = await asyncio.to_thread(self.bridge.stop, run.task_id, run.run_id)
                run = stopped if stopped.status in ("cancelled", "orphaned") else TaskRun(
                    task_id=run.task_id, run_id=run.run_id, status="timed_out")
                break
            await asyncio.sleep(5.0)

        self.awaiting_approval.pop(uid, None)
        self.running[uid] = run
        # 终态推送：brief 必发；detail 分片
        brief = make_brief(run)
        if brief:
            await send(brief)
        detail = run.output or run.error
        chunks = split_detail(detail)
        for c in chunks:
            await send(c)


def json_dumps_safe(d: dict) -> str:
    import json
    try:
        return json.dumps(d, ensure_ascii=False)[:300]
    except Exception:
        return str(d)[:300]
