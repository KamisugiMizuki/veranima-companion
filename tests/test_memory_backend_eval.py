"""MEMORY_BACKEND_EVAL.md 四项机制增量落地行为测试。

M-A 强度：decay 不动 core_profile；召回命中强化；强度参与排序。
M-B 双时间线：valid_from 未到期的记忆不进入普通召回；recall_asof 审计接口。
M-C 夜间整理：LLM 摘要 → ADD-only 候选校验入库，原始片段降权，当日去重，垃圾输出跳过。
M-D 审核准入（默认关）：开启后低置信候选进队列，批准才成为可召回记忆。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            out.append([b / 255.0 for b in h[:8]])
        return out


class FakeLLM:
    """返回构造时给定的原始输出。"""

    base_url = "http://fake"

    def __init__(self, raw: str = ""):
        self.raw = raw
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self.raw


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(
        db_path=str(tmp_path / "t.db"),
        config={"decay_enabled": True, "importance_base_s": 3600},
        provider=FakeEmbed(),
    )
    yield s
    s.con.close()


# ---------- M-A ----------

def test_decay_skips_core_profile(store):
    core = store.store("core_profile", "角色身份事实", importance=0.9)
    sem = store.store("semantic", "用户喜欢深夜写代码", importance=0.9)
    store.con.execute(
        "UPDATE memories SET updated_at='2020-01-01T00:00:00+00:00' WHERE id IN (?,?)",
        (core.id, sem.id),
    )
    store.con.commit()
    frozen = store.get(core.id).updated_at  # 手动改写后再取基准
    store.decay()
    assert store.get(core.id).strength == 1.0  # 身份层永不衰减
    assert store.get(core.id).updated_at == frozen  # decay 不触碰身份层时间戳
    assert store.get(sem.id).strength < 1.0


def test_recall_hit_reinforces_strength(store):
    a = store.store("semantic", "用户在准备研究生考试")
    b = store.store("semantic", "用户养了一只橘猫")
    # 新记忆初始满强度；先压低再验证命中强化与 cap 行为
    store.con.execute("UPDATE memories SET strength=0.5 WHERE id IN (?,?)", (a.id, b.id))
    store.con.commit()
    hits = store.recall("研究生考试", top_k=1)
    assert [e.id for e in hits][0] == a.id
    assert store.get(a.id).strength == pytest.approx(0.55)
    assert store.get(b.id).strength == pytest.approx(0.5)  # 未命中的不涨
    again = store.recall("研究生考试", top_k=1)
    assert [e.id for e in again][0] == a.id
    assert store.get(a.id).strength == pytest.approx(0.6)
    # 连续命中到上限不再溢出
    for _ in range(20):
        store.recall("研究生考试")
    assert store.get(a.id).strength <= 1.0


def test_score_prefers_higher_strength_when_other_signals_equal(store):
    e_low = store.store("semantic", "共同经历样本甲", importance=0.5)
    e_high = store.store("semantic", "共同经历样本乙", importance=0.5)
    store.con.execute("UPDATE memories SET strength=0.2 WHERE id=?", (e_low.id,))
    store.con.execute("UPDATE memories SET strength=1.0 WHERE id=?", (e_high.id,))
    store.con.commit()
    intent = store._temporal_intent("共同经历")
    s_low = store._score_entry(store.get(e_low.id), sim=None, fts_hit=True, intent=intent, query="共同经历")
    s_high = store._score_entry(store.get(e_high.id), sim=None, fts_hit=True, intent=intent, query="共同经历")
    assert s_high > s_low


# ---------- M-B ----------

def test_valid_from_gates_recall(store):
    e = store.store("semantic", "用户下个月开始实习")
    store.con.execute(
        "UPDATE memories SET meta=json_set(meta,'$.valid_from','2099-01-01T00:00:00+00:00') WHERE id=?",
        (e.id,),
    )
    store.con.commit()
    assert not store.get(e.id).is_active()
    assert store.recall("实习") == []


def test_is_active_true_without_window(store):
    e = store.store("semantic", "普通记忆")
    assert store.get(e.id).is_active()


def test_recall_asof_audit_view(store):
    early = store.store("semantic", "早期事件记录一")
    late = store.store("semantic", "晚期事件记录二")
    store.con.execute(
        "UPDATE memories SET created_at='2026-01-01T00:00:00+00:00', updated_at='2026-01-01T00:00:00+00:00' WHERE id=?",
        (early.id,),
    )
    store.con.execute(
        "UPDATE memories SET created_at='2026-08-01T00:00:00+00:00', updated_at='2026-08-01T00:00:00+00:00' WHERE id=?",
        (late.id,),
    )
    store.con.commit()
    asof_june = [e.content for e in store.recall_asof("2026-06-01T00:00:00+00:00")]
    assert "早期事件记录一" in asof_june
    assert "晚期事件记录二" not in asof_june
    asof_sep = [e.content for e in store.recall_asof("2026-09-01T00:00:00+00:00")]
    assert "晚期事件记录二" in asof_sep


# ---------- M-D ----------

def _make_agent(tmp_path, llm, memory_config=None):
    """轻量 Agent 构造（对齐 tests/test_agent.py 的 Fake 组合方式）。"""
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.core.state import AgentState
    from veranima.memory.store import MemoryStore

    card = CharacterCard(name="小V", first_mes="你好")
    memory = MemoryStore(
        db_path=str(tmp_path / "a.db"),
        config={"decay_enabled": False, **(memory_config or {})},
        provider=FakeEmbed(),
    )
    agent = Agent(
        card=card, memory=memory, llm=llm, state=AgentState(),
        config={
            "chat": {"proactive_message_prob": 0.0},
            "proactive": {"enabled": False},
            "memory": dict(memory_config or {}),
        },
    )
    yield agent
    agent.memory.con.close()


def test_low_confidence_queued_when_review_enabled(tmp_path):
    llm = FakeLLM("")
    agent = next(_make_agent(tmp_path, llm, {"review_inbox_enabled": True, "review_confidence_below": 0.6}))
    cand = {"kind": "user_fact", "content": "用户自称怕打雷", "source_message_id": 1, "confidence": 0.4, "source": "rule_extract"}
    agent._store_candidate(cand)
    queued = agent.memory.list_review()
    assert len(queued) == 1
    assert agent.memory.recall("怕打雷") == []  # 未批准不可召回


def test_approve_promotes_and_reject_drops(tmp_path):
    llm = FakeLLM("")
    agent = next(_make_agent(tmp_path, llm, {"review_inbox_enabled": True}))
    cand = {"kind": "user_fact", "content": "用户讨厌香菜", "source_message_id": 2, "confidence": 0.4, "source": "rule_extract"}
    agent._store_candidate(cand)
    item = agent.memory.list_review()[0]
    assert agent.review_memory(item["id"], approve=True) is True
    assert agent.memory.recall("香菜") != []
    assert agent.memory.list_review() == []
    # 第二条走拒绝
    agent._store_candidate({**cand, "content": "用户不吃苦瓜"})
    item2 = agent.memory.list_review()[0]
    assert agent.review_memory(item2["id"], approve=False) is True
    stored = agent.memory.con.execute(
        "SELECT count(*) FROM memories WHERE content LIKE '%苦瓜%'"
    ).fetchone()[0]
    assert stored == 0  # 拒绝的候选未写入任何记忆
    assert agent.memory.list_review() == []


def test_high_confidence_bypasses_inbox_by_default(tmp_path):
    llm = FakeLLM("")
    agent = next(_make_agent(tmp_path, llm))  # 默认关闭收件箱
    agent._store_candidate({"kind": "user_fact", "content": "用户偏好简洁回复", "source_message_id": 3, "confidence": 0.95, "source": "rule_extract"})
    assert agent.memory.list_review() == []
    assert agent.memory.recall("简洁") != []


# ---------- M-C ----------

_DIGEST_PROMPT_MARK = "近期情节"


def _digest_llm(content: str) -> FakeLLM:
    return FakeLLM(json.dumps({"content": content}, ensure_ascii=False))


def test_nightly_digest_creates_summary_and_downweights_sources(tmp_path):
    llm = _digest_llm("这几天用户一直在赶项目进度，比较疲惫。")
    agent = next(_make_agent(tmp_path, llm))
    ids = []
    for txt in ("周一加班到十一点", "周二继续改方案", "周三终于提测了"):
        mid = agent.memory.store_message("user", txt)
        ids.append(mid)
        agent._store_candidate({
            "kind": "shared_episode", "content": txt,
            "source_message_id": mid, "confidence": 0.9, "subject": "user", "source": "rule_extract",
        })
    out = agent.maybe_nightly_digest()
    assert out.get("created") is True
    assert len(llm.calls) == 1
    # 摘要走既有校验入库且带来源
    hits = agent.memory.recall("项目进度")
    assert any((e.meta or {}).get("digest_date") for e in hits)
    # 原始片段降权但不删除
    src = agent.memory.get(hits[0].id)
    assert agent.memory.stats()["memories"]["episodic"] >= 4
    raw_rows = agent.memory.con.execute(
        "SELECT count(*) FROM memories WHERE meta LIKE '%\"kind\": \"shared_episode\"%'"
    ).fetchone()[0]
    assert raw_rows >= 3
    lowered = agent.memory.con.execute(
        "SELECT strength FROM memories WHERE id IN ({})".format(",".join("?" * 3)), ids
    ).fetchall()
    assert all(r[0] < 1.0 for r in lowered)


def test_nightly_digest_same_day_skipped(tmp_path):
    llm = _digest_llm("摘要内容。")
    agent = next(_make_agent(tmp_path, llm))
    for txt in ("a记录", "b记录", "c记录"):
        mid = agent.memory.store_message("user", txt)
        agent._store_candidate({"kind": "shared_episode", "content": txt, "source_message_id": mid, "source": "rule_extract"})
    assert agent.maybe_nightly_digest().get("created") is True
    calls_after_first = len(llm.calls)
    assert agent.maybe_nightly_digest().get("created") is False
    assert len(llm.calls) == calls_after_first  # 当日不再调用 LLM


def test_nightly_digest_bad_llm_output_skipped(tmp_path):
    llm = FakeLLM("这不是JSON{{{")
    agent = next(_make_agent(tmp_path, llm))
    for txt in ("x情节一", "y情节二", "z情节三"):
        mid = agent.memory.store_message("user", txt)
        agent._store_candidate({"kind": "shared_episode", "content": txt, "source_message_id": mid, "source": "rule_extract"})
    out = agent.maybe_nightly_digest()
    assert out.get("created") is False
    assert agent.memory.stats()["memories"]["episodic"] == 3  # 无新增


def test_nightly_digest_needs_minimum_material(tmp_path):
    llm = _digest_llm("不该被调用")
    agent = next(_make_agent(tmp_path, llm))
    out = agent.maybe_nightly_digest()
    assert out.get("created") is False
    assert llm.calls == []


def test_nightly_digest_budget_floor_and_cooldown(tmp_path):
    """09-02 真机实锤：digest 直调 chat(256) 被 reasoning 烧空且每分钟重试。
    ① 预算不得低于 short_task_max_tokens 下限；② 坏输出后进冷却不再掷。"""
    llm = FakeLLM("")  # 空 content = finish_reason=length 的测试替身
    agent = next(_make_agent(tmp_path, llm))
    for txt in ("甲情节", "乙情节", "丙情节"):
        mid = agent.memory.store_message("user", txt)
        agent._store_candidate({"kind": "shared_episode", "content": txt,
                                "source_message_id": mid, "source": "rule_extract"})
    out = agent.maybe_nightly_digest()
    assert out.get("created") is False and len(llm.calls) == 1
    assert llm.calls[0]["max_tokens"] >= 1024          # 预算下限（默认 config）
    assert agent.maybe_nightly_digest().get("reason") == "cooldown"
    assert len(llm.calls) == 1                          # 冷却期零调用
