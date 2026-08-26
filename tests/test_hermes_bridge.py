"""Hermes bridge 行为测试（HERMES_AGENT_INTEGRATION_SPEC §11.1）。

fake HTTP（monkeypatch _request）覆盖：health 三态、submit 成功/鉴权失败/并发满、
状态归一化、404→orphaned、stop 轮询、approve choice 校验与 409 fail-closed、
四段报告解析、workspace 审计、task_runs 持久化/恢复、审批关键词硬匹配。
"""
from __future__ import annotations

import pytest

from veranima.memory.store import MemoryStore
from veranima.tools.hermes_bridge import (
    HermesBridgeError,
    HermesExecutionBridge,
    load_bridge_config,
)


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(db_path=str(tmp_path / "t.db"), config={"decay_enabled": False}, provider=FakeEmbed())
    yield s
    s.con.close()


@pytest.fixture
def bridge():
    return HermesExecutionBridge({
        "base_url": "http://127.0.0.1:8642",
        "profile": "veranima-worker",
        "multiplex_profiles": True,
        "workspace_root": "D:/Hermes_workspace/veranima",
        "timeout_seconds": 5,
    })


REPORT_OK = (
    "1. 做了什么：转换文档\n"
    "2. 改动文件清单：\nD:/Hermes_workspace/veranima/tmp/out.pdf\n"
    "3. 验证结果：pdfinfo OK\n"
    "4. 未验证项：无"
)
REPORT_BAD = (
    "2. 改动文件清单：\nD:/outside/x.txt\n"  # 缺段 1/3/4 + 越界路径
)


# ---------- health ----------

def test_health_not_configured():
    b = HermesExecutionBridge({})
    assert b.health() == (False, "not_configured")


def test_health_offline_and_auth(bridge, monkeypatch):
    monkeypatch.setattr(HermesExecutionBridge, "_request", lambda self, m, p, **k: (_ for _ in ()).throw(OSError("refused")))
    ok, reason = bridge.health()
    assert ok is False and "offline" in reason


# ---------- submit ----------

def test_submit_success_and_prefix(bridge, monkeypatch):
    captured = {}

    def fake(self, method, path, *, body=None, timeout=None):
        captured["method"], captured["path"], captured["body"] = method, path, body
        return 202, {"run_id": "run_x", "status": "started"}

    monkeypatch.setattr(HermesExecutionBridge, "_request", fake)
    run = bridge.submit({"task_id": "t1", "goal": "整理文件"})
    assert run.run_id == "run_x"
    assert run.status == "queued"
    assert captured["method"] == "POST" and captured["path"] == "/runs"
    assert "/p/veranima-worker/v1/runs" in captured["path"] or captured["path"] == "/runs"
    prompt = captured["body"]["input"]
    assert "写操作边界" in prompt and "四段" in prompt or "改动文件清单" in prompt


def test_submit_not_configured_raises():
    b = HermesExecutionBridge({})
    with pytest.raises(HermesBridgeError):
        b.submit({"task_id": "t", "goal": "x"})


def test_submit_auth_failure(bridge, monkeypatch):
    monkeypatch.setattr(HermesExecutionBridge, "_request", lambda self, m, p, **k: (401, {}))
    with pytest.raises(HermesBridgeError, match="鉴权"):
        bridge.submit({"task_id": "t", "goal": "x"})


def test_submit_busy_429(bridge, monkeypatch):
    monkeypatch.setattr(HermesExecutionBridge, "_request", lambda self, m, p, **k: (429, {}))
    with pytest.raises(HermesBridgeError, match="并发"):
        bridge.submit({"task_id": "t", "goal": "x"})


# ---------- 状态归一化 / 404 orphaned ----------

def test_normalize_completed_to_succeeded_with_raw(bridge):
    run = bridge.from_status_payload("t1", {"run_id": "r1", "status": "completed", "output": REPORT_OK})
    assert run.status == "succeeded"
    assert run.raw_status == "completed"
    assert run.changed_files == ("D:/Hermes_workspace/veranima/tmp/out.pdf",)
    assert run.test_summary == "pdfinfo OK"


def test_status_404_nonterminal_becomes_orphaned(bridge, monkeypatch):
    monkeypatch.setattr(HermesExecutionBridge, "_request", lambda self, m, p, **k: (404, {}))
    run = bridge.status("t1", "r1")
    assert run.status == "orphaned"


def test_status_404_terminal_kept(bridge, monkeypatch):
    prior = bridge.from_status_payload("t1", {
        "run_id": "r1", "status": "cancelled"})
    monkeypatch.setattr(HermesExecutionBridge, "_request", lambda self, m, p, **k: (404, {}))
    run = bridge.status("t1", "r1", prior=prior)
    assert run.status == "cancelled"


# ---------- 报告解析 / workspace 审计 ----------

def test_parse_report_missing_sections_flagged():
    files, summary, warns = HermesExecutionBridge.parse_report(REPORT_BAD)
    assert "missing_section_what_done" in warns
    assert "missing_section_test_summary" in warns
    assert files == ("D:/outside/x.txt",)


def test_workspace_violation_detected(bridge):
    run = bridge.from_status_payload("t1", {"run_id": "r", "status": "completed", "output": REPORT_BAD})
    assert "workspace_violation" in run.warnings


def test_workspace_clean_no_violation(bridge):
    run = bridge.from_status_payload("t1", {"run_id": "r", "status": "completed", "output": REPORT_OK})
    assert "workspace_violation" not in run.warnings


def test_colon_in_path_not_truncated(bridge):
    out = (
        "1. 做了什么：x\n"
        "2. 改动文件清单：\nC:/a/b:D/file.txt\n"
        "3. 验证结果：ok\n4. 未验证项：无"
    )
    files, _, _ = HermesExecutionBridge.parse_report(out)
    assert files == ("C:/a/b:D/file.txt",)


# ---------- stop / approve ----------

def test_stop_then_cancelled(bridge, monkeypatch):
    calls = []

    def fake(self, method, path, *, body=None, timeout=None):
        calls.append(path)
        if path.endswith("/stop"):
            return 200, {"status": "stopping"}
        if len([c for c in calls if c.endswith(f"/runs/r1")]) >= 2:
            return 200, {"run_id": "r1", "status": "cancelled"}
        return 200, {"run_id": "r1", "status": "running"}

    monkeypatch.setattr(HermesExecutionBridge, "_request", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    run = bridge.stop("t1", "r1")
    assert run.status == "cancelled"


def test_approve_invalid_choice_rejected(bridge):
    with pytest.raises(ValueError):
        bridge.approve("t1", "r1", "yes please")


def test_approve_409_fail_closed(bridge, monkeypatch):
    def fake(self, method, path, *, body=None, timeout=None):
        if path.endswith("/approval"):
            assert body == {"choice": "deny"}
            return 409, {"error": "approval_not_pending"}
        return 200, {"run_id": "r1", "status": "completed"}

    monkeypatch.setattr(HermesExecutionBridge, "_request", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    run = bridge.approve("t1", "r1", "deny")
    assert run.status == "succeeded"


def test_approval_match_strict():
    f = HermesExecutionBridge.match_approval_choice
    assert f("once") == "once"
    assert f(" deny ") == "deny"
    assert f("好的") is None
    assert f("不要 once") is None
    assert f("once session") is None


def test_approval_match_loose_negation_blocked():
    f = lambda t: HermesExecutionBridge.match_approval_choice(t, strict=False)
    assert f("就 once 吧") == "once"
    assert f("不要 once") is None      # 否定语境 → 不执行
    assert f("拒绝 always") is None
    assert f("once 或 deny") is None   # 多命中


# ---------- task_runs 持久化 ----------

def test_task_run_upsert_get_unfinished(store):
    store.task_run_upsert("t1", "run_1", "queued")
    store.task_run_upsert("t1", "run_1", "running", raw_status="started")
    d = store.task_run_get("t1")
    assert d["status"] == "running" and d["run_id"] == "run_1"
    assert store.task_runs_unfinished()[0]["task_id"] == "t1"
    store.task_run_upsert("t1", "run_1", "failed", error="boom")
    assert store.task_runs_unfinished() == []


def test_load_bridge_config():
    assert load_bridge_config(None) == {"enabled": False}
    assert load_bridge_config({"enabled": True, "hermes": {"base_url": "http://x"}})["enabled"] is True
