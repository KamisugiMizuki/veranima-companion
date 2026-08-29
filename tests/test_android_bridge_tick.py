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

    def store_message(self, role, content, energy, mood, channel=""):
        self.messages.append((role, content, channel))

    def recent_proactive_feedback(self, source=None, limit=10, **kw):
        return []

    def record_proactive_feedback(self, **kw):
        self.fb = kw


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
        self.state = type("St", (), {"energy": 0.7, "mood": 0.2})()

    def record_proactive_message(self, text, channel=""):
        self.memory.store_message("assistant", text, 0.7, 0.2, channel=channel)


def test_offline_think_timer_window_dedup(bridge):
    """bridge 用的 OfflineThinkTimer 语义核对：一个静默窗口只掷一次骰。"""
    from veranima.core.proactive import OfflineThinkTimer

    t = OfflineThinkTimer(silence_minutes=30, probability=1.0, rand=random.Random(0))
    t0 = 1_700_000_000.0
    assert t.due(t0 + 31 * 60, t0) is True     # 静默满窗口 → 命中
    assert t.due(t0 + 32 * 60, t0) is False    # 同窗口内再来 → 去重
    assert t.due(t0 + 62 * 60, t0) is True     # 新窗口 → 可再触发
