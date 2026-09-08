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


# ---------- ⑩ 动态裁剪（R1_SPEC 6 / MEMORY_SPEC 16 三个配置项接线） ----------

def _trim_store(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "t.db"), config={"embedding_model": "none"})
    store.store("episodic", "用户上个月通过了考试", importance=0.9)
    return store


def test_recall_min_score_is_a_relevance_floor(tmp_path):
    class _P:                      # 假 embedding provider：让 sim 通道走通（venv 无 fastembed）
        dim = 4

        def embed(self, texts):
            return [[0.0] * 4 for _ in texts]

    store = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=_P())
    store.store("episodic", "用户上个月通过了考试", importance=0.9)
    store.store("episodic", "用户昨天加班到十一点", importance=0.6)
    ids = [e.id for e in store.list_layer("episodic", limit=10)]
    store._knn = lambda vec, k: [(ids[0], 0.9), (ids[1], 0.1)]  # 只有第一条与 query 相关

    kept = store.recall("考试", top_k=5, layer="episodic", min_score=0.45)
    assert [e.id for e in kept] == [ids[0]]                          # 0.1 < 0.45 → 裁掉
    assert len(store.recall("考试", top_k=5, layer="episodic")) == 2  # 地板关 → 全留
    assert len(store.recall("考试", top_k=5, layer="episodic", min_score=0.05)) == 2  # 地板低 → 不误伤


def test_build_prompt_forwards_trim_knobs(tmp_path):
    from veranima.core import prompts as P
    from veranima.core.state import AgentState

    store = _trim_store(tmp_path)
    store.store("core_profile", "用户是浙大生仪学院的研究生，正在做毕业设计。" * 2, importance=0.9)

    class _Recorder:  # 记录 recall 参数，其余透传真库
        def __init__(self, inner):
            self.inner, self.calls = inner, []

        def recall(self, q, *, top_k=5, layer=None, min_score=0.0):
            self.calls.append((layer, top_k, min_score))
            return self.inner.recall(q, top_k=top_k, layer=layer, min_score=min_score)

        def recent_messages(self, limit=20, channel=None, **kw):
            return [{"role": "user", "content": "考试"}]

        def __getattr__(self, name):
            return getattr(self.inner, name)

    rec = _Recorder(store)
    card, state = CharacterCard(name="测试卡"), AgentState()
    full = P.build_system_prompt(card, state, rec, channel="im",
                                 recall_top_k=2, recall_floor=0.4)
    assert ("semantic", 2, 0.4) in rec.calls and ("episodic", 2, 0.4) in rec.calls
    capped = P.build_system_prompt(card, state, rec, channel="im", max_brief_chars=1)
    assert "浙大生仪学院" in full            # 默认总预算 → 常驻档案注入
    assert "浙大生仪学院" not in capped      # 总预算压到 1 字 → 整条丢弃


def test_sibling_prompt_paths_consume_same_trim_config(tmp_path):
    """prompt 三个调用点共用 _prompt_trim_kwargs。

    09-08 实锤：只有 handle 接了 config，digest / _short_task 直调
    build_system_prompt 走默认值（MuMu 日志 floor=0.00 cap=0，配置改了不生效）。
    """
    from veranima.core.agent import Agent

    store = _trim_store(tmp_path)
    store.store("core_profile", "用户是浙大生仪学院的研究生，正在做毕业设计。" * 2, importance=0.9)
    llm = _RecLLM("{}")
    agent = Agent(card=CharacterCard(name="测试卡"), memory=store, llm=llm, state=None,
                  config={"memory": {"max_injected_chars": 0}})
    agent._short_task("写一句问候")
    assert any("浙大生仪学院" in s for s in _systems(llm))
    llm.calls.clear()
    agent.config["memory"] = {"max_injected_chars": 1}   # 上限 1 字 → 画像整条裁掉
    agent._short_task("写一句问候")
    assert not any("浙大生仪学院" in s for s in _systems(llm))
