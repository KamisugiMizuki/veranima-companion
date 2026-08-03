"""主动触发测试：定时问候 / 节庆与纪念日检查（每日去重）。"""

from __future__ import annotations

import datetime

import pytest

from veranima.core.proactive import GreetingScheduler, OccasionChecker
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


@pytest.fixture
def store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())


# ---------- 定时问候 ----------

def test_greeting_morning():
    g = GreetingScheduler()
    slot = g.due_greeting(now=datetime.datetime(2026, 8, 3, 8, 0))
    assert slot == "morning"
    assert "早" in g.greeting_text(slot)


def test_greeting_noon_evening():
    g = GreetingScheduler()
    assert g.due_greeting(now=datetime.datetime(2026, 8, 3, 12, 0)) == "noon"
    assert g.due_greeting(now=datetime.datetime(2026, 8, 3, 20, 0)) == "evening"


def test_greeting_off_window():
    g = GreetingScheduler()
    assert g.due_greeting(now=datetime.datetime(2026, 8, 3, 3, 0)) is None  # 深夜不问候
    assert g.due_greeting(now=datetime.datetime(2026, 8, 3, 15, 0)) is None  # 下午无问候


def test_greeting_daily_dedup():
    g = GreetingScheduler()
    assert g.due_greeting(now=datetime.datetime(2026, 8, 3, 8, 0)) == "morning"
    assert g.due_greeting(now=datetime.datetime(2026, 8, 3, 9, 0)) is None  # 同日同段不重复
    assert g.due_greeting(now=datetime.datetime(2026, 8, 4, 8, 0)) == "morning"  # 次日重新允许


# ---------- 节庆与纪念日 ----------

def test_fixed_holiday():
    o = OccasionChecker()
    hit = o.due_occasion(memory=None, now=datetime.datetime(2026, 1, 1))
    assert hit and "元旦" in hit
    # 同日不重复
    assert o.due_occasion(memory=None, now=datetime.datetime(2026, 1, 1)) is None


def test_anniversary_from_memory(store):
    store.store("semantic", "我的生日是3月14日", importance=0.9, confidence=0.8)
    o = OccasionChecker()
    hit = o.due_occasion(store, now=datetime.datetime(2026, 3, 14))
    assert hit and "生日" in hit


def test_anniversary_no_date_no_hit(store):
    store.store("semantic", "我的生日快到了", importance=0.9, confidence=0.8)  # 无具体日期
    o = OccasionChecker()
    assert o.due_occasion(store, now=datetime.datetime(2026, 3, 14)) is None


def test_occasion_reaction_birthday():
    r = OccasionChecker.occasion_reaction("今天好像是你的特别日子：我的生日是3月14日")
    assert "生日" in r
    assert "谢谢你" in r
