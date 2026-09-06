"""2026-09-06 真机导出逐条筛查的行为级验收（exports/phone-20260906 原证据）。

对应缺陷（真机原话可回查）：
- 苏醒播报复读（09-05「醒了？13小时52分」×4、09-06「快十一个小时」×3+）：
  proactive_feedback 同名键记了 6 次还在重发——旁路去重必须长在周期行上
- 内部指令原句外溢（08-28「不要输出思考过程…raw JSON」整段发出）：
  封闭词表未含 system prompt 原句 + 逐行删留英文残句
- 「上次你说那事」逐字连发（09-04/09-05）：heartbeat 降级池 pool[0] 写死
- 「三个小时没动静」估时错（09-03 15:36，实际 14 分钟）：联想素材过期照发
- 「周五嘛」发在周六（09-05）：prompt 时间戳不带星期，模型自己推算
- 判词记忆污染（episodic 87 条中 60+「用户认真回应了直接问题」）：
  recall/考古/digest 把机器判词当共同记忆灌回 prompt
- 餐名跨日卡死（09-02 凌晨「早饭」）：改写式改名会污染次日模板
"""
from __future__ import annotations

import datetime

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.proactive import MEAL_SLOTS, MealReminderScheduler
from veranima.core.reply import parse_reply
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    def __init__(self, reply="好的。"):
        self.reply = reply

    def chat(self, messages, **kw):
        return self.reply

    def chat_structured(self, messages, **kw):
        return self.reply

    def is_model_loaded(self):
        return True

    low_energy_max_tokens = 256


def _agent(tmp_path, llm=None):
    card = CharacterCard(name="小V", first_mes="你好")
    memory = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    a = Agent(card=card, memory=memory, llm=llm or FakeLLM(),
              state=AgentState(), config={})
    return a, memory


# ---------- 1) 苏醒总结认领：周期行级幂等（旁路通道根修） ----------

def test_sleep_summary_claim_is_atomic(tmp_path):
    _a, memory = _agent(tmp_path)
    ts = (datetime.datetime.now(datetime.timezone.utc)
          - datetime.timedelta(hours=9)).isoformat(timespec="seconds")
    memory.open_sleep_cycle(ts)
    cycle = memory.close_sleep_cycle(datetime.datetime.now(datetime.timezone.utc)
                                     .isoformat(timespec="seconds"))
    assert cycle is not None
    # 未写总结前可认领；写总结（update_sleep_summary）自动占用发送权
    assert memory.claim_sleep_summary(cycle["id"]) is True
    memory.update_sleep_summary(cycle["id"], "醒了？九个小时。")
    assert memory.claim_sleep_summary(cycle["id"]) is False       # 旁路拿不到第二次
    assert memory.claim_sleep_summary(cycle["id"]) is False       # 幂等


def test_wake_path_consumes_summary_claim(tmp_path, monkeypatch):
    """用户报「醒了」→ 主链融合素材 + 周期认领 → bridge 旁路永远零输出。"""
    a, memory = _agent(tmp_path)
    a.state.user_asleep = True
    now = datetime.datetime(2026, 9, 6, 1, 54, tzinfo=datetime.timezone.utc)
    memory.open_sleep_cycle((now - datetime.timedelta(hours=11)).isoformat(timespec="seconds"))
    monkeypatch.setattr(a, "_sleep_cycle_summary", lambda c: "醒了？看你睡了快十一个小时。")
    assert a._note_sleep_report("堂堂起床！", now) == "wake"
    cycle = memory.latest_closed_cycle()
    assert memory.claim_sleep_summary(cycle["id"]) is False  # update_sleep_summary 已认领


# ---------- 2) 内部指令原句外溢：整段杀，不留英文残句 ----------

def test_reasoning_fragment_killed_whole_paragraph():
    frag = ('" - Wait, "不要输出思考过程... 或 Markdown"。So I should output raw JSON, '
            'without markdown codeblocks?\n    *   Actually, usually agent')
    p = parse_reply(frag, channel="im")
    joined = (p.text or "") if p.segments else ""
    assert "Actually" not in joined and "raw JSON" not in joined
    # 正常多行台词不误杀
    normal = "行，那我问你。\n今天训练跑得挺顺。\n晚饭记得吃。"
    assert parse_reply(normal, channel="im").text == normal


# ---------- 3) heartbeat 降级池不再逐字复读 ----------

def test_heartbeat_fallback_rotates_and_silences(tmp_path):
    class DeadLLM(FakeLLM):
        def chat(self, messages, **kw):
            raise RuntimeError("llm down")

        def chat_structured(self, messages, **kw):
            raise RuntimeError("llm down")

    a, memory = _agent(tmp_path, llm=DeadLLM())
    # 对话闭合（最后一条=assistant）+ 无素材可用：降级池必须避开已发模板
    a.record_proactive_message("随便聊点什么收尾")
    for p in [
        "（刚在整理聊天记录）上次你说那事，后来有后续了吗？",
        "刚闲着没事翻了翻咱俩的聊天记录，发现你之前念叨的东西挺多的……最近都还好吗？",
        "（离线整理完毕）我突然想起你上次说的那个计划，后来怎么样了？",
    ]:
        memory.store_message("assistant", p, 60, "开心")
        a._append_history_message("assistant", p)
    assert a.heartbeat() == ""  # 三条全用过=闭嘴，不再第四遍「上次你说那事」


# ---------- 4) 联想素材保鲜：生成后用户已回话 → 出池不补发 ----------

def test_stale_context_probe_dropped_from_pool(tmp_path):
    a, memory = _agent(tmp_path)
    now = datetime.datetime.now()
    a._last_proactive_sent_at = (now - datetime.timedelta(hours=2)
                                ).astimezone().isoformat(timespec="seconds")
    user_ts = (now - datetime.timedelta(minutes=10)).isoformat()
    memory.store_message("user", "午饭没吃，待会儿再说", 60, "开心")
    memory.con.execute("UPDATE messages SET created_at=? WHERE id=(SELECT MAX(id) FROM messages)",
                       (user_ts,))
    memory.con.commit()
    # 池里躺着一小时前的联想素材（生成于用户回话之前）
    a._ritual_pending.append({
        "source": "context_probe", "text": "三个小时没动静，该不会又焊在工位上了吧",
        "ts": (now - datetime.timedelta(hours=1)).timestamp(),
        "role": a.role_key or a.card.name,
    })
    a.gate.decide = lambda cand, **kw: type("D", (), {"allow": True})()
    a.tick_proactive(now=now.timestamp(), persist=False, commit=False)
    # 断言是池（素材被销毁不补发），不依赖本轮发了什么
    assert all(m.get("source") != "context_probe" for m in a._ritual_pending)


# ---------- 5) 时间戳前缀带星期（「周五嘛」发在周六的根修） ----------

def test_history_prefix_carries_weekday():
    saturday = "2026-09-05T03:44:27+00:00"  # UTC→本地(+8)=09-05 周六 11:44
    line = Agent._format_history_content("（小声）周五嘛……", saturday)
    assert "周六" in line


# ---------- 6) 判词记忆不进召回/考古 ----------

def test_tension_ledger_entries_not_recallable(tmp_path):
    _a, memory = _agent(tmp_path)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    memory.store("episodic", "用户认真回应了直接问题（判断点）",
                 importance=0.7, confidence=0.85,
                 meta={"kind": "relational_tension_event", "event_type": "answered_question"})
    memory.store("episodic", "用户说m记的薯条确实好吃", importance=0.7, confidence=0.85,
                 meta={"kind": "conversation_event"})
    hits = memory.recall("用户认真回应了直接问题", top_k=5)
    assert not any("判断点" in h.content for h in hits)
    assert any("薯条" in h.content for h in hits)  # 正常记忆照常召回


# ---------- 7) 三餐文案模板跨日不卡死（改名挪到产出时） ----------

def test_meal_slots_never_mutated():
    m = MealReminderScheduler()
    m.adjust_to_user_cycle(22.43)
    m.adjust_to_user_cycle(7.5)   # 模拟次日回正（旧版：模板已被改坏，换不回来）
    for meal in ("breakfast", "lunch", "dinner"):
        assert m.slots[meal][1] == MEAL_SLOTS[meal][1]
