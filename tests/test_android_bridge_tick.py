"""安卓 bridge tick 的行为级冒烟（2026-08-29 周期功能移植验收）。

只测纯逻辑单元（OfflineThinkTimer 接线面）——tick 线程本身
不可单测（无限循环），其消费链在 MuMu 实机验收。bridge 顶层 import 仅标准库，
PC 可直接加载（veranima 段全是函数内延迟 import，正是为 APK/PC 双端可测）。
"""
import importlib.util
import random
import sys
import time
from pathlib import Path

import pytest

_BRIDGE = Path(__file__).resolve().parents[1] / "android/fuyuno/app/src/main/python/bridge.py"


@pytest.fixture(scope="module")
def bridge():
    spec = importlib.util.spec_from_file_location("fuyuno_bridge", _BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeStore:
    def __init__(self):
        self.messages = []
        self.feedback = []

    def store_message(self, role, content, energy, mood, channel=""):
        self.messages.append((role, content, channel))

    def recent_proactive_feedback(self, source=None, limit=10, **kw):
        return list(self.feedback)

    def record_proactive_feedback(self, **kw):
        self.feedback.append(kw)


class _Gate:
    def __init__(self, allow=True):
        self.allow, self.committed, self.decided = allow, [], []

    def decide(self, cand, **kw):
        self.decided.append(cand)
        return type("D", (), {"allow": self.allow})()

    def commit(self, cand):
        self.committed.append(cand)


class _FakeAgent:
    def __init__(self, gate):
        self.gate = gate
        self.scene_lock = type("S", (), {"current": lambda s: "normal"})()
        self.memory = _FakeStore()
        self.message_channel = "im"
        self.state = type("St", (), {"energy": 0.7, "mood": 0.2})()

    def record_proactive_message(self, text, channel=None):
        # 与真 Agent 同语义：不传=落 message_channel（bridge 侧不再显式传通道）
        self.memory.store_message("assistant", text, 0.7, 0.2,
                                  channel=channel or self.message_channel)


def test_sleep_summary_pending_stores_and_dedups(bridge):
    """苏醒总结=通知栏 + App 内双通道（2026-08-31 用户反馈只有通知没有应用内）：
    首次调用返回文本且落库 assistant 消息+认领去重；再次调用零输出。"""
    import datetime
    agent = _FakeAgent(_Gate())
    cycle = {"id": 7, "summary": "早，昨晚睡得还行嘛。",
             "woke_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    agent.memory.latest_closed_cycle = lambda: cycle
    bridge.boot = type("B", (), {"agent": agent})()
    assert bridge.sleep_summary_pending() == "早，昨晚睡得还行嘛。"
    assert ("assistant", "早，昨晚睡得还行嘛。", "im") in agent.memory.messages
    assert agent.memory.feedback and agent.memory.feedback[0]["candidate_id"] == "sleep_summary:7"
    assert bridge.sleep_summary_pending() == ""              # 去重：不重发
    assert len(agent.memory.messages) == 1                   # 不重存
    # 时效闸（2026-09-04 审计#4）：苏醒超 3h 的旧总结=过期不发，记账认领防夜半诈尸
    agent2 = _FakeAgent(_Gate())
    stale = {"id": 8, "summary": "醒这么早？",
             "woke_at": (datetime.datetime.now(datetime.timezone.utc)
                         - datetime.timedelta(hours=17)).isoformat()}
    agent2.memory.latest_closed_cycle = lambda: stale
    bridge.boot = type("B", (), {"agent": agent2})()
    assert bridge.sleep_summary_pending() == ""
    assert agent2.memory.feedback and agent2.memory.feedback[0]["candidate_id"] == "sleep_summary:8"
    assert not agent2.memory.messages


def test_offline_think_timer_window_dedup(bridge):
    """bridge 用的 OfflineThinkTimer 语义核对：一个静默窗口只掷一次骰。"""
    from veranima.core.proactive import OfflineThinkTimer

    t = OfflineThinkTimer(silence_minutes=30, probability=1.0, rand=random.Random(0))
    t0 = 1_700_000_000.0
    assert t.due(t0 + 31 * 60, t0) is True     # 静默满窗口 → 命中
    assert t.due(t0 + 32 * 60, t0) is False    # 同窗口内再来 → 去重
    assert t.due(t0 + 62 * 60, t0) is True     # 新窗口 → 可再触发


# ---------- Galaxy 详情页数据面（2026-09-01 UI 重构） ----------

import json as _json


def _detail_agent(tmp_path):
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.core.state import AgentState
    from veranima.memory.store import MemoryStore

    class _Emb:
        dim = 8

        def embed(self, texts):
            import hashlib
            return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]

    class _LLM:
        def chat(self, m, **k): return ""
        def is_model_loaded(self): return False

    memory = MemoryStore(db_path=str(tmp_path / "d.db"), config={}, provider=_Emb())
    a = Agent(card=CharacterCard(name="凛", first_mes=""), memory=memory,
              llm=_LLM(), state=AgentState(), config={})
    return a


def test_memory_stats_layers_and_dim(bridge, tmp_path):
    """memory_stats：分层计数/向量维度/长期短期待归档拆分正确。"""
    a = _detail_agent(tmp_path)
    a.memory.store("core_profile", "自我画像", importance=0.9)
    a.memory.store("semantic", "事实A", importance=0.7)
    a.memory.store("episodic", "事件B", importance=0.6)
    bridge.boot = type("B", (), {"agent": a})()
    out = _json.loads(bridge.memory_stats())
    assert out["ok"] and out["total"] == 3 and out["dim"] == 8
    assert out["long_term"] == 2 and out["short_term"] == 1


def test_relationship_trend_snapshots(bridge, tmp_path):
    """relationship_trend：首调落当日快照；同日重复调用覆盖不叠行；series 可读。"""
    a = _detail_agent(tmp_path)
    bridge.boot = type("B", (), {"agent": a})()
    out = _json.loads(bridge.relationship_trend())
    assert out["ok"] and out["role"] == "凛" and len(out["series"]) == 1
    out2 = _json.loads(bridge.relationship_trend())
    assert len(out2["series"]) == 1  # 同日覆盖（趋势表每天至多一行）
    dims = out2["series"][0]["dims"]
    assert "intimacy" in dims and "trust" in dims


def test_sleep_status_live_state(bridge, tmp_path):
    """sleep_status：asleep 实时态 + 已闭合周期估算（时长非负、评分 1..100）。"""
    import datetime
    a = _detail_agent(tmp_path)
    now = datetime.datetime.now(datetime.timezone.utc)
    a.memory.open_sleep_cycle((now - datetime.timedelta(hours=8)).isoformat(timespec="seconds"))
    a.memory.close_sleep_cycle(now.isoformat(timespec="seconds"))
    bridge.boot = type("B", (), {"agent": a})()
    out = _json.loads(bridge.sleep_status())
    assert out["ok"] and out["asleep"] is False
    assert out["last"]["sleep_minutes"] >= 470
    assert 1 <= out["last"]["score"] <= 100
    # 入睡报告后 → asleep=True、当前累计分钟存在
    a.state.user_asleep = True
    a.state.last_sleep_report_at = (now - datetime.timedelta(minutes=42)).isoformat(timespec="seconds")
    out2 = _json.loads(bridge.sleep_status())
    assert out2["asleep"] is True and 40 <= out2["current_minutes"] <= 45
