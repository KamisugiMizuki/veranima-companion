"""2026-09-07 外部借鉴评估（design_append §12）落码行为验收。

C（否决台账）：judges 源名闭集裁决 → 词面兜底 → 台账捕获（带保质期）→
tick 收集端剔除 → 到期自动解除 → 随快照持久化（重启读回）。
D（复盘维度）：夜间 digest 的 portrait 引导含固定角度且要求「有依据才写」。
A/B/E 为纯文档裁决（A 移交仓库外；B 挂账 SHARED_CREATION §3.6；
E 并入 CHARPKG §3.2），无代码断言。
"""
from __future__ import annotations

import datetime

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.judges import MessageJudgment, _coerce
from veranima.core.proactive import MealReminderScheduler, veto_from_keywords
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

    def chat(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        return self.raw

    def is_model_loaded(self):
        return True


def _agent(tmp_path, llm=None):
    card = CharacterCard(name="小V", first_mes="你好")
    memory = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    return Agent(card=card, memory=memory, llm=llm or FakeLLM(),
                 state=AgentState(), config={})


# ---------- C：judges 源名闭集 ----------

def test_veto_coerce_closed_set():
    j = _coerce({"veto_source": "meal", "veto_days": 3})
    assert j.veto_source == "meal" and j.veto_days == 3
    # 源名表外=丢弃（同 _VALID_* 纪律）；天数越界截断、非数字归 0
    assert _coerce({"veto_source": "喝水提醒", "veto_days": 2}).veto_source == ""
    assert _coerce({"veto_source": "greeting", "veto_days": 999}).veto_days == 365
    assert _coerce({"veto_source": "greeting", "veto_days": "三天"}).veto_days == 0


def test_veto_keyword_fallback():
    assert veto_from_keywords("别老提醒我吃饭了") == ("meal", 0)
    assert veto_from_keywords("这三天不用跟我问好") == ("greeting", 3)
    # 否决句式但不含已知类型 / 普通聊天 → 不否决
    assert veto_from_keywords("别再提那件事了") is None
    assert veto_from_keywords("今天天气不错") is None


def test_veto_capture_and_tick_suppresses_source(tmp_path):
    """否决 meal 后：晚餐到点不产素材；未否决的 occasion 等其余源不受影响。"""
    a = _agent(tmp_path)
    a._capture_proactive_veto("别老提醒我吃饭了",
                              MessageJudgment(veto_source="meal", veto_days=0))
    assert a._proactive_veto.get("meal") == ""
    target = MealReminderScheduler().scheduled_at(datetime.date(2026, 8, 4), "dinner")
    assert a.tick_proactive(now=target) == []
    # 去重键未被消耗：解除否决后同一天饭点仍可发（闸在收集前）
    a._proactive_veto.pop("meal")
    assert a.tick_proactive(now=target) != []


def test_veto_judgment_present_no_keyword_second_guess(tmp_path):
    """判断点在场且裁决无否决（veto_source 空）→ 不再跑词面兜底。"""
    a = _agent(tmp_path)
    a._capture_proactive_veto("别老提醒我吃饭了", MessageJudgment())  # LLM 判=无否决
    assert a._proactive_veto == {}


def test_veto_expiry(tmp_path):
    a = _agent(tmp_path)
    today = datetime.date(2026, 9, 7)
    a._proactive_veto["greeting"] = (today + datetime.timedelta(days=3)).isoformat()
    assert a._vetoed("greeting", datetime.datetime(2026, 9, 9, 8, 0)) is True
    assert a._vetoed("greeting", datetime.datetime(2026, 9, 11, 8, 0)) is False
    assert "greeting" not in a._proactive_veto  # 到期即清账


def test_veto_persists_across_restart(tmp_path):
    a = _agent(tmp_path)
    a._capture_proactive_veto("别催我报作息了",
                              MessageJudgment(veto_source="sleep_hint", veto_days=0))
    a._persist_state()
    b = _agent(tmp_path)  # 同库新 Agent（快照读回）
    assert b._proactive_veto.get("sleep_hint") == ""
    assert b._vetoed("sleep_hint", datetime.datetime.now()) is True


# ---------- D：复盘维度（digest 提示词引导） ----------

def test_digest_portrait_guidance_words(tmp_path):
    """portrait 引导含固定角度 + 「有依据才写」护栏，且仍是角色口吻（非判词腔）。"""
    import json as _json
    llm = FakeLLM(raw=_json.dumps({"content": "摘要", "portrait": "观察"}, ensure_ascii=False))
    a = _agent(tmp_path, llm=llm)
    ids = []
    for txt in ("周一加班到十一点", "周二继续改方案", "周三终于提测了"):
        mid = a.memory.store_message("user", txt)
        ids.append(mid)
        a._store_candidate({"kind": "shared_episode", "content": txt,
                            "source_message_id": mid, "confidence": 0.9,
                            "subject": "user", "source": "rule_extract"})
    out = a.maybe_nightly_digest()
    assert out.get("created") is True
    task = llm.calls[-1]["messages"][-1]["content"]
    assert "什么时候最爱来找我" in task and "回避" in task and "有依据才写" in task


# ---------- 时间回显剥除（09-07 真机增量病灶） ----------

def test_strip_time_echo_single_double_and_hallucinated():
    from veranima.core.agent import Agent
    s = Agent._strip_time_echo
    # 单前缀（含/不含星期都剥）
    assert s("[2026-09-07 12:30:11 周一] 好，知道你还没真睡。") == "好，知道你还没真睡。"
    assert s("[2026-09-07 12:30:11] 正文") == "正文"
    # 双前缀拼接（#759/#761 实锤形态：第二个还是幻觉时间）
    assert s("[2026-09-07 12:30:30 周一] [2026-09-07 12:32:01 周一] 知道了。") == "知道了。"
    # 换行后的第二个也剥
    assert s("第一行。\n[2026-09-07 12:32:01 周一] 第二行。") == "第一行。\n第二行。"
    # 正文中间的日期引用不动（不是行首标记=人话）
    assert s("记得 2026-09-07 12:30:11 那条吗") == "记得 2026-09-07 12:30:11 那条吗"


# ---------- 编造闸（09-07 用户裁决：猜要猜出声，不许当既定事实） ----------

def test_fabrication_gate_strips_claim_keeps_question():
    """出口闸已按 09-07 用户二次裁决撤销（真人的记忆错觉=拟真的一部分，硬删
    是矫枉过正）——留下的方向断言：system 必须教『事实边界』=不许当既定事实、
    不确定用问句猜。闸的代码与 _fabrication_gate 测试一并删除。"""
    from veranima.core.agent import Agent
    instr = Agent._time_context_instruction()
    assert "【事实边界】" in instr and "论文吗" in instr
    assert not hasattr(Agent, "_fabrication_gate")


# ---------- 动态睡窗闸（09-07 真机案底：1:52 给睡着的凛补发 D02） ----------

def test_moment_gate_blocks_sleeping_catch_up(tmp_path):
    import dataclasses
    import datetime
    import pathlib
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.core.state import AgentState
    from veranima.memory.store import MemoryStore
    card = CharacterCard(name="小V", first_mes="你好")
    mem = MemoryStore(db_path=str(tmp_path / "m.db"), config={}, provider=FakeEmbed())
    a = Agent(card=card, memory=mem, llm=FakeLLM(), state=AgentState(), config={})
    a.role_key = "lin"

    class _Ctx:
        activity_category = "sleep_window"

    class _RT:
        def __init__(self, sleeping):
            self.sleeping = sleeping

        def current_context(self, when):
            return _Ctx()

    now = datetime.datetime(2026, 9, 7, 1, 52, tzinfo=datetime.timezone.utc)
    # 真 runtime 判定路径（schedule_runtime=None 的裸 Agent 不受影响）
    a.schedule_runtime = _RT(sleeping=False)
    assert a.moments._gate(now, {"moments": {"enabled": True}}) == "asleep"
    a.schedule_runtime = _RT(sleeping=True)
    assert a.moments._gate(now, {"moments": {"enabled": True}}) == "asleep"
    # catch_up 也不越过（tick 硬闸名单含 asleep）→ 0 发布
    assert a.moments.tick(now=now, catch_up=True) == 0
    assert mem.con.execute("select count(*) from moments").fetchone()[0] == 0
    # 醒着+活动非 sleep_window → 正常放行（闸不误伤日间发布）
    class _Ctx2:
        activity_category = "social"

    class _RT3(_RT):
        def current_context(self, when):
            return _Ctx2()
    a.schedule_runtime = _RT3(sleeping=False)
    assert a.moments._gate(now, {"moments": {"enabled": True}}) == ""


# ---------- 角色翻聊天记录（09-07 用户裁决：被要求找回时说"我翻翻"然后真翻） ----------

def test_search_messages_role_isolation(tmp_path):
    """共享库按会话隔离：凛翻不到许眠窗口的对话（09-04 审计#2 同纪律）。"""
    mem = MemoryStore(db_path=str(tmp_path / "iso.db"), config={}, provider=FakeEmbed())
    mem.store_message("user", "周五要交毕设初稿", role_id="lin")
    mem.store_message("user", "周五的体检复查安排好了", role_id="xumian")
    hits = mem.search_messages("周五", role_id="lin")
    assert len(hits) == 1 and "初稿" in hits[0]["content"]
    hits = mem.search_messages("周五", role_id="xumian")
    assert len(hits) == 1 and "体检" in hits[0]["content"]
    assert len(mem.search_messages("周五")) == 2   # 不带 role=旧口径全量不变


def test_judges_recall_evidence_coerce():
    from veranima.core.judges import _coerce
    j = _coerce({"recall_evidence": "体检 复查"})
    assert j.recall_evidence == "体检 复查"
    assert _coerce({"recall_evidence": "x" * 50}).recall_evidence == "x" * 20
    assert _coerce({}).recall_evidence == ""


def test_recall_evidence_block(tmp_path):
    """翻记录注入块：命中→带时间原话+「别说不记得」；查无→诚实模板不给编造
    留台阶；隔离→xumian 的 Agent 翻不到 lin 窗口的对话（端到端 handle 接线
    留给真机链路验——假 LLM 喂不动 im 结构化协议）。"""
    a = _agent(tmp_path)
    a.role_key = "xumian"
    a.memory.store_message("user", "中饭点了m记，薯条确实好吃", role_id="xumian")
    a.memory.store_message("assistant", "记下了，不吃辣这条", role_id="lin")  # 别的会话，不该被翻到
    blk = a._recall_evidence_block("薯条")
    assert "【翻到的聊天记录】" in blk and "m记" in blk and "你：" in blk
    assert "lin" not in blk and "记下了" not in blk   # 跨会话隔离
    assert "没有找到" in a._recall_evidence_block("火锅店")
