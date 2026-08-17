"""M3a 时空沉浸测试：场景锁 / 通道互斥 / 仲裁器最小版。"""
import pytest

from veranima.core.ambient import Arbitrator, ChannelActivityTracker, SceneLock


# ---------- 场景锁 ----------

def test_scene_enter_busy_and_shorten():
    lock = SceneLock(now=1000.0)
    assert lock.note("我去看个电影了") == "busy"
    assert lock.current() == "busy"
    assert lock.max_len() == 40  # busy 限制回复长度
    assert lock.reply_delay() == 30


def test_scene_enter_away_and_delay():
    lock = SceneLock(now=1000.0)
    assert lock.note("我睡觉去了") == "away"
    assert lock.reply_delay() == 300  # away 长延迟


def test_scene_exit_on_return():
    lock = SceneLock(now=1000.0)
    lock.note("我去看个电影了")
    assert lock.note("看完了，回来了") == "normal"
    assert lock.current() == "normal"
    assert lock.max_len() is None


def test_scene_auto_reset_after_2h():
    lock = SceneLock(now=1000.0)
    lock.note("我去看个电影了")
    lock._now = 1000.0 + 3 * 3600  # 3h 后
    assert lock.current() == "normal"


def test_scene_busy_touch_extends():
    lock = SceneLock(now=1000.0)
    lock.note("我去看个电影了")
    lock.note("这电影好烂")  # busy 中普通消息 → 触碰
    lock._now = 1000.0 + 1 * 3600  # 1h 后仍在窗口
    assert lock.current() == "busy"


# ---------- 通道互斥 ----------

def test_channel_blocking():
    t = ChannelActivityTracker(now=1000.0)
    t.touch("qq")
    assert t.active("qq") is True
    assert t.blocking("desktop") is True   # 桌宠被 QQ 活跃阻塞
    assert t.blocking("qq") is False       # 自身不阻塞
    t._now = 1000.0 + 31 * 60  # 31min 后窗口过期
    assert t.active("qq") is False
    assert t.blocking("desktop") is False


# ---------- 仲裁器 ----------

def test_arbitrator_blocks_in_scene():
    a = Arbitrator(now=1000.0)
    assert a.request("idle", scene="busy") is False
    assert a.request("idle", scene="normal") is True


def test_arbitrator_blocks_other_channel():
    a = Arbitrator(now=1000.0)
    assert a.request("idle", other_channel_active=True) is False


def test_arbitrator_cooldown_and_daily_cap():
    a = Arbitrator(now=1000.0)
    assert a.request("idle") is True
    a.commit("idle")
    assert a.request("idle") is False  # 冷却中
    a._now = 1000.0 + 31 * 60  # 冷却过后
    assert a.request("idle") is True
    a.commit("idle")
    # 日上限：直接塞满
    a._today_count = a.MAX_PER_DAY
    assert a.request("fatigue") is False


def test_arbitrator_priority_sort():
    a = Arbitrator()
    assert a.sort(["idle", "conflict", "fatigue"]) == ["conflict", "fatigue", "idle"]
