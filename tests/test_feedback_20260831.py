"""2026-08-31 用户真机反馈的行为级验收（当日聊天记录逐条筛查）。

对应缺陷（exports/phone-20260831/chat_messages.json 原证据）：
- 早安轰炸（07:02~09:26 六条独立问候）：去重键只存内存，重启=清零重发
- 「醒了」三连（22:26）：作息适应消息绕过 gate + 用户刚说话就 ritual
- 睡前收到早安（12:50）：主对话 prompt 从未声明 user_asleep
- 同一记忆复读（14:58/16:04）：_dig_old_memory 无已挖排除
- 裸引号直出（07:25）：im 兜底分支不过引号守卫
"""
from __future__ import annotations

import datetime

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.proactive import GreetingScheduler
from veranima.core.reply import parse_reply
from veranima.core.state import AgentState
from veranima.llm.client import LLMError
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    def __init__(self):
        self.reply = "好的。"

    def chat(self, messages, **kw):
        return self.reply

    def chat_structured(self, messages, **kw):
        return self.reply

    def is_model_loaded(self):
        return True

    low_energy_max_tokens = 256


def _agent(tmp_path):
    card = CharacterCard(name="小V", first_mes="你好")
    memory = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    return card, memory


def _tick_agent(tmp_path):
    a = Agent(card=_agent(tmp_path)[0], memory=_agent(tmp_path)[1],
              llm=FakeLLM(), state=AgentState(), config={})
    return a


# ---------- 1. 问候去重跨重启存活 ----------

def test_greeting_dedup_survives_restart(tmp_path):
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    # 去重键持久化按「今天」过滤回灌 → 测试时钟用真实日期（时间 12:30 落 noon 窗口）
    noon = datetime.datetime.combine(datetime.date.today(), datetime.time(12, 30))
    assert a.tick_proactive(now=noon)  # 第一次：发中午问候
    assert a.tick_proactive(now=noon) == []  # 同日第二次 tick：不发
    b = Agent(card=card, memory=memory, llm=FakeLLM(),
              state=AgentState.from_snapshot(memory.load_state()), config={})
    assert b.tick_proactive(now=noon) == []  # 重启后同日同窗口：仍不发


def test_greeting_restore_only_today():
    g = GreetingScheduler()
    today = datetime.date.today().isoformat()
    g.restore_state([f"2020-01-01:morning", f"{today}:noon"])
    assert g.greeted == {f"{today}:noon"}


# ---------- 2. 「醒了」三连：静默期 + gate 前置 ----------

def test_ritual_suppressed_right_after_user_message(tmp_path):
    """用户刚发过消息（<5min）→ 本轮 ritual 整体让位（22:26 三连发场景）。"""
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    now = datetime.datetime(2026, 8, 31, 12, 30)  # 本地 12:30 = noon 窗口
    memory.con.execute(
        "INSERT INTO messages(role, content, channel, created_at) VALUES (?,?,?,?)",
        ("user", "醒了。", "qq", (now - datetime.timedelta(minutes=1)).isoformat()),
    )
    memory.con.commit()
    assert a.tick_proactive(now=now) == []
    # 5 分钟静默期一过就恢复（去重键未被消耗）
    later = now + datetime.timedelta(minutes=6)
    memory.con.execute(
        "INSERT INTO messages(role, content, channel, created_at) VALUES (?,?,?,?)",
        ("user", "醒了。", "qq", (now - datetime.timedelta(minutes=1)).isoformat()),
    )
    memory.con.commit()
    assert a.tick_proactive(now=later) != []


def test_adapt_message_never_bypasses_gate(tmp_path, monkeypatch):
    """gate 不放行时，tick_proactive 不得返回任何消息（含作息适应消息）。"""
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})

    def fake_adapt(wake_hour, now, msgs):
        msgs.append("我把生物钟挪一挪。")  # 模拟适应消息生成
    monkeypatch.setattr(a, "_adapt_schedule_to_user", fake_adapt)
    monkeypatch.setattr(a.gate, "decide", lambda *x, **k: type("D", (), {"allow": False})())
    # 无最近用户消息（recent_messages 空）→ 走到 gate 被拦
    assert a.tick_proactive(now=datetime.datetime(2026, 8, 31, 12, 30)) == []


# ---------- 3. 睡眠状态进主对话 prompt ----------

def test_sleeping_state_declared_in_prompt(tmp_path):
    card, memory = _agent(tmp_path)
    st = AgentState()
    st.user_asleep = True
    block = st.to_prompt_block()
    assert "【作息】" in block and "早安" in block
    st.user_asleep = False
    assert "【作息】" not in st.to_prompt_block()


# ---------- 4. 考古记忆不重复挖 ----------

def test_dig_old_memory_excludes_used(tmp_path):
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    ids = []
    for i in range(20):
        e = memory.store("episodic", f"记忆条目{i}", importance=0.6)
        if e.id % 3 != 0:
            ids.append(e.id)
    seen = []
    for _ in range(min(6, len(ids))):
        picked = a._dig_old_memory()
        assert picked
        seen.append(picked)
    assert len(set(seen)) == len(seen)  # 同一批池子，连挖不重样


# ---------- 5. 裸引号守卫覆盖 im 兜底分支 ----------

def test_bare_quotes_stripped_on_im_fallback():
    p = parse_reply('"早。早餐在桌上了。"', channel="im")
    assert p.text == "早。早餐在桌上了。"
    # 不成对不动
    p2 = parse_reply('他说"早上好看"，我觉得也是', channel="im")
    assert '"早上好看"' in p2.text


# ---------- 6. 苏醒总结融合进当轮回复（不再旁路） ----------

def test_wake_summary_marks_feedback_and_defers_to_turn(tmp_path, monkeypatch):
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    a.state.user_asleep = True
    now = datetime.datetime(2026, 8, 31, 14, 26, tzinfo=datetime.timezone.utc)
    memory.open_sleep_cycle((now - datetime.timedelta(hours=9)).isoformat(timespec="seconds"))
    monkeypatch.setattr(a, "_sleep_cycle_summary", lambda c: "睡足九个半小时。")
    action = a._note_sleep_report("醒了。", now)
    assert action == "wake"
    # 总结素材交给当轮融合
    assert getattr(a, "_wake_summary_for_turn", "") == "睡足九个半小时。"
    # 旁路通道已预标记消费：bridge.sleep_summary_pending 查 candidate_id 即跳过
    rows = memory.recent_proactive_feedback(source="sleep_summary", limit=10)
    cycle = memory.latest_closed_cycle()
    assert any(r.get("candidate_id") == f"sleep_summary:{cycle['id']}" for r in rows)


# ---------- 7. 落库通道标签（安卓=im，不再冒 qq） ----------

def test_channel_tag_configurable(tmp_path):
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(),
              config={"channel_tag": "im"})
    a.record_proactive_message("测试主动消息")
    assert memory.recent_messages(limit=1)[0]["channel"] == "im"


def test_channel_tag_defaults_qq(tmp_path):
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    a.record_proactive_message("PC 默认")
    assert memory.recent_messages(limit=1)[0]["channel"] == "qq"


# ---------- 8. 问候族合并窗口（2026-09-01 用户反馈 07:09/07:11 双早安） ----------

def test_merge_window_blocks_second_ritual(tmp_path):
    """任何问候族消息发出后 60min 内，其余源（含 tick_proactive 全链）让位。"""
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    now = datetime.datetime.combine(datetime.date.today(), datetime.time(12, 30))
    assert a.tick_proactive(now=now) != []          # 首发：中午问候
    b = Agent(card=card, memory=memory, llm=FakeLLM(),
              state=AgentState.from_snapshot(memory.load_state()), config={})
    later = now + datetime.timedelta(minutes=20)
    assert b.tick_proactive(now=later) == []        # 窗口内：睡醒公告/心跳同款撞车场景
    # 窗口过后：问候族恢复（当日 meal 兜底位仍可在非问候窗口出）
    assert b.proactive_merge_open(now + datetime.timedelta(minutes=61))


def test_record_proactive_message_stamps_ledger(tmp_path):
    """记账点唯一性：heartbeat/late_reply 等旁路源都经 record_proactive_message 更新窗口起点。"""
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    assert a.proactive_merge_open()  # 无历史=开
    a.record_proactive_message("角色睡醒公告")
    assert not a.proactive_merge_open()  # 刚发过=关（2026-09-01 07:09→07:11 场景）


def test_ritual_sources_single_message(tmp_path, monkeypatch):
    """清单求值：多源同时到期也只出一条（greeting 优先级最高）。"""
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    monkeypatch.setattr(a, "_missing_sleep_report_hint", lambda now="": "提示X")
    now = datetime.datetime.combine(datetime.date.today(), datetime.time(12, 30))
    msgs = a.tick_proactive(now=now)
    assert len(msgs) == 1 and msgs[0] != "提示X"  # greeting 先命中；sleep_hint 不再叠发


# ---------- 9. 睡醒公告吃掉时段问候位（2026-09-01 用户反馈 07:09/07:11 双早安） ----------

def test_woken_notice_consumes_greeting_slot(tmp_path, monkeypatch):
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    monkeypatch.setattr(a.llm, "is_model_loaded", lambda: True)
    now = datetime.datetime.combine(datetime.date.today(), datetime.time(7, 9))
    text = a.schedule_notice_text("woke", now)  # FakeLLM 返回「好的。」
    assert text
    assert now.strftime("%Y-%m-%d") + ":morning" in a.greeter.greeted
    # 重启回灌后依然生效：同日上午 ritual 问候不再发
    b = Agent(card=card, memory=memory, llm=FakeLLM(),
              state=AgentState.from_snapshot(memory.load_state()), config={})
    assert b.tick_proactive(now=now + datetime.timedelta(minutes=2)) == []


# ---------- 10. 待织池：素材攒窗合织（2026-09-01 用户裁决 v2） ----------

def test_pending_materials_weave_when_window_opens(tmp_path, monkeypatch):
    """窗口关着→素材攒池不单独发；窗口开→池内全部素材织进同一条消息（信息不丢）。
    时间线全部用朴素本地时刻注入（stamp 与判定同一时钟，无 UTC/本地混算）。"""
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    woven_in = {}

    def fake_weave(texts):
        woven_in["texts"] = list(texts)
        return "｜".join(texts)
    monkeypatch.setattr(a, "_weave_ritual", fake_weave)
    monkeypatch.setattr(a, "_adapt_schedule_to_user",
                        lambda wh, now, out: out.append("作息挪一挪"))
    # 本地 8:00：+71min=9:11 仍在 morning 窗内（槽位不漂移）；合并窗口起点
    # 由 record 记真实时刻，注入判定用 abs 差——两点都满足
    t0 = datetime.datetime.combine(datetime.date.today(), datetime.time(8, 0))
    # ① 睡醒公告（旁路源走 record 记账，注入同一时间线：窗口起点=t0）
    a.record_proactive_message("刚发过一条睡醒公告", channel="im", now=t0)
    # ② 7:11 tick：窗口关 → tick 不发、也不织（问候+adapt 素材进池；
    #    顺带验证 2min<5min 静默期同样挡发送）
    assert a.tick_proactive(now=t0 + datetime.timedelta(minutes=2)) == []
    assert woven_in.get("texts") is None
    assert len(a._ritual_pending) == 2  # greeting + schedule_adapt 双素材
    # ③ 8:20（窗口 71min 后开 + 距末条用户消息>5min）：两素材织进同一条
    msgs = a.tick_proactive(now=t0 + datetime.timedelta(minutes=71))
    assert len(msgs) == 1 and "｜" in msgs[0]        # 单条=编织产物
    assert set(woven_in["texts"]) == {"早。今天有什么打算？", "作息挪一挪"}  # 信息零丢失
    assert a._ritual_pending == []


def test_weave_falls_back_to_concat(tmp_path, monkeypatch):
    """LLM 编织失败=分段拼接回退：宁可不美不能丢信息。"""
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    monkeypatch.setattr(a, "_short_task",
                        lambda task, **kw: (_ for _ in ()).throw(LLMError("down")))
    out = a._weave_ritual(["第一件事A", "第二件事B"])
    assert "第一件事A" in out and "第二件事B" in out
    assert a._weave_ritual(["只有一件"]) == "只有一件"
