"""阶段 3/4 行为测试：worktree 门禁、QQ 任务流（确认/审批硬匹配/超时 deny/分层推送）。"""
from __future__ import annotations

import asyncio

import pytest

from veranima.core.task_session import QQTaskSessionManager, make_brief, split_detail
from veranima.tools.hermes_bridge import HermesExecutionBridge, TaskRun


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    base_url = ""
    def is_available(self):
        return False
    def chat(self, messages, **kw):
        raise RuntimeError("no llm")


@pytest.fixture
def store(tmp_path):
    from veranima.memory.store import MemoryStore
    s = MemoryStore(db_path=str(tmp_path / "t.db"), config={"decay_enabled": False}, provider=FakeEmbed())
    yield s
    s.con.close()


class FakeAgent:
    def __init__(self, memory):
        self.memory = memory
        self.llm = FakeLLM()
        self.config = {}


# ---------- 阶段 3：代码任务门禁 ----------

def test_code_task_gate_blocks_by_default(store):
    bridge = HermesExecutionBridge({"base_url": "http://x", "worktree_for_code": False})
    mgr = QQTaskSessionManager(FakeAgent(store), bridge)
    reply = mgr.propose("u1", mgr.build("帮我改代码修复登录模块"))
    assert reply is not None and "做不了" in reply
    assert "u1" not in mgr.pending_confirm  # 未进入确认流


def test_non_code_task_not_blocked_by_gate(store):
    bridge = HermesExecutionBridge({"base_url": "http://x", "worktree_for_code": False,
                                    "workspace_root": "D:/Hermes_workspace/veranima"})
    mgr = QQTaskSessionManager(FakeAgent(store), bridge)
    reply = mgr.propose("u1", mgr.build("帮我在 D:/Hermes_workspace/veranima/tmp 整理一下日志文件转成 PDF"))
    assert reply is None or "做不了" not in (reply or "")


# ---------- 阶段 4a：分流与确认流 ----------

def _mgr(store, enabled=True):
    bridge = HermesExecutionBridge({
        "base_url": "http://127.0.0.1:8642", "workspace_root": "D:/ws",
        "timeout_seconds": 5,
    }) if enabled else None
    return QQTaskSessionManager(FakeAgent(store), bridge)


def test_chat_not_routed_when_disabled(store):
    mgr = _mgr(store, enabled=False)
    assert mgr.route("u1", "帮我把文档转成 PDF") is None  # 未启用 → 全部走陪伴链


def test_new_task_requires_confirmation(store):
    mgr = _mgr(store)
    action = mgr.route("u1", "帮我把 D:/docs/a.xlsx 转成 PDF")
    assert action["action"] == "new_task"
    reply = mgr.propose("u1", mgr.build(action["text"]))
    assert "确认" in reply and "u1" in mgr.pending_confirm


def test_confirm_then_submit_action(store):
    mgr = _mgr(store)
    from veranima.core.workorder import WorkOrder
    mgr.pending_confirm["u1"] = WorkOrder(goal="整理 D:/x 下的文件并转成 PDF", source="D:/x",
                                          task_type="文档处理", task_id="t88", status="confirmed")
    action = mgr.route("u1", "确认")
    assert action["action"] == "submit"


def test_cancel_pending(store):
    mgr = _mgr(store)
    mgr.pending_confirm["u1"] = object()
    assert mgr.route("u1", "算了不做了")["action"] == "cancelled_pending"
    assert "u1" not in mgr.pending_confirm


# ---------- 阶段 4b：审批硬匹配 ----------

def test_approval_hard_match_only_keywords(store):
    mgr = _mgr(store)
    mgr.awaiting_approval["u1"] = {"run_id": "r1", "task_id": "t1"}
    # 硬匹配命中
    a = mgr.route("u1", "once")
    assert a["action"] == "approve" and a["choice"] == "once"
    # 否定语境 → 提醒而非执行
    mgr.awaiting_approval["u1"] = {"run_id": "r1", "task_id": "t1"}
    a = mgr.route("u1", "不要 once")
    assert a["action"] == "approval_reminder"
    # 普通聊天 → 提醒，绝不当审批
    mgr.awaiting_approval["u1"] = {"run_id": "r1", "task_id": "t1"}
    a = mgr.route("u1", "今天天气真好啊")
    assert a["action"] == "approval_reminder"


def test_approval_timeout_auto_deny(store, monkeypatch):
    """审批等待超时 → 自动 deny，run 走到终态后正常推送。"""
    mgr = _mgr(store)
    mgr.approval_timeout_seconds = 0  # 立即超时
    calls = {"approve": None}

    class FakeBridge:
        timeout = 5
        def submit(self, wo_json):
            return TaskRun(task_id="t9", run_id="r9", status="running")
        def status(self, task_id, run_id, prior=None):
            return TaskRun(task_id=task_id, run_id=run_id, status="waiting_for_approval",
                           approval_request={"command": "rm -rf /tmp/x"})
        def approve(self, task_id, run_id, choice):
            calls["approve"] = choice
            return TaskRun(task_id=task_id, run_id=run_id, status="cancelled")
        def stop(self, task_id, run_id):
            return TaskRun(task_id=task_id, run_id=run_id, status="cancelled")

    sent: list[str] = []
    async def send(m):
        sent.append(m)

    mgr.bridge = FakeBridge()
    from veranima.core.workorder import WorkOrder as _WO
    asyncio.run(mgr.submit_and_watch("u1", _WO(goal="x", task_id="t9"), send))
    assert calls["approve"] == "deny"
    assert any("取消" in m or "超时" in m for m in sent) or make_brief(TaskRun(task_id="t9", run_id="r9", status="cancelled")) in sent


# ---------- 阶段 4c：brief/detail ----------

def test_make_brief_success_under_limit():
    run = TaskRun(task_id="t", run_id="r", status="succeeded",
                  output="1. 做了什么：把周报转成了 PDF\n2. 改动文件清单：无\n3. 验证结果：ok\n4. 未验证项：无")
    b = make_brief(run)
    assert b.startswith("办好了") and len(b) <= 120
    assert "周报" in b


def test_make_brief_violation_flagged():
    run = TaskRun(task_id="t", run_id="r", status="succeeded",
                  output="1. 做了什么：x\n2. 改动文件清单：无\n3. 验证结果：ok\n4. 未验证项：无",
                  warnings=("workspace_violation",))
    assert "越界" in make_brief(run)


def test_split_detail_chunks_and_overflow():
    detail = "x" * 6500
    parts = split_detail(detail)
    assert len(parts) == 1 and "详情" in parts[0]  # 超 3 条 → 按需拉取
    parts2 = split_detail("y" * 3500)
    assert len(parts2) == 2 and all(len(p) <= 2000 for p in parts2)


# ---------- 断线补报 ----------

def test_unreported_scan_from_store(store):
    store.task_run_upsert("tA", "rA", "succeeded", raw_status="completed", output="done")
    store.task_run_upsert("tB", "rB", "running")
    unfinished_before = [r["task_id"] for r in store.task_runs_unfinished()]
    assert unfinished_before == ["tB"]
    # manager 启动时扫描 unfinished（补报候选）
    mgr = _mgr(store)
    scanned = {r["task_id"] for r in mgr.unreported}
    assert "tB" in scanned and "tA" not in scanned
