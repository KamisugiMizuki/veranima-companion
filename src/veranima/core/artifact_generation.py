"""Bridge 版 artifact 生成（SHARED_CREATION_SPEC §4.2 + HERMES 集成）。

软件/研究类 Scene 的「生成或修改 artifact」步骤委托 Hermes 执行：
工作区内真实创建文件、运行验证、返回四段报告。产物以「摘要+hash+路径」
回存 shared_artifacts（SPEC §3.4：大文件留在工作区，不进记忆）。

边界：写操作仅限 workspace_root（bridge 审计）；Hermes 离线 fail-closed。
"""
from __future__ import annotations

import logging

from ..tools.hermes_bridge import HermesBridgeError, HermesExecutionBridge, TaskRun

logger = logging.getLogger(__name__)


class ArtifactGenerationError(RuntimeError):
    pass


def build_generation_prompt(*, project_title: str, scene_goal: str,
                            instruction: str, workspace_root: str) -> str:
    return (
        f"共同项目「{project_title}」的一次生成任务。\n"
        f"本步目标：{scene_goal}\n"
        f"用户指令：{instruction}\n"
        f"请在 {workspace_root} 内完成；不要改动该目录之外的任何文件。"
    )


async def generate_artifact_via_bridge(
    store,  # SharedCreationStore
    bridge: HermesExecutionBridge,
    *,
    project_id: str,
    scene_id: str,
    title: str,
    instruction: str,
    evidence_message_ids: list[int],
    wait_poll_seconds: float = 5.0,
):
    """提交生成任务 → 轮询终态 → 校验产物 → save_artifact 回存。

    返回 (Artifact, TaskRun)。失败抛 ArtifactGenerationError，不产生半截产物记录。
    """
    project = store.get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    scene = store.get_scene(scene_id)
    goal = scene.goal if scene else title
    prompt = build_generation_prompt(
        project_title=project.title, scene_goal=goal,
        instruction=instruction, workspace_root=bridge.workspace_root,
    )
    wo = {
        "task_id": f"gen-{scene_id}-{abs(hash(title)) % 10000}",
        "goal": prompt,
        "task_type": "自动化流程",
    }
    try:
        run = await _run_blocking(bridge.submit, wo)
        final = await _wait_terminal(bridge, run, poll=wait_poll_seconds)
    except HermesBridgeError as e:
        raise ArtifactGenerationError(f"Hermes 执行失败：{e}") from e

    if final.status != "succeeded":
        raise ArtifactGenerationError(
            f"生成未成功（{final.status}）：{(final.error or final.output)[:200]}"
        )
    if "workspace_violation" in final.warnings:
        raise ArtifactGenerationError("执行器越界修改了工作区外的文件，产物已拒绝入库")
    content = _summarize(final)
    if not content.strip():
        raise ArtifactGenerationError("执行器没有返回可用内容")
    from dataclasses import replace
    # save_artifact 要求 evidence 含用户消息——生成任务本身由用户发起，证据沿用
    artifact = await _run_blocking(
        store.save_artifact, project_id, scene_id,
        title=title, content=content,
        evidence_message_ids=evidence_message_ids,
    )
    return artifact, final


async def _run_blocking(fn, *args, **kwargs):
    import asyncio
    return await asyncio.to_thread(fn, *args, **kwargs)


async def _wait_terminal(bridge: HermesExecutionBridge, run: TaskRun,
                         *, poll: float) -> TaskRun:
    import asyncio
    import time

    deadline = time.monotonic() + bridge.timeout
    prior: TaskRun | None = None
    while True:
        run = await _run_blocking(bridge.status, run.task_id, run.run_id, prior)
        prior = run
        if run.status in ("succeeded", "failed", "cancelled", "timed_out", "orphaned"):
            return run
        if time.monotonic() > deadline:
            stopped = await _run_blocking(bridge.stop, run.task_id, run.run_id)
            return stopped if stopped.status in ("cancelled", "orphaned") else TaskRun(
                task_id=run.task_id, run_id=run.run_id, status="timed_out")
        await asyncio.sleep(poll)


def _summarize(run: TaskRun) -> str:
    """四段报告 → artifact 正文（做了什么+验证结果；文件清单和路径留工作区）。"""
    lines = [ln for ln in (run.output or "").splitlines()]
    keep: list[str] = []
    section = 0
    for line in lines:
        s = line.strip()
        if s.startswith(("1.", "1、")):
            section = 1; keep.append(s); continue
        if s.startswith(("2.", "2、")):
            section = 2; keep.append(s.split("：", 1)[-1].split(":", 1)[-1].strip()); continue
        if s.startswith(("3.", "3、")):
            section = 3; keep.append(s); continue
        if s.startswith(("4.", "4、")):
            section = 4; continue
        if section == 2 and s:
            continue  # 文件逐行清单折叠为一行摘要
        if s:
            keep.append(s)
    text = "\n".join(k for k in keep if k).strip()
    return f"{text}\n\n[由 Hermes 执行器生成 | run={run.run_id} | raw_status={run.raw_status}]"
