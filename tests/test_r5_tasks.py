"""R5 外部任务协作测试（R5_SPEC 6）。

覆盖：闲聊不转交、缺路径追问、validate_workorder 校验、生命周期状态、
bridge fake subprocess（取消/超时/失败/截断）。
"""
from __future__ import annotations

import threading

import pytest

from veranima.core.workorder import (
    WorkOrder,
    build_workorder,
    is_task_request,
    validate_workorder,
)
from veranima.tools import dsh_bridge


def test_chat_not_task():
    """R5_SPEC 1：闲聊不触发转交。"""
    assert not is_task_request("我想聊聊周报")
    assert not is_task_request("今天天气怎么样")
    assert is_task_request("帮我把这个文档转成 PDF")
    assert is_task_request("把这个文件整理一下")


def test_build_workorder_missing_path_asks():
    """R5_SPEC 1/2：缺路径必须追问。"""
    wo = build_workorder("帮我把这个文档转成 PDF")
    assert "来源路径" in wo.needs_clarification
    assert "目标格式" not in wo.needs_clarification  # PDF 已给出
    assert clarification_text(wo)


def clarification_text(wo):
    from veranima.core.workorder import clarification_question
    return clarification_question(wo)


def test_validate_workorder_ok(tmp_path):
    src = tmp_path / "a.docx"
    src.write_text("x")
    wo = WorkOrder(
        goal="把文档转成 PDF", source=str(src), task_type="文档处理",
        task_id="t1", constraints={"deadline": "2099-01-01T00:00:00"},
    )
    assert validate_workorder(wo) == []


def test_validate_workorder_rejects(tmp_path):
    wo = WorkOrder(goal="", task_type="非法类型")
    issues = validate_workorder(wo)
    assert any("goal" in i for i in issues)
    assert any("task_type" in i for i in issues)
    # 路径不存在
    wo2 = WorkOrder(goal="整理文件", source="C:/nope/missing.docx", task_type="文档处理")
    assert any("不存在" in i for i in validate_workorder(wo2))
    # 危险操作
    wo3 = WorkOrder(goal="删除 C:/x 目录", task_type="系统操作")
    assert any("危险" in i for i in validate_workorder(wo3))
    # 追问缺失来源
    wo4 = WorkOrder(goal="把文档转成 PDF", needs_clarification=["来源路径"], task_type="文档处理")
    assert any("来源路径" in i for i in validate_workorder(wo4))


def test_workorder_lifecycle_status():
    wo = WorkOrder(goal="x", task_id="t1")
    assert wo.status == "draft"
    wo.status = "confirmed"
    wo.status = "running"
    wo.status = "succeeded"
    assert wo.status == "succeeded"


def test_run_dsh_task_success(monkeypatch):
    """fake subprocess：成功路径 + 输出截断 + status。"""
    import subprocess as sp

    class FakeProc:
        returncode = 0
        stdout = None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(dsh_bridge, "dsh_available", lambda: True)
    monkeypatch.setattr(sp, "Popen", lambda *a, **k: FakeProc())
    r = dsh_bridge.run_dsh_task({"task_id": "t1", "goal": "查资料"}, output_max_chars=10)
    assert r["status"] == "succeeded"
    assert r["ok"] is True
    assert "截断" in r["output"] or len(r["output"]) <= 10


def test_run_dsh_task_cancel(monkeypatch):
    import subprocess as sp

    class HangProc:
        returncode = None

        def wait(self, timeout=None):
            if self._killed:
                return -9
            raise sp.TimeoutExpired("x", 1)

        def kill(self):
            self._killed = True

        def __init__(self):
            self._killed = False

    monkeypatch.setattr(dsh_bridge, "dsh_available", lambda: True)
    monkeypatch.setattr(sp, "Popen", lambda *a, **k: HangProc())
    ev = threading.Event()
    ev.set()  # 立即取消
    r = dsh_bridge.run_dsh_task({"task_id": "t1", "goal": "x"}, ev, timeout=30)
    assert r["status"] == "cancelled"
    assert r["ok"] is False


def test_run_dsh_task_timeout(monkeypatch):
    import subprocess as sp
    import time

    class HangProc:
        returncode = None

        def __init__(self):
            self._killed = False

        def wait(self, timeout=None):
            if self._killed:
                return -9
            raise sp.TimeoutExpired("x", 1)

        def kill(self):
            self._killed = True

    monkeypatch.setattr(dsh_bridge, "dsh_available", lambda: True)
    monkeypatch.setattr(sp, "Popen", lambda *a, **k: HangProc())
    r = dsh_bridge.run_dsh_task({"task_id": "t1", "goal": "x"}, None, timeout=1)
    assert r["status"] == "timed_out"


def test_run_dsh_task_failed(monkeypatch):
    import subprocess as sp

    class FailProc:
        returncode = 2
        stdout = None

        def wait(self, timeout=None):
            return 2

    monkeypatch.setattr(dsh_bridge, "dsh_available", lambda: True)
    monkeypatch.setattr(sp, "Popen", lambda *a, **k: FailProc())
    r = dsh_bridge.run_dsh_task({"task_id": "t1", "goal": "x"})
    assert r["status"] == "failed"
    assert r["ok"] is False


def test_run_dsh_task_not_installed():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(dsh_bridge, "dsh_available", lambda: False)
    r = dsh_bridge.run_dsh_task({"task_id": "t1", "goal": "x"})
    monkeypatch.undo()
    assert r["ok"] is False
    assert "未安装" in r["output"]
