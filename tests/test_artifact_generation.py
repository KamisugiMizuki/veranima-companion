"""Bridge 版 artifact 生成行为测试（fake bridge，不发真实 HTTP）。"""
from __future__ import annotations

import asyncio

import pytest

from veranima.core.shared_creation import SharedCreationStore
from veranima.memory.store import MemoryStore
from veranima.tools.hermes_bridge import TaskRun


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


REPORT_OK = (
    "1. 做了什么：写出了 hello.py 并运行验证\n"
    "2. 改动文件清单：\nD:/ws/gen/hello.py\n"
    "3. 验证结果：python hello.py 输出正确\n"
    "4. 未验证项：无"
)


class FakeBridge:
    workspace_root = "D:/ws"
    timeout = 5

    def __init__(self, final: TaskRun | None = None, *, fail: str | None = None):
        self.final = final or TaskRun(task_id="t", run_id="r", status="succeeded", output=REPORT_OK)
        self.fail = fail
        self.submitted = None

    def submit(self, wo_json):
        if self.fail:
            from veranima.tools.hermes_bridge import HermesBridgeError
            raise HermesBridgeError(self.fail)
        self.submitted = wo_json
        return TaskRun(task_id="t9", run_id="r9", status="queued")

    def status(self, task_id, run_id, prior=None):
        return self.final

    def stop(self, task_id, run_id):
        return TaskRun(task_id=task_id, run_id=run_id, status="cancelled")


@pytest.fixture
def store(tmp_path):
    memory = MemoryStore(db_path=str(tmp_path / "c.db"), config={"decay_enabled": False}, provider=FakeEmbed())
    s = SharedCreationStore(memory)
    yield s
    s.con.close()


def _project_with_scene(store):
    e = store.memory.store_message("user", "帮我生成一个 hello 脚本")
    p = store.create_project(kind="software", title="小工具", purpose="原型", confirmed=True)
    scene = store.create_scene(p.project_id, title="S1", goal="产出可运行的脚本")
    return p, scene, e


def test_generate_artifact_success(tmp_path, store):
    from veranima.core.artifact_generation import generate_artifact_via_bridge
    p, scene, evidence = _project_with_scene(store)
    bridge = FakeBridge()
    artifact, run = asyncio.run(generate_artifact_via_bridge(
        store, bridge, project_id=p.project_id, scene_id=scene.scene_id,
        title="hello 脚本 v1", instruction="写一个打印 hello 的 python 脚本",
        evidence_message_ids=[evidence], wait_poll_seconds=0,
    ))
    assert artifact.sha256  # hash 已回存
    assert artifact.version == 1
    assert "hello.py" in artifact.content       # 文件清单折叠为摘要行
    assert "[由 Hermes 执行器生成" in artifact.content
    assert run.status == "succeeded"
    # prompt 含工作区边界
    assert "D:/ws" in (bridge.submitted.get("goal") if isinstance(bridge.submitted, dict) else bridge.submitted)


def test_generate_artifact_rejects_violation(tmp_path, store):
    from veranima.core.artifact_generation import ArtifactGenerationError, generate_artifact_via_bridge
    p, scene, evidence = _project_with_scene(store)
    bad = TaskRun(task_id="t", run_id="r", status="succeeded", output=REPORT_OK,
                  warnings=("workspace_violation",))
    with pytest.raises(ArtifactGenerationError, match="越界"):
        asyncio.run(generate_artifact_via_bridge(
            store, FakeBridge(bad), project_id=p.project_id, scene_id=scene.scene_id,
            title="x", instruction="x", evidence_message_ids=[evidence],
        ))
    assert store.list_artifacts(scene.scene_id) == []  # 无半截产物


def test_generate_artifact_failure_no_artifact(tmp_path, store):
    from veranima.core.artifact_generation import ArtifactGenerationError, generate_artifact_via_bridge
    p, scene, evidence = _project_with_scene(store)
    failed = TaskRun(task_id="t", run_id="r", status="failed", error="boom")
    with pytest.raises(ArtifactGenerationError, match="failed|失败"):
        asyncio.run(generate_artifact_via_bridge(
            store, FakeBridge(failed), project_id=p.project_id, scene_id=scene.scene_id,
            title="x", instruction="x", evidence_message_ids=[evidence],
        ))


def test_generate_artifact_bridge_offline(tmp_path, store):
    from veranima.core.artifact_generation import ArtifactGenerationError, generate_artifact_via_bridge
    p, scene, evidence = _project_with_scene(store)
    with pytest.raises(ArtifactGenerationError, match="Hermes"):
        asyncio.run(generate_artifact_via_bridge(
            store, FakeBridge(fail="offline: refused"),
            project_id=p.project_id, scene_id=scene.scene_id,
            title="x", instruction="x", evidence_message_ids=[evidence],
        ))
