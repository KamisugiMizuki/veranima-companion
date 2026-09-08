"""09-08 用户四项裁决的落码验收（Q1 外卖逻辑 / Q2 情绪起伏）。

Q1（外卖在送到前不可能被吃完）：饭点素材时效 + 改写自查 + 全局状态一致方向句。
Q2（许眠情绪太平稳）：judges.tease → PAD 接线 → 【当下语气】表达约束。
顺带钉住两个常量：longing 过阈是小时级（旧 0.055=23 分钟，「半天没声」发在 21 分钟）。
"""
from __future__ import annotations

import datetime

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.judges import MessageJudgment, _coerce
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    base_url = "http://fake"
    raw = "早呀"

    def __init__(self, raw=None):
        if raw is not None:
            self.raw = raw
        self.calls = []
        self.last_messages = None
        self.all_messages = []

    def chat(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        self.last_messages = messages
        self.all_messages.append(messages)
        return self.raw

    def is_model_loaded(self):
        return True


def _agent(tmp_path, llm=None):
    card = CharacterCard(name="小V", first_mes="你好")
    memory = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    return Agent(card=card, memory=memory, llm=llm or FakeLLM(),
                 state=AgentState(), config={})


def _quiet_agent(tmp_path, llm=None):
    """凌晨 3 点 + 低依恋 + 刚报过作息：收集器全静默，池里只留手工注入的素材。"""
    a = _agent(tmp_path, llm=llm)
    a.state.attachment = 0.2
    a.state.last_sleep_report_at = "2026-08-02T20:00:00+00:00"
    return a


def _seed_user_msg(a, when: datetime.datetime):
    a.memory.store_message("user", "先去忙了", 80, "平静")
    a.memory.con.execute(
        "UPDATE messages SET created_at=? WHERE id=(SELECT max(id) FROM messages)",
        (when.isoformat(),))
    a.memory.con.commit()


# ---------- Q1：过期素材不再当事实说 ----------

def test_stale_meal_material_dropped_from_pool(tmp_path):
    """09-08 真机实锤：16:05 收的午饭素材 17:02 才出窗 → 反问「外卖到了就吱一声」，
    而用户 16:27 已说「早吃完了」。迟到 30 分钟以上的饭点提醒=噪音，直接丢。"""
    a = _quiet_agent(tmp_path)
    now = datetime.datetime(2026, 8, 3, 3, 0)
    _seed_user_msg(a, now - datetime.timedelta(hours=1))
    a._ritual_pending[:] = [
        {"source": "meal", "text": "到饭点了，先去吃午饭。", "meal": "lunch",
         "ts": (now - datetime.timedelta(minutes=57)).timestamp(), "role": a.card.name},
    ]
    msgs = a.tick_proactive(now=now)
    assert msgs == []
    assert not [m for m in a._ritual_pending if m.get("source") == "meal"]


def test_fresh_meal_material_still_fires(tmp_path):
    """时效内照常发（闸只砍过期，不砍功能）。"""
    a = _quiet_agent(tmp_path)
    now = datetime.datetime(2026, 8, 3, 3, 0)
    _seed_user_msg(a, now - datetime.timedelta(hours=1))
    a._ritual_pending[:] = [
        {"source": "meal", "text": "到饭点了，先去吃午饭。", "meal": "lunch",
         "ts": (now - datetime.timedelta(minutes=5)).timestamp(), "role": a.card.name},
    ]
    assert a.tick_proactive(now=now) == ["早呀"]


def test_meal_rewrite_self_veto_sends_nothing(tmp_path):
    """改写自查判「前提已被推翻」→ 回空串：不退回模板（模板同样催饭），也不落空消息。"""
    a = _quiet_agent(tmp_path, llm=FakeLLM(raw=""))
    assert a._meal_message("lunch", "到饭点了，先去吃午饭。") == ""
    now = datetime.datetime(2026, 8, 3, 3, 0)
    _seed_user_msg(a, now - datetime.timedelta(hours=1))
    a._ritual_pending[:] = [
        {"source": "meal", "text": "到饭点了，先去吃午饭。", "meal": "lunch",
         "ts": (now - datetime.timedelta(minutes=5)).timestamp(), "role": a.card.name},
    ]
    assert a.tick_proactive(now=now) == []
    kinds = [r[0] for r in a.memory.con.execute("SELECT kind FROM decisions")]
    assert any("ritual:meal" in str(k) for k in kinds)   # 否决留痕（D4）


def test_state_consistency_direction_present(tmp_path):
    """方向句：已交代结果的事不再问 + 不虚报自己说过（交 prompt 不交出口硬删）。"""
    a = _agent(tmp_path)
    block = a._time_context_instruction()
    assert "【状态一致】" in block and "吃完了" in block
    assert "【别虚报自己】" in block


# ---------- Q2：情绪起伏接线 ----------

def test_affect_moves_from_judgment_and_shows_in_block(tmp_path):
    a = _agent(tmp_path)
    base_arousal = a.state.arousal
    a._update_affect(MessageJudgment(tease=True))
    assert a.state.arousal > base_arousal + 0.1      # 被调戏=唤醒上来了
    block = a._affect_block()
    assert "【当下语气】" in block and "标点" in block  # 情绪必须落到可见文本形态
    for _ in range(20):                               # 无情绪事件的轮次→向基线回归
        a._update_affect(MessageJudgment())
    assert abs(a.state.arousal - 0.5) < 0.03
    assert a._affect_block() == ""                    # 平复后不再加约束


def test_affect_negative_emotion_lowers_valence(tmp_path):
    a = _agent(tmp_path)
    a._update_affect(MessageJudgment(emotion="angry"))
    assert a.state.valence < 0.5 and a.state.arousal > 0.5


def test_judge_tease_coercion():
    assert _coerce({"tease": True}).tease is True
    assert _coerce({"tease": False}).tease is False
    assert _coerce({"tease": "true"}).tease is None    # 非 bool 丢弃（同 _VALID_* 纪律）


def test_turn_prompt_carries_affect_block(tmp_path, monkeypatch):
    """接线端到端：被调戏的一轮，system prompt 里必须出现【当下语气】。"""
    a = _agent(tmp_path)
    monkeypatch.setattr(a, "turn_judgment", lambda text: MessageJudgment(tease=True))
    a.handle("想我没？")
    systems = [m["content"] for msgs in a.llm.all_messages
               for m in msgs if m.get("role") == "system"]
    assert any("【当下语气】" in s for s in systems)


# ---------- 09-09：她自己的生活也驱动情绪（此前唯一驱动源=用户消息） ----------

def test_life_event_moves_pad_and_shows_in_block(tmp_path):
    """用户裁决（09-09）：日程/地点/路上出岔子必须留下情绪痕迹，且要落到可见
    文本形态（【当下语气】）——否则「她有自己的生活」只是日程表在动。"""
    a = _agent(tmp_path)
    a._apply_life_affect("transition_interrupted")
    assert a.state.valence < 0.45 and a.state.arousal > 0.58
    assert a.state.last_cause == "路上不顺"
    assert "【当下语气】" in a._affect_block()


def test_life_affect_converges_not_drifts(tmp_path):
    """同类事件重复只收敛到靶点：离场期间没有 per-turn decay，若写成加法会单向漂到边界。"""
    a = _agent(tmp_path)
    for _ in range(60):
        a._apply_life_affect("transition_interrupted")
    tv, ta, _ = Agent._LIFE_MOOD["transition_interrupted"]
    assert abs(a.state.valence - tv) < 0.01 and abs(a.state.arousal - ta) < 0.01


def test_life_affect_ignores_unknown_event(tmp_path):
    """只认结构事件表——新增事件忘了登记时不许悄悄改状态。"""
    a = _agent(tmp_path)
    before = (a.state.valence, a.state.arousal, a.state.last_cause)
    a._apply_life_affect("place_entered_v2")
    assert (a.state.valence, a.state.arousal, a.state.last_cause) == before


def test_schedule_block_speaks_human_not_machine_key(tmp_path):
    """活动键是机器名（model_training_work），注入提示要给人话。"""
    from veranima.core.virtual_schedule import ScheduleContext
    ctx = ScheduleContext(plan_id="p", item_id="i", activity_category="obligation",
                          phase="work", progress=0.2, interaction_profile="occupied",
                          availability=0.2, reply_budget={}, share_allowed=False,
                          curiosity_allowed=False, source_anchor={},
                          activity_key="model_training_work", place_label="家")
    block = Agent._format_schedule_context(ctx)
    assert "盯着训练跑" in block and "model_training_work" not in block


def test_scene_event_through_advance_moves_mood(tmp_path):
    """真实链路：advance_schedule_async 留痕的那一次场景事件必须同时落到 PAD
    （不是只有单元级调用能动状态）。"""
    import asyncio
    import datetime as _dt
    import json as _json
    from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime

    role = tmp_path / "characters" / "life"
    role.mkdir(parents=True)
    (role / "virtual_schedule.json").write_text(_json.dumps({
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "08:00", "end": "09:00"},
                      "sleep_window": {"start": "22:00", "end": "23:00"},
                      "chronotype": "day_aligned", "target_sleep_minutes": 480},
        "sleep": {"grace_period_minutes": 0, "max_extension_minutes": 0},
    }), encoding="utf-8")

    a = _agent(tmp_path)
    a.schedule_runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role))
    a.schedule_runtime.pending_scene_event = "transition_interrupted"
    when = _dt.datetime(2026, 8, 28, 10, 0, tzinfo=_dt.timezone.utc)  # 北京 18 点，醒着
    asyncio.run(a.advance_schedule_async(when))

    assert a.state.last_cause == "路上不顺" and a.state.valence < 0.45
    kinds = [r[0] for r in a.memory.con.execute("SELECT event_kind FROM virtual_life_events")]
    assert "transition_interrupted" in kinds


# ---------- 常量：longing 是小时级 ----------

def test_longing_threshold_is_hours_not_minutes(tmp_path):
    a = _agent(tmp_path)
    a.state.attachment = 1.0
    t0 = datetime.datetime(2026, 8, 3, 10, 0)
    _seed_user_msg(a, t0 - datetime.timedelta(minutes=1))
    for i in range(6):                                  # 静默 25 分钟
        a.desires.tick(t0 + datetime.timedelta(minutes=i * 5))
    assert a.desires._level("longing") < a.desires._LONG_TH   # 旧常量 21 分钟就过阈
    for i in range(45):                                 # 静默累计约 3.75 小时
        a.desires.tick(t0 + datetime.timedelta(minutes=30 + i * 5))
    assert a.desires._level("longing") >= a.desires._LONG_TH
    assert "半天" not in "".join(a.desires._LONG_LINES)
