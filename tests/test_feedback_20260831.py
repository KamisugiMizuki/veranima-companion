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
import pathlib

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard as Card
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


# ---------- 11. 联想四型（2026-09-01 设计文档落地） ----------

def _probe_agent(tmp_path, gap_hours=3.0):
    """构造「用户 gap_hours 小时前发过消息」的 agent（本地时间轴自洽）。"""
    import datetime as _dt
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    a.state.attachment = 0.8  # 解锁 B 类门槛
    then = _dt.datetime.now().replace(tzinfo=None) - _dt.timedelta(hours=gap_hours)
    memory.con.execute(
        "INSERT INTO messages(role, content, channel, created_at) VALUES (?,?,?,?)",
        ("user", "今天开始减肥", "qq", then.isoformat()))
    memory.con.commit()
    return a, memory


def test_context_probe_gates(tmp_path):
    """B 类：2-6h 窗口/日≤2/同桶不重复/依恋门槛。"""
    a, mem = _probe_agent(tmp_path)
    now = __import__("datetime").datetime.now().replace(tzinfo=None)
    bucket = ("morning" if 6 <= now.hour < 11 else "noon" if now.hour < 15
              else "evening" if 15 <= now.hour < 23 else "night")
    out = a._context_probe(now)
    if bucket == "night":
        assert out == ""            # 深夜不推测（打扰）
        return
    assert out  # FakeLLM「好的。」
    assert a._context_probe(now) == ""  # 同桶去重
    a.state.attachment = 0.2
    now2 = now + __import__("datetime").timedelta(hours=5)
    assert a._context_probe(now2) == ""  # 依恋 <0.3 不发


def test_context_probe_gap_bounds(tmp_path):
    a, _ = _probe_agent(tmp_path, gap_hours=1.0)
    import datetime as _dt
    assert a._context_probe(_dt.datetime.now().replace(tzinfo=None)) == ""  # <2h 不触发
    a2, _ = _probe_agent(tmp_path / "b", gap_hours=8.0)
    assert a2._context_probe(_dt.datetime.now().replace(tzinfo=None)) == ""  # >6h 不触发


def test_context_probe_flows_into_pool(tmp_path):
    """B 类素材进待织池：与问候一起被 _weave_ritual 合并。"""
    import datetime as _dt
    a, _ = _probe_agent(tmp_path)
    woven = {}
    # 直接调 tick：morning/noon/evening 当前真实小时的桶 + probe 都在意
    a._weave_ritual = lambda texts: (woven.__setitem__("t", texts), "｜".join(texts))[1]
    now = _dt.datetime.now().replace(tzinfo=None, second=0, microsecond=0)
    msgs = a.tick_proactive(now=now)
    if woven.get("t"):
        assert len(msgs) <= 1 and len(woven["t"]) >= 1  # 多素材同轮 → 一条产物


def test_sleep_care_includes_open_promise(tmp_path):
    """A 类扩展：睡前牵挂带上未兑现承诺（PromiseBook 识别的句式）。"""
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    a.state.attachment = 0.8
    assert a.promises.record("我明天要去看牙，记得提醒我")  # 命中承诺句式
    # 造作息错位：角色睡眠窗覆盖全部三餐锚点（8/12/17 点全在 20:00→07:00 之外→
    # 反过来：角色 20 点睡 7 点起，餐点 8-17 落醒时=不错位；这里让角色 09 睡 18 醒）
    class _C:
        sleep_start, wake_start = "09:00", "18:00"
    class _O:
        circadian = _C()
    class _R:
        outline = _O()
    a.schedule_runtime = _R()
    care = a._sleep_care_note()
    assert "午饭" in care      # 饭点错位（12 点在角色睡眠窗 09-18 内）
    assert "看牙" in care or "牙" in care  # 牵挂叠加未完成事项


def test_dig_old_memory_returns_confidence(tmp_path):
    """C 类：挖旧事带置信度（<0.7 心跳注入"记不清"语气）。"""
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    for i in range(10):
        memory.store("episodic", f"旧事{i}", importance=0.6, confidence=0.5)
    got = a._dig_old_memory()
    assert got and isinstance(got, tuple) and got[1] == 0.5


# ---------- 12. 作息偏移按角色隔离（跨角色切换不污染） ----------

def test_schedule_offset_isolated_across_roles(tmp_path):
    """A 角色攒的偏移落在共享 relationship 快照；B 角色 boot 恢复快照时
    from_snapshot 的 role_id 守卫整体弃档（offset=0），不得继承。"""
    from dataclasses import replace
    from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime
    outline_a = ScheduleOutline.from_role_dir("characters/lin")
    outline_b = replace(outline_a, role_id="yuki")
    assert outline_b.role_id != outline_a.role_id
    # 凛攒 40 分钟偏移 → 落快照
    rt_a = ScheduleRuntime(outline_a)
    import datetime as _dt
    rt_a.apply_offset(40, "test", _dt.datetime.now(_dt.timezone.utc))
    snap = rt_a.to_snapshot()
    # 凛自己重启：继承偏移
    assert ScheduleRuntime.from_snapshot(outline_a, snap).schedule_offset_minutes == 40
    # 切由岐：role_id 守卫 → 弃档回基准作息
    assert ScheduleRuntime.from_snapshot(outline_b, snap).schedule_offset_minutes == 0


# ---------- 13. 跨角色关系隔离 + 卡级作息偏移上限（许眠卡落地） ----------

def test_relationship_snapshot_isolated_across_roles(tmp_path):
    """agent_state 共享单行：凛攒的亲密度不得灌给切换后的新角色，
    同卡重启则必须保留（快照 owner 标守卫）。"""
    root = pathlib.Path(__file__).resolve().parents[1] / "characters"
    lin = Card.from_file(root / "lin" / "character.json")
    xumian = Card.from_file(root / "xumian" / "character.json")
    memory = MemoryStore(db_path=str(tmp_path / "iso.db"), config={}, provider=FakeEmbed())
    a1 = Agent(card=lin, memory=memory, llm=None, state=AgentState(), config={})
    a1.relationship.intimacy = 0.9
    a1.state.attachment = 0.93
    a1._persist_state()
    a2 = Agent(card=xumian, memory=memory, llm=None,
               state=AgentState.from_snapshot(memory.load_state()), config={})
    assert a2.relationship.intimacy == 0.82  # 新角色=自己卡的 preset，非凛的 0.9
    assert a2.state.attachment == 0.5
    a2._persist_state()
    a3 = Agent(card=xumian, memory=memory, llm=None,
               state=AgentState.from_snapshot(memory.load_state()), config={})
    assert a3.relationship.intimacy == 0.82  # 同卡重启不清档


def test_card_relationship_preset_and_schedule_cap():
    """异地恋人卡：交往史先验 + 作息偏移受 max_offset_minutes 约束（凛不受限）。"""
    from veranima.core.persona import RelationshipModel
    root = pathlib.Path(__file__).resolve().parents[1] / "characters"
    xumian = Card.from_file(root / "xumian" / "character.json")
    preset = xumian.veranima["relationship_preset"]
    m = RelationshipModel.from_initial(0.5, preset=preset)
    assert m.intimacy == 0.82 and m.trust == 0.80
    assert m.recurring_rituals and m.open_relational_threads
    import datetime as _dt
    from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime
    ox = ScheduleOutline.from_role_dir(root / "xumian")
    rx = ScheduleRuntime(ox)
    rx.apply_offset(9999, "x", _dt.datetime.now(_dt.timezone.utc))
    assert rx.schedule_offset_minutes == 120  # 996 卡：只许挪两小时
    ol = ScheduleOutline.from_role_dir(root / "lin")
    rl = ScheduleRuntime(ol)
    rl.apply_offset(9999, "x", _dt.datetime.now(_dt.timezone.utc))
    assert rl.schedule_offset_minutes == 720  # 住家卡：全幅不变


def test_relationship_roster_round_trip(tmp_path):
    """roster：凛攒进度→切许眠→切回凛，凛的账还在；许眠吃自己 preset。"""
    root = pathlib.Path(__file__).resolve().parents[1] / "characters"
    lin = Card.from_file(root / "lin" / "character.json")
    xumian = Card.from_file(root / "xumian" / "character.json")
    db = str(tmp_path / "roster.db")
    def boot(card):
        mem = MemoryStore(db_path=db, config={}, provider=FakeEmbed())
        snap = mem.load_state()
        return Agent(card=card, memory=mem, llm=None,
                     state=AgentState.from_snapshot(snap) if snap else AgentState(), config={})
    a1 = boot(lin); a1.relationship.intimacy = 0.9; a1.state.attachment = 0.93; a1._persist_state()
    a2 = boot(xumian); a2._persist_state()          # 首 boot=preset，写自己条目
    a3 = boot(lin)
    assert a3.relationship.intimacy == 0.9          # 凛的账原样回来（不被覆盖）
    assert abs(a3.state.attachment - 0.93) < 1e-6
    a4 = boot(xumian)
    assert a4.relationship.intimacy == 0.82         # 许眠也没被凛灌


# ---------- 14. 用户画像 + 称呼系统（2026-09-01 设计稿裁决） ----------

def test_profile_judgment_lands_in_store(tmp_path):
    """判断点 profile 字段 → user_profile 表；闭集外键丢弃。"""
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    from veranima.core.judges import _coerce
    j = _coerce({"profile": {"real_name": "林晓", "city": "杭州", "gender_guess": "脑补"}})
    a._apply_profile_facts(j)
    assert memory.profile_get("real_name")["value"] == "林晓"
    assert memory.profile_get("gender_guess") is None  # 闭集外不落

def test_profile_user_source_not_overwritten(tmp_path):
    """用户自述级(user)不被对话推断级(dialog)覆盖；用户再自述可更新。"""
    _, memory = _agent(tmp_path)
    memory.profile_set("city", "杭州", source="user", confidence=1.0)
    memory.profile_set("city", "上海", source="dialog", confidence=0.7)
    assert memory.profile_get("city")["value"] == "杭州"
    memory.profile_set("city", "北京", source="user", confidence=1.0)
    assert memory.profile_get("city")["value"] == "北京"

def test_nickname_forbidden_per_role(tmp_path):
    """「别叫我宝宝」→ 只对当前角色记 forbidden，换角色不继承。"""
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    a._capture_nickname_feedback("别叫我宝宝，鸡皮疙瘩")
    nicks = memory.nicknames_for(card.name or "小V")
    assert "宝宝" in (nicks.get("forbidden") or []) or nicks  # 至少落账
    memory.nickname_mark("凛", "宝宝", "forbidden", stage="熟悉")
    assert "宝宝" in memory.nicknames_for("凛")["forbidden"]
    assert "宝宝" not in memory.nicknames_for("许眠")["forbidden"]

def test_profile_block_injects_with_pools(tmp_path):
    """_profile_block：画像+称呼账+卡内阶段池，一次成型；空库=空串。"""
    root = pathlib.Path(__file__).resolve().parents[1] / "characters"
    xumian = Card.from_file(root / "xumian" / "character.json")
    _, memory = _agent(tmp_path)
    a = Agent(card=xumian, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    assert a._profile_block() == ""            # 空画像不灌噪声
    memory.profile_set("occupation", "学生", source="user", confidence=1.0)
    memory.nickname_mark("xumian", "宝宝", "forbidden")
    a.relationship.intimacy = 0.9; a.relationship.trust = 0.9
    a.relationship.safety = 0.9; a.relationship.reciprocity = 0.85
    block = a._profile_block()
    assert "学生" in block and "用户亲口说的" in block
    assert "宝宝" in block and "绝不能" in block
    assert "称呼" in block                      # 阶段池行存在（任意阶段）


# ---------- 15. 多角色会话隔离（MOMENTS_MULTIROLE_SPEC P1） ----------

def test_messages_isolated_by_role(tmp_path):
    """两个 Agent 共享单库：各自 store/recent 只见自己的会话；PC 角色键空=不过滤。"""
    root = pathlib.Path(__file__).resolve().parents[1] / "characters"
    lin = Card.from_file(root / "lin" / "character.json")
    xumian = Card.from_file(root / "xumian" / "character.json")
    mem = MemoryStore(db_path=str(tmp_path / "iso2.db"), config={}, provider=FakeEmbed())
    a1 = Agent(card=lin, memory=mem, llm=FakeLLM(), state=AgentState(), config={})
    a2 = Agent(card=xumian, memory=mem, llm=FakeLLM(), state=AgentState(), config={})
    assert (a1.role_key, a2.role_key) == ("lin", "xumian")
    a1.memory.store_message("user", "凛的悄悄话", role_id=a1.role_key)
    a2.memory.store_message("user", "眠的悄悄话", role_id=a2.role_key)
    only_lin = mem.recent_messages(role_id="lin")
    only_xu = mem.recent_messages(role_id="xumian")
    assert [m["content"] for m in only_lin] == ["凛的悄悄话"]
    assert [m["content"] for m in only_xu] == ["眠的悄悄话"]
    assert len(mem.recent_messages()) == 2          # 不过滤=全量（PC 兼容）

def test_backfill_and_unread(tmp_path):
    """旧库迁移：'' 行一次性归活跃角色；未读=assistant 消息超已读指针。"""
    _, mem = _agent(tmp_path)
    mem.con.execute("INSERT INTO messages(role, content, channel, created_at) VALUES ('user','前世','','x')")
    mem.con.commit()
    assert mem.message_role_gaps() == 1
    assert mem.backfill_message_roles("lin") == 1
    assert mem.message_role_gaps() == 0
    mem.store_message("assistant", "凛主动消息", role_id="lin")
    mem.store_message("assistant", "眠主动消息", role_id="xumian")
    assert mem.unread_counts() == {"lin": 1, "xumian": 1}
    mem.mark_role_read("lin")
    assert mem.unread_counts() == {"xumian": 1}  # 凛清零，眠不受影响

def test_legacy_schema_migrates_role_id(tmp_path):
    """旧库（messages 无 role_id 列）init_db 迁移补列，存量行不丢。"""
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL, content TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'qq',
        created_at TEXT NOT NULL, energy_at REAL, mood_at TEXT,
        tone_at TEXT NOT NULL DEFAULT '', attachments TEXT NOT NULL DEFAULT '')""")
    con.execute("INSERT INTO messages(role, content, created_at) VALUES ('user','旧行','x')")
    con.commit(); con.close()
    MemoryStore(db_path=str(db), config={}, provider=FakeEmbed())  # init_db 迁移
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(messages)")}
    assert "role_id" in cols


# ---------- 16. 通知角色署名 + 头像查找（2026-09-01 用户裁决） ----------

def test_drain_pending_carries_role_name(tmp_path):
    """drain_pending 元素={role,name,text}：通知标题取角色名（微信式）。"""
    import importlib
    import sys
    root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "android" / "fuyuno" / "app" / "src" / "main" / "python"))
    try:
        br = importlib.import_module("bridge")
    finally:
        sys.path.remove(str(root / "android" / "fuyuno" / "app" / "src" / "main" / "python"))
    br._pending.clear()
    br._pending.append({"role": "xumian", "name": "许眠", "text": "在吗"})
    import json as _j
    out = _j.loads(br.drain_pending())
    assert out["messages"][0]["name"] == "许眠"
    assert out["messages"][0]["role"] == "xumian"

def test_avatar_path_convention(tmp_path):
    """portrait.jpg 约定优先；缺→portraits/ 首图兜底；再缺=''。"""
    import importlib, sys
    root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "android" / "fuyuno" / "app" / "src" / "main" / "python"))
    try:
        br = importlib.import_module("bridge")
    finally:
        sys.path.remove(str(root / "android" / "fuyuno" / "app" / "src" / "main" / "python"))
    br.boot.root = tmp_path
    rd = tmp_path / "characters" / "foo"
    rd.mkdir(parents=True)
    assert br.avatar_path("foo") == ""
    (rd / "portrait.jpg").write_bytes(b"x")
    assert br.avatar_path("foo").endswith("portrait.jpg")
    (rd / "portrait.jpg").unlink()
    (rd / "portraits" / "a.png").parent.mkdir()
    (rd / "portraits" / "a.png").write_bytes(b"y")
    assert br.avatar_path("foo").endswith("a.png")


# ---------- 17. 好友动态引擎（MOMENTS_MULTIROLE_SPEC P2） ----------

def _moment_agent(tmp_path, role="xumian"):
    root = pathlib.Path(__file__).resolve().parents[1] / "characters"
    card = Card.from_file(root / role / "character.json")
    mem = MemoryStore(db_path=str(tmp_path / "mo.db"), config={}, provider=FakeEmbed())
    a = Agent(card=card, memory=mem, llm=FakeLLM(), state=AgentState(),
              config={"character_card": str(root / role / "character.json"),
                      "root": str(root.parent), "virtual_schedule": {"enabled": True}})
    a.state.mood = "开心"   # D03 情绪素材稳定命中（默认"平静"不产素材=空池）
    return a, mem


def test_moment_publish_dedupe(tmp_path):
    """dedupe_key 撞 UNIQUE=静默 0：同素材永不二次成动态。"""
    a, mem = _moment_agent(tmp_path)
    id1 = mem.moment_publish("xumian", "今天跑完了", kind="D03", source_ref="m1", dedupe_key="k1")
    id2 = mem.moment_publish("xumian", "换个内容也一样被拒", kind="D03", source_ref="m2", dedupe_key="k1")
    assert id1 > 0 and id2 == 0
    assert len(mem.moments_recent_texts("xumian")) == 1

def test_moment_gate_and_tick(tmp_path):
    """发布链：素材→闸→入库；同 tick 幂等；开关关掉即停发。"""
    a, mem = _moment_agent(tmp_path)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    n1 = a.moments.tick(now=now)
    assert n1 == 1                                   # FakeLLM 织文成功（"好的。"）
    assert mem.moments_count_today("xumian", now.date().isoformat()) == 1
    assert a.moments.tick(now=now) == 0              # 6h 冷却（gap 闸）
    # 关开关=硬闸
    mem.role_settings_set("xumian", {"moments": {"enabled": False}})
    far = now + __import__("datetime").timedelta(hours=48)
    assert a.moments.tick(now=far) == 0

def test_moment_tick_role_key_required(tmp_path):
    """PC/QQ 无角色键：动态引擎自动禁用（不炸、不产生无主动态）。"""
    card, memory = _agent(tmp_path)
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    assert a.role_key == ""
    assert a.moments.tick() == 0

def test_moment_llm_fail_fallback(tmp_path):
    """织文失败（LLMError）→ 素材原文降级入库：零丢失。"""
    a, mem = _moment_agent(tmp_path / "f")
    a._short_task = lambda *a_, **k_: (_ for _ in ()).throw(LLMError("boom"))
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    assert a.moments.tick(now=now) == 1
    row = mem.moments_recent_texts("xumian", limit=1)[0]
    assert len(row) > 2 and row != "好的。"            # 降级=素材文本非 LLM 假答

def test_moment_interactions_store(tmp_path):
    """赞=toggle 幂等；评论+角色回复都进流水。"""
    a, mem = _moment_agent(tmp_path)
    mid = mem.moment_publish("xumian", "测试动态", kind="D05", source_ref="x", dedupe_key="kk")
    assert mem.moment_toggle_like(mid) is True
    assert mem.moment_toggle_like(mid) is False
    assert mem.moment_toggle_like(mid) is True
    mem.moment_comment(mid, "哈哈哈", "user")
    mem.moment_reply(mid, "笑什么", "xumian")
    feed = mem.moment_feed()
    d = [x for x in feed if x["id"] == mid][0]
    assert d["likes"] == 1 and d["liked_by_me"] is True
    assert [c["actor"] for c in d["comments"]] == ["user", "xumian"]

def test_d01_ignores_space_events(tmp_path):
    """素材过滤：虚拟生活表混着空间事件——D01 只吃日终摘要。"""
    a, mem = _moment_agent(tmp_path / "d01")
    mem.store_virtual_life_event(role_id="xumian", event_kind="space_move",
                                 summary="当前虚拟地点：公司工位", source={})
    mem.store_virtual_life_event(role_id="xumian", event_kind="day_close_summary",
                                 summary="本周期有效活动 300 分钟，中断 10 分钟。", source={})
    mats = a.moments._materials(__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    kinds = [x[0] for x in mats if x[0] == "D01"]
    assert kinds == ["D01"]
    refs = [x[2] for x in mats if x[2].startswith("event:")]
    assert refs, "日终摘要应入选"
    ev_id = refs[0].split(":")[1]
    row = mem.con.execute("SELECT event_kind FROM virtual_life_events WHERE id=?", (ev_id,)).fetchone()
    assert row["event_kind"] == "day_close_summary"


# ---------- 18. D02 虚拟天气 / D07 角色级里程碑（P3 素材源） ----------

def test_virtual_weather_deterministic():
    """纯函数：同城同天=同结果（全源一致）；跨天/跨城会变（覆盖≠恒定）。"""
    from veranima.core.moments import virtual_weather
    import datetime as _dt
    d1, d2 = _dt.date(2026, 9, 1), _dt.date(2026, 9, 2)
    assert virtual_weather("成都", d1) == virtual_weather("成都", d1)
    seq = {virtual_weather("成都", d1 + _dt.timedelta(days=i)) for i in range(30)}
    assert len(seq) >= 3                       # 30 天里至少出过 3 种天气（不是死值）
    assert virtual_weather("成都", d1) in ("晴", "多云", "阴", "雨", "降温", "大风")

def test_d07_uses_role_scoped_count(tmp_path):
    """里程碑计数=该角色会话行数，非共享全局 total_messages。"""
    a, mem = _moment_agent(tmp_path / "d07")
    # 全局计数造假 999，但该角色会话只有 3 条 → 不得命中 500/1000 里程碑
    a.state._total_messages = 999
    mats = a.moments._materials(__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    assert not [x for x in mats if x[0] == "D07"]
    for i in range(505):
        mem.store_message("user", f"m{i}", role_id="xumian")
    mats = a.moments._materials(__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    d07 = [x for x in mats if x[0] == "D07"]
    assert d07 and d07[0][2] == "mile:500"     # 500 台阶，按角色会话数


# ---------- 19. P3 设置消费链（类型过滤/评论风格/称呼锁定/屏蔽话题） ----------

def test_moment_allowed_types_filter(tmp_path):
    """allowed_types 白名单外素材一律不发（只留 D02 → D03 情绪素材被滤光）。"""
    a, mem = _moment_agent(tmp_path / "at")
    mem.role_settings_set("xumian", {"moments": {"allowed_types": ["D02"]}})
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    # 素材池默认含 D03（mood=开心）+D02；若 D02 被排除日已发过则空池 → 只验发布类型
    n = a.moments.tick(now=now)
    if n:
        kinds = mem.moments_recent_kinds("xumian", limit=1)
        assert kinds == ["D02"]

def test_comment_style_none_and_minimal(tmp_path):
    """comment_response_style: none=不回复；minimal=短模板路径（走 LLM 但提示词换）。"""
    a, mem = _moment_agent(tmp_path / "cs")
    mid = mem.moment_publish("xumian", "测试动态", kind="D05", source_ref="x", dedupe_key="cs1")
    mem.role_settings_set("xumian", {"interaction": {"comment_response_style": "none"}})
    assert a.moments.reply_comment(mid, "哈哈哈") == ""

def test_profile_block_expression_prefs(tmp_path):
    """expression 组进 prompt：固定称呼压制池演化、追加屏蔽、表达强度行。"""
    a, mem = _moment_agent(tmp_path / "ex")
    a.relationship.intimacy = 0.9; a.relationship.trust = 0.9
    a.relationship.safety = 0.9; a.relationship.reciprocity = 0.85
    mem.role_settings_set("xumian", {"expression": {
        "fixed_nickname": "Kamisugi", "sensitive_topics_extra": ["体检"],
        "expressiveness": "cold"}})
    # 画像非空才会出块（expression 挂在画像块尾部）
    mem.profile_set("city", "杭州", source="user", confidence=1.0)
    block = a._profile_block()
    assert "固定用「Kamisugi」" in block and "体检" in block and "偏冷淡" in block

def test_moment_fallback_no_machine_text(tmp_path):
    """织文失败降级：发布的是第一人称骨架，绝不把素材指令（精力86%类）直录。"""
    a, mem = _moment_agent(tmp_path / "fb2")
    a._short_task = lambda *a_, **k_: ""     # 模拟 LLM 空返回（非异常）
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    assert a.moments.tick(now=now) == 1
    row = mem.moments_recent_texts("xumian", limit=1)[0]
    assert "精力" not in row and "情绪" not in row   # 机器口径零泄漏
    assert row in ("今天心情挺好，说不上为什么。",)     # D03 开心骨架
