"""OneBot v11（NapCatQQ）适配器测试：白名单 / 文本提取 / 消息管线 / 离线思考。

不连真实 WS：替换 bot 为 FakeBot，直接调用 adapter._handle_private。
"""

from __future__ import annotations

import asyncio
import datetime
import random
import time

import pytest
from aiocqhttp import Message

from veranima.adapters.qq import OfflineThinkTimer, QQAdapter
from veranima.core.agent import Agent, TurnResult
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    def __init__(self, reply="你好呀"):
        self.reply = reply
        self.calls = 0

    def chat(self, messages, **kw):
        self.calls += 1
        return self.reply

    def is_model_loaded(self):
        return True

    low_energy_max_tokens = 256


class FakeBot:
    """替换 adapter.bot：记录 send/send_private_msg 调用。"""

    def __init__(self):
        self.sent: list = []

    async def send(self, event, message, **kw):
        self.sent.append(("send", str(message)))

    async def send_private_msg(self, **kw):
        self.sent.append(("private", kw))


@pytest.fixture
def agent(tmp_path):
    card = CharacterCard(name="小V", description="测试", personality="温柔")
    return Agent(
        card=card,
        memory=MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed()),
        llm=FakeLLM(),
        state=AgentState(),
        config={"chat": {"proactive_message_prob": 0.0}},
    )


@pytest.fixture
def adapter(agent):
    # quiet_hours=None：默认测试不关心静默时段（时段判定单独测）
    a = QQAdapter(agent, allowed_qq=["10001"], quiet_hours=None)
    a.bot = FakeBot()
    return a


def run(coro):
    return asyncio.run(coro)


# ---------- 白名单 ----------

def test_non_whitelist_ignored(adapter, agent):
    """非白名单 QQ 消息：不调用 agent.handle，不发送。"""
    orig = agent.handle

    def fail(text):
        raise AssertionError("agent.handle must not be called")

    agent.handle = fail
    run(adapter._handle_private({"user_id": 99999, "message_type": "private", "message": "你好"}))
    assert adapter.bot.sent == []
    agent.handle = orig


def test_whitelist_processed(adapter, agent):
    """白名单 QQ：handle 被调用，回复发送。"""
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "在吗"}))
    assert adapter.bot.sent == [("send", "你好呀")]


def test_whitelist_str_and_int(agent):
    """白名单支持字符串/数字混写。"""
    a = QQAdapter(agent, allowed_qq=[10001, "20002"])
    assert a.allowed == {"10001", "20002"}


# ---------- 文本提取 ----------

def test_plain_text_from_message_object():
    """CQ 码剥离：图片段被忽略，文本保留。"""
    msg = Message("[CQ:image,file=ab.png]你好呀[CQ:face,id=1]")
    assert QQAdapter._plain_text({"message": msg}) == "你好呀"


def test_plain_text_from_raw_string():
    assert QQAdapter._plain_text({"message": " 直接文本 "}) == "直接文本"


def test_image_only_message_skipped(adapter, agent):
    """纯图片消息（无文本）：跳过，不调用 handle。"""
    def fail(text):
        raise AssertionError("agent.handle must not be called")

    agent.handle = fail
    run(adapter._handle_private(
        {"user_id": 10001, "message_type": "private", "message": Message("[CQ:image,file=a.png]")}
    ))
    assert adapter.bot.sent == []


# ---------- 消息管线 ----------

def test_proactive_msg_deferred_not_sent_immediately(adapter, agent):
    """主动消息不立即发送：进 pending，等待对话静默（防双连发）。"""
    agent.handle = lambda text: TurnResult(
        reply="普通回复", proactive_msg="（主动）对了，你上次说的事",
        energy=80, mood="平静",
    )
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "hi"}))
    assert adapter.bot.sent == [("send", "普通回复")]  # 只有回复，主动消息未发
    assert adapter._pending_proactive == "（主动）对了，你上次说的事"


def test_pending_proactive_flushed_after_silence(adapter, agent, monkeypatch):
    """对话静默满 proactive_delay_minutes 后，后台 tick 发送 pending 主动消息。"""
    adapter._pending_proactive = "（主动）对了，你上次说的事"
    adapter._last_user_activity = time.time() - adapter.proactive_delay_minutes * 60 - 10
    loop, t = _run_loop_thread()
    try:
        adapter._flush_pending_proactive(loop)
    finally:
        _stop_loop_thread(loop, t)
    assert adapter.bot.sent[0][0] == "private"
    assert "（主动）对了，你上次说的事" in adapter.bot.sent[0][1]["message"]
    assert adapter._pending_proactive is None  # 发送后清空


def test_pending_proactive_not_flushed_before_silence(adapter, agent):
    """对话还没冷下来：pending 不发送。"""
    adapter._pending_proactive = "（主动）等等再说"
    adapter._last_user_activity = time.time() - 10  # 10 秒前刚发过
    loop, t = _run_loop_thread()
    try:
        adapter._flush_pending_proactive(loop)
    finally:
        _stop_loop_thread(loop, t)
    assert adapter.bot.sent == []
    assert adapter._pending_proactive == "（主动）等等再说"  # 保留等待


def test_pending_proactive_discarded_on_new_user_message(adapter, agent):
    """用户又发消息：旧 pending 作废，不插话。"""
    adapter._pending_proactive = "（主动）旧话题"
    agent.handle = lambda text: TurnResult(reply="新回复", energy=80, mood="平静")
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "hi"}))
    assert adapter.bot.sent == [("send", "新回复")]
    assert adapter._pending_proactive is None  # 作废


def test_empty_reply_not_sent(adapter, agent):
    """回复为空（空白输入）不发送。"""
    agent.handle = lambda text: TurnResult(reply="", energy=80, mood="平静")
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "hi"}))
    assert adapter.bot.sent == []


def test_messages_serialized(adapter, agent):
    """并发消息串行处理（asyncio.Lock），handle 无并发调用。"""
    import threading
    active = []
    seen = []

    def slow_handle(text):
        active.append(1)
        assert len(active) == 1, "handle 并发调用！"
        import time
        time.sleep(0.05)
        active.pop()
        seen.append(text)
        return TurnResult(reply=f"reply:{text}", energy=80, mood="平静")

    agent.handle = slow_handle

    async def concurrent():
        ev = {"user_id": 10001, "message_type": "private", "message": "m1"}
        await asyncio.gather(
            adapter._handle_private(dict(ev, message="m1")),
            adapter._handle_private(dict(ev, message="m2")),
            adapter._handle_private(dict(ev, message="m3")),
        )

    run(concurrent())
    assert seen == ["m1", "m2", "m3"]  # 顺序保持


# ---------- 8.7.4 离线思考 ----------

def test_offline_not_due_before_silence():
    t = OfflineThinkTimer(silence_minutes=30, probability=1.0, rand=random.Random(0))
    assert not t.due(now=1000.0, last_activity=None)          # 从未对话
    assert not t.due(now=1000.0, last_activity=1000.0 - 29 * 60)  # 未满静默窗口


def test_offline_due_after_silence():
    t = OfflineThinkTimer(silence_minutes=30, probability=1.0, rand=random.Random(0))
    assert t.due(now=1000.0, last_activity=1000.0 - 31 * 60)


def test_offline_probability_gate():
    t = OfflineThinkTimer(silence_minutes=30, probability=0.0, rand=random.Random(0))
    assert not t.due(now=1000.0, last_activity=1000.0 - 31 * 60)


def test_offline_window_dedup():
    """触发后需再静默满一个窗口才可能再触发（防刷屏）。"""
    t = OfflineThinkTimer(silence_minutes=30, probability=1.0, rand=random.Random(0))
    assert t.due(now=1000.0, last_activity=1000.0 - 60 * 60)       # 触发
    assert not t.due(now=1010.0, last_activity=1000.0 - 60 * 60)   # 窗口内不重复
    assert t.due(now=1000.0 + 31 * 60, last_activity=1000.0 - 60 * 60)  # 再满窗口可再触发


def test_offline_max_per_day_limit():
    """每日上限：当天触发满 max_per_day 后不再触发（防整夜轰炸）。"""
    t = OfflineThinkTimer(
        silence_minutes=30, probability=1.0, max_per_day=2, rand=random.Random(0)
    )
    # 基准 now 需距 last_activity 超过静默窗口（30 分钟 = 1800s）
    base = 2000.0
    assert t.due(now=base, last_activity=0.0)                       # 第 1 次
    assert t.due(now=base + 31 * 60, last_activity=0.0)             # 第 2 次
    assert not t.due(now=base + 62 * 60, last_activity=0.0)         # 当日额度用尽


def test_offline_max_per_day_resets_next_day():
    """跨天重置每日计数。"""
    t = OfflineThinkTimer(
        silence_minutes=30, probability=1.0, max_per_day=1, rand=random.Random(0)
    )
    base = 2000.0
    assert t.due(now=base, last_activity=0.0)
    assert not t.due(now=base + 31 * 60, last_activity=0.0)         # 同日已满
    assert t.due(now=base + 24 * 3600, last_activity=0.0)           # 次日重置


def test_offline_probability_grows_on_miss():
    """渴望度积累：未命中时概率增长，命中后重置回基础值。"""
    class SeqRand:
        def __init__(self, vals):
            self._vals = list(vals)
        def random(self):
            return self._vals.pop(0)

    t = OfflineThinkTimer(
        silence_minutes=30, probability=0.3, growth_factor=0.1, max_probability=0.9,
        rand=SeqRand([0.8, 0.5, 0.9, 0.2]),
    )
    # 窗口1：0.8 >= 0.3 → miss，概率 0.3 → 0.4
    assert not t.due(now=2000.0, last_activity=0.0)
    assert abs(t.probability - 0.4) < 1e-9
    # 窗口2：0.5 >= 0.4 → miss，概率 0.4 → 0.5
    assert not t.due(now=2000.0 + 31 * 60, last_activity=0.0)
    assert abs(t.probability - 0.5) < 1e-9
    # 窗口3：0.9 >= 0.5 → miss，概率 0.5 → 0.6
    assert not t.due(now=2000.0 + 62 * 60, last_activity=0.0)
    assert abs(t.probability - 0.6) < 1e-9
    # 窗口4：0.2 < 0.6 → hit，概率重置回 0.3
    assert t.due(now=2000.0 + 93 * 60, last_activity=0.0)
    assert abs(t.probability - 0.3) < 1e-9


def test_offline_probability_capped_at_max():
    """渴望度积累封顶：不超过 max_probability。"""
    class SeqRand:
        def __init__(self, vals):
            self._vals = list(vals)
        def random(self):
            return self._vals.pop(0)

    t = OfflineThinkTimer(
        silence_minutes=30, probability=0.85, growth_factor=0.1, max_probability=0.9,
        rand=SeqRand([0.99, 0.99, 0.99]),
    )
    for i in range(3):
        assert not t.due(now=2000.0 + i * 31 * 60, last_activity=0.0)
    assert abs(t.probability - 0.9) < 1e-9  # 0.85+0.1 → 封顶 0.9


def test_offline_probability_zero_never_grows():
    """概率 0 = 关闭：永不触发，也不增长。"""
    t = OfflineThinkTimer(silence_minutes=30, probability=0.0, rand=random.Random(0))
    for i in range(5):
        assert not t.due(now=2000.0 + i * 31 * 60, last_activity=0.0)
    assert t.probability == 0.0


def test_quiet_hours_judgment(adapter):
    """静默时段判定：跨午夜区间（23:00-08:00）。"""
    adapter.quiet_hours = (23, 8)
    assert adapter._in_quiet_hours(datetime.datetime(2026, 8, 3, 23, 30))
    assert adapter._in_quiet_hours(datetime.datetime(2026, 8, 4, 3, 0))
    assert adapter._in_quiet_hours(datetime.datetime(2026, 8, 4, 7, 59))
    assert not adapter._in_quiet_hours(datetime.datetime(2026, 8, 3, 22, 59))
    assert not adapter._in_quiet_hours(datetime.datetime(2026, 8, 4, 8, 0))
    adapter.quiet_hours = None
    assert not adapter._in_quiet_hours(datetime.datetime(2026, 8, 4, 3, 0))


def test_offline_think_skipped_in_quiet_hours(adapter, agent, monkeypatch):
    """静默时段内 _tick_offline_think 直接短路，不发消息。"""
    agent.memory.store_message("user", "下周要去面试了", 80, "平静")
    adapter._last_user_activity = 0.0
    adapter.offline = OfflineThinkTimer(
        silence_minutes=1, probability=1.0, rand=random.Random(0)
    )
    monkeypatch.setattr(adapter, "_in_quiet_hours", lambda now=None: True)
    loop, t = _run_loop_thread()
    try:
        adapter._tick_offline_think(loop)
    finally:
        _stop_loop_thread(loop, t)
    assert adapter.bot.sent == []


def _run_loop_thread():
    """启动一个真实运行的事件循环线程（run_coroutine_threadsafe 需要）。"""
    import threading

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    return loop, t


def _stop_loop_thread(loop, t):
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=5)


def test_offline_think_generates_late_reply(adapter, agent):
    """离线思考 tick：静默命中 → late_reply → 发送给白名单 QQ。"""
    agent.memory.store_message("user", "下周要去面试了", 80, "平静")
    adapter._last_user_activity = 0.0  # 很久没活动
    adapter.offline = OfflineThinkTimer(
        silence_minutes=1, probability=1.0, rand=random.Random(0)
    )
    loop, t = _run_loop_thread()
    try:
        adapter._tick_offline_think(loop)
    finally:
        _stop_loop_thread(loop, t)
    assert adapter.bot.sent, "应发送离线思考消息"
    assert adapter.bot.sent[0][0] == "private"
    assert adapter.bot.sent[0][1]["user_id"] == 10001
    assert adapter.bot.sent[0][1]["message"]


def test_offline_think_skips_when_model_unloaded(adapter, agent):
    """模型未加载：离线思考不发送（不打扰）。"""
    agent.memory.store_message("user", "下周要去面试了", 80, "平静")
    agent.llm = FakeLLM()
    agent.llm.is_model_loaded = lambda: False
    adapter._last_user_activity = 0.0
    adapter.offline = OfflineThinkTimer(
        silence_minutes=1, probability=1.0, rand=random.Random(0)
    )
    loop, t = _run_loop_thread()
    try:
        adapter._tick_offline_think(loop)
    finally:
        _stop_loop_thread(loop, t)
    assert adapter.bot.sent == []
