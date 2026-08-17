"""M1 打断决策/表达瑕疵 + M3 无缝衔接测试。"""
import pytest

from veranima.core.interrupt import InterruptDecider, TopicFrequency


@pytest.fixture
def agent(tmp_path):
    from veranima.core.character import CharacterCard
    from veranima.memory.store import MemoryStore
    mem = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, llm_config={})
    card = CharacterCard(name="测试", first_mes="hi")
    return card, mem


# ---------- M1 打断决策（DESIGN 4.5） ----------

def test_topic_frequency_counts():
    """话题指纹：重复提及同一话题累计计数。"""
    tf = TopicFrequency(now=1000.0)
    assert tf.note("我昨天打游戏打到凌晨") == 1
    assert tf.note("我又去打游戏了，还是那个副本") == 2  # 共享「打游戏」3-gram
    assert tf.note("今天天气不错") == 1  # 新话题
    assert tf.count("我又去打游戏了") >= 2  # 共享「打游戏」3-gram


def test_interrupt_l0_no_interrupt():
    """第 1-2 次提及不打断。"""
    d = InterruptDecider(now=0.0)
    assert d.decide(0, prob=0.1) == 0
    assert d.decide(1, prob=0.1) == 0  # 第 2 次
    assert d.decide(2, prob=0.1) == 0  # 第 3 次前


def test_interrupt_l1_probability():
    """第 3 次 → L1（概率 50% 命中）。"""
    d = InterruptDecider(now=0.0)
    assert d.decide(3, prob=0.1) == 1  # 低概率 → 命中
    assert d.decide(3, prob=0.9) == 0  # 高概率 → 未命中


def test_interrupt_l2():
    """第 4 次 → L2（概率 30% 命中）。"""
    d = InterruptDecider(now=0.0)
    assert d.decide(4, prob=0.05) == 2  # 低概率 → 命中
    assert d.decide(4, prob=0.9) == 0
    assert d.decide(6, prob=0.9) == 2  # 更频繁恒 L2


def test_interrupt_negative_feedback_cooldown():
    """负面反馈 → 30min 冷却 + 阈值提高一级。"""
    d = InterruptDecider(now=1000.0)
    d.note_negative()
    assert d.decide(4, prob=0.01) == 0  # 冷却中不打断
    d._now = 1000.0 + 1800.0  # 冷却结束
    # boost 后：第 3 次（count=3）→ n=0 → 不打断（阈值提高一级）
    assert d.decide(3, prob=0.01) == 0
    # 第 4 次（count=4）→ n=1 → L1 概率判定
    assert d.decide(4, prob=0.01) == 1


def test_interrupt_prompt_content():
    """L1/L2 指令含收尾协议（新出口）。"""
    from veranima.core.agent import _interrupt_prompt
    assert "轻推" in _interrupt_prompt(1)
    assert "转移" in _interrupt_prompt(2)
    assert "新" in _interrupt_prompt(2)


# ---------- M1 表达瑕疵（DESIGN 4.9） ----------

def test_withdraw_skipped_high_energy():
    """高精力/高确信 → 不撤回。"""
    from veranima.core.agent import _maybe_withdraw
    class S: energy = 80; confidence = 0.9
    assert _maybe_withdraw("今天天气不错，适合出门走走看看", S(), 0.01) == "今天天气不错，适合出门走走看看"


def test_withdraw_low_energy_probability():
    """低精力 + 长回复 + 概率命中 → 撤回追加。"""
    from veranima.core.agent import _maybe_withdraw
    class S: energy = 20; confidence = 0.9
    reply = "我觉得这件事应该从长计议，先看看数据再说，不能急着下结论"
    out = _maybe_withdraw(reply, S(), 0.05)
    assert "撤回" in out
    assert out.startswith(reply)


def test_withdraw_short_reply_skip():
    """短回复（无具体细节）不触发。"""
    from veranima.core.agent import _maybe_withdraw
    class S: energy = 10; confidence = 0.5
    assert _maybe_withdraw("嗯", S(), 0.01) == "嗯"


# ---------- M3 无缝衔接（DESIGN 4.8） ----------

def test_seamless_greeting_uses_last_user_msg(agent, monkeypatch):
    """衔接语引用最近用户消息（跨通道共享历史）。"""
    from veranima.core.agent import Agent
    from veranima.core.state import AgentState
    card, memory = agent
    memory.store_message("user", "明天要出差去上海了，好烦", 70, "neutral")
    a = Agent(card=card, memory=memory, llm=None, state=AgentState(), config={})
    a.state.energy = 80
    monkeypatch.setattr(a, "_short_task", lambda task, max_tokens=120: "你刚才说明天要去上海？一路顺风。")
    msg = a.seamless_greeting()
    assert "上海" in msg
    assert len(a._history) == 2  # 恢复的 user 消息 + 衔接语


def test_seamless_greeting_empty_history(agent):
    """无历史 → 返回空（不发言）。"""
    from veranima.core.agent import Agent
    from veranima.core.state import AgentState
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=None, state=AgentState(), config={})
    a.state.energy = 80
    assert a.seamless_greeting() == ""


def test_pet_server_presence_tick(agent, monkeypatch):
    """absent→present 转变触发衔接语（tick_presence）。"""
    import asyncio
    from veranima.core.agent import Agent
    from veranima.core.state import AgentState
    from veranima.pet_server import PetServer
    card, memory = agent
    memory.store_message("user", "刚才说的那个方案你觉得怎么样", 70, "neutral")
    a = Agent(card=card, memory=memory, llm=None, state=AgentState(), config={})
    a.state.energy = 80
    monkeypatch.setattr(a, "_short_task", lambda task, max_tokens=120: "那个方案啊，我觉得可行")

    import veranima.core.presence as presence_mod
    monkeypatch.setattr(presence_mod, "presence", lambda: False)
    srv = PetServer()
    srv.connect_agent(a)
    # 初始在场 → 无转变不触发
    assert asyncio.run(srv.tick_presence()) is False
    # 用户回来 → 触发衔接语（mock speak 异步）
    sent = []
    async def fake_speak(text, tags=None):
        sent.append(text)
        return True
    monkeypatch.setattr(srv, "speak", fake_speak)
    monkeypatch.setattr(presence_mod, "presence", lambda: True)
    assert asyncio.run(srv.tick_presence()) is True
    assert len(sent) == 1


# ---------- M1 可逆性 + M3 TTS 打断 ----------

def test_clarification_detection():
    """追问检测：细节追问词命中。"""
    from veranima.core.prompts import is_clarification
    assert is_clarification("那到底是什么时候说的？") is True
    assert is_clarification("具体是几点来着") is True
    assert is_clarification("今天天气不错") is False


def test_format_memory_clarification_gives_exact():
    """追问时低确信记忆不模糊化（M1_SPEC 2.2 可逆性）。"""
    from veranima.core.prompts import format_memory_line
    from veranima.memory.store import MemoryEntry
    e = MemoryEntry(id=1, layer="episodic", content="三天前下午三点在星巴克见面", importance=0.5,
                    confidence=0.5, provenance="test", version=1, strength=0.5,
                    category=None, meta={}, created_at=0, updated_at=0)
    fuzzy = format_memory_line(e)
    exact = format_memory_line(e, clarification=True)
    assert "那阵子" in fuzzy  # 模糊化生效（3 天 → 那阵子）
    assert "三天前下午三点在星巴克见面" in exact  # 追问给精确值
    assert "细节全糊了" not in exact


def test_tts_interrupt_on_new_message(monkeypatch):
    """新互动（poke/stream_talk）→ 先 stop_speak（M3 3.2 TTS 打断）。"""
    import asyncio
    from veranima.pet_server import PetServer
    srv = PetServer()
    stopped = []
    async def fake_stop():
        stopped.append(1)
        return True
    monkeypatch.setattr(srv, "stop_speak", fake_stop)
    # 直接验证处理分支（模拟 _handle 中的打断逻辑）
    from veranima.pet_server import PetServer as PS
    # 通过真实 WS 验证：poke 到达时 stop_speak 被调用
    port = 9987
    async def scenario():
        import websockets, json
        orig_handle = srv._handle
        async def wrapped(ws):
            await orig_handle(ws)
        srv2 = PS(port=port)
        monkeypatch.setattr(srv2, "stop_speak", fake_stop)
        server = await websockets.serve(srv2._handle, "127.0.0.1", port)
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"type": "poke"}))
            await asyncio.sleep(0.3)
        server.close()
        await server.wait_closed()
    asyncio.run(scenario())
    assert len(stopped) >= 1
