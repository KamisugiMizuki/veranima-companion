"""2026-09-08 接线验收：未接线功能落位后的行为断言。

覆盖（每项都是可观察行为，不是「代码里有这个名字」）：
- 卡内 prompt 块合并（22 标签 → 5 组，信息无损）
- capability 话题姿态注入（略知/完全不懂）
- 承诺清单在兑现后不塌陷（版本链修复）
- 承诺 → 牵挂线
- soothe / note_negative / note_repair_turn 三个零调用账本被真实回合驱动
- 冲突状态机 acknowledge / hold_boundary + 未闭合冲突进 prompt
- 已生效印记进 prompt
- 记忆复核队列批准后落库
- 自我模型章节随夜眠消化刷新
"""
from __future__ import annotations

import pytest

from veranima.core.character import CharacterCard
from veranima.core.judges import MessageJudgment
from veranima.memory.store import MemoryStore


class _RecLLM:
    """记录每次调用的假 LLM（判断点一次 + 回复一次）。"""

    base_url = "http://fake"
    low_energy_max_tokens = 512

    def __init__(self, reply: str = "嗯。") -> None:
        self.reply = reply
        self.calls: list = []

    def is_model_loaded(self) -> bool:
        return True

    def chat(self, messages, max_tokens=None):
        self.calls.append(messages)
        return self.reply

    def chat_structured(self, messages, max_tokens=None):
        self.calls.append(messages)
        return self.reply


def _agent(tmp_path, reply: str = "嗯。", **veranima):
    from veranima.core.agent import Agent
    card = CharacterCard(name="测试卡", veranima=veranima)
    store = MemoryStore(db_path=str(tmp_path / "t.db"), config={"embedding_model": "none"})
    return Agent(card=card, memory=store, llm=_RecLLM(reply), state=None, config={})


def _systems(llm) -> list[str]:
    return [m[0]["content"] for m in llm.calls if m and m[0].get("role") == "system"]


# ---------- ① 卡内块合并 ----------

def test_card_prompt_merges_blocks_without_losing_fields():
    card = CharacterCard(
        name="测试卡", description="概述文", personality="性格文", scenario="背景文",
        tones=["中性", "调侃"], mes_example="用户：在吗",
        veranima={"communication_style": "沟通文", "quirks": ["癖好一"],
                  "taboos": ["禁忌文"], "values": ["底线文"],
                  "core_drives": ["驱动力文"], "initial_affection": 0.5,
                  "inner_tensions": [{"left": "左", "right": "右"}]},
    )
    p = card.to_system_prompt()
    # 5 个语义组 + 对话示例（标签数从 22 降到 6）
    for group in ("【人格】", "【背景】", "【表达】", "【底线】", "【内核】", "【对话示例】"):
        assert group in p
    # 信息无损：每个字段名仍在（组内行首前缀）
    for label in ("概述：概述文", "性格：性格文", "设定：背景文", "沟通风格：沟通文",
                  "癖好：癖好一", "禁忌话题：禁忌文", "价值观底线：底线文",
                  "长期驱动力：驱动力文", "内在张力：左 / 右", "初始好感：0.5",
                  "可用语气：中性/调侃"):
        assert label in p
    # 旧的一字段一标签不再出现
    assert "【性格细节】" not in p and "【沟通风格】" not in p


# ---------- ② 话题能力姿态 ----------

def test_capability_stance_injected_for_weak_topic(tmp_path):
    a = _agent(tmp_path, capabilities={"完全不懂": ["美妆", "口红色号"]})
    a.handle("这个口红色号好看吗")
    assert any("【话题边界】" in s and "完全不懂" in s for s in _systems(a.llm))


def test_capability_stance_absent_for_strong_topic(tmp_path):
    a = _agent(tmp_path, capabilities={"完全不懂": ["美妆"]})
    a.handle("这个口红色号好看吗")
    assert not any("【话题边界】" in s for s in _systems(a.llm))


# ---------- ③ 承诺清单版本链 ----------

def test_open_promises_survives_one_marked_done(tmp_path):
    a = _agent(tmp_path)
    first = a.promises.record("明天记得提醒我带伞")
    second = a.promises.record("下周记得提醒我交房租")
    assert first and second
    assert len(a.promises.open_promises()) == 2
    a.promises.mark_done(first)
    rest = a.promises.open_promises()
    # 兑现一条不会把其余承诺挤出清单（旧实现按 provenance max(version) 会清空）
    assert [e.id for e in rest] == [second]
    a.promises.mark_cancelled(second)
    assert a.promises.open_promises() == []


# ---------- ④ 承诺 → 牵挂线 ----------

def test_promise_opens_thread(tmp_path):
    a = _agent(tmp_path)
    a.handle("明天记得提醒我带伞")
    topics = [r["topic"] for r in a.memory.thread_list(a.threads.role)]
    assert any("带伞" in t for t in topics)


# ---------- ⑤ 驱力/打断/张力三个账本 ----------

def test_positive_feedback_soothes_care_need(tmp_path):
    a = _agent(tmp_path)
    a.desires._d["care_need"] = [1.0, ""]
    a.handle("谢谢，说得对")
    assert a.desires._level("care_need") < 1.0


def test_negative_feedback_raises_interrupt_cooldown(tmp_path):
    a = _agent(tmp_path)
    assert a.interrupt_decider.decide(topic_count=9, prob=0.0) == 2
    a.handle("太长了")
    assert a.interrupt_decider.decide(topic_count=9, prob=0.0) == 0  # 冷却期


def test_repair_turn_counter_advances(tmp_path):
    a = _agent(tmp_path)
    a.handle("在吗")
    assert a.tension.state.consecutive_repair_turns == 1


# ---------- ⑥ 冲突状态机 ----------

def test_reply_admitting_fault_acknowledges_conflict(tmp_path):
    a = _agent(tmp_path, reply="对不起，是我没想周全。")
    a._conflicts.open("c1", cause="角色说错话")
    a.handle("你今天有点怪")
    assert a._conflicts.status("c1") == "acknowledged"


def test_user_violation_with_firm_reply_holds_boundary(tmp_path):
    a = _agent(tmp_path, reply="这不行。")
    a.turn_judgment = lambda text: MessageJudgment(conflict="violation")
    a.handle("你烦不烦")
    assert any(c["status"] == "boundary_held" for c in a._conflicts.open_conflicts())


def test_open_conflict_reaches_prompt(tmp_path):
    a = _agent(tmp_path)
    a._conflicts.open("c1", cause="上次那句话没说开")
    a.handle("在吗")
    assert any("【没消化的事】" in s and "没说开" in s for s in _systems(a.llm))


# ---------- ⑦ 印记 ----------

def test_active_imprint_reaches_prompt(tmp_path):
    a = _agent(tmp_path)
    for i in range(3):
        a._imprints.note("depth", 1.0, i, scope="对话")
    assert a._imprints.active_imprints()
    a.handle("在吗")
    assert any("【相处里长出来的】" in s for s in _systems(a.llm))


# ---------- ⑧ 记忆复核 ----------

def test_review_memory_approves_candidate_into_store(tmp_path):
    a = _agent(tmp_path)
    a.memory.queue_review(
        {"kind": "user_fact", "content": "用户常喝冷萃", "confidence": 0.5,
         "source_message_id": 1, "importance": 0.6},
        reason="confidence 0.5 < 0.6")
    items = a.memory.list_review()
    assert len(items) == 1
    assert a.review_memory(items[0]["id"], True) is True
    assert a.memory.list_review() == []
    assert any("冷萃" in e.content
               for e in a.memory.list_layer("semantic", limit=20, include_superseded=True))


def test_review_memory_rejects_without_storing(tmp_path):
    a = _agent(tmp_path)
    a.memory.queue_review(
        {"kind": "user_fact", "content": "用户常喝冷萃", "confidence": 0.5,
         "source_message_id": 1}, reason="low")
    items = a.memory.list_review()
    assert a.review_memory(items[0]["id"], False) is True
    assert not any("冷萃" in e.content
                   for e in a.memory.list_layer("semantic", limit=20, include_superseded=True))


# ---------- ⑨ 自我模型章节 ----------

def test_self_model_chapter_refresh_records_open_threads(tmp_path):
    a = _agent(tmp_path)
    a.memory.store_self_model_chapter(title="自我模型阶段 1", self_interpretation="起点")
    a.threads.from_user("明天答辩，紧张")
    a._refresh_self_model_chapter()
    chapter = a.memory.list_self_model_chapters(limit=1)[0]
    assert any("答辩" in t for t in chapter["open_threads"])
    assert chapter["period_end"]  # 收口到今日
