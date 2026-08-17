"""M3a 时空沉浸测试：场景锁 / 通道互斥 / 仲裁器最小版 / 心跳。"""
import pytest

from veranima.core.agent import Agent
from veranima.core.ambient import Arbitrator, ChannelActivityTracker, SceneLock
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore


@pytest.fixture
def agent(tmp_path):
    mem = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, llm_config={})
    card = CharacterCard(name="测试", first_mes="hi")
    return card, mem


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


# ---------- 心跳（agent.heartbeat） ----------

def test_heartbeat_requires_closed_conversation(agent):
    """对话闭合（最后一条 assistant）才触发心跳；用户刚说话不触发。"""
    from veranima.core.agent import Agent
    card, memory = agent
    llm = FakeHeartbeatLLM()
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(),
              config={"chat": {"proactive_message_prob": 0.0}})
    # 对话未闭合：最后一条是 user
    memory.store_message("user", "你好", 80, "平静")
    assert a.heartbeat() == ""
    # 闭合：补一条 assistant
    memory.store_message("assistant", "你好呀", 80, "平静")
    a.arbitrator._cooldown.clear()
    out = a.heartbeat()
    assert out != ""
    assert llm.calls > 0


def test_heartbeat_blocked_by_scene(agent):
    from veranima.core.agent import Agent
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeHeartbeatLLM(), state=AgentState(),
              config={"chat": {"proactive_message_prob": 0.0}})
    memory.store_message("user", "你好", 80, "平静")
    memory.store_message("assistant", "你好呀", 80, "平静")
    a.scene_lock.note("我去看个电影了")
    assert a.heartbeat() == ""  # busy 场景拦截


class FakeHeartbeatLLM:
    """心跳测试用假 LLM：loaded=True，chat 返回固定文本。"""

    def __init__(self):
        self.calls = 0

    def is_model_loaded(self):
        return True

    def chat(self, messages, **kwargs):
        self.calls += 1
        return "（刚在整理聊天记录）上次你说的那事，后来怎么样了？"
