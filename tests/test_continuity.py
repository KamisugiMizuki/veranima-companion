"""R1 连续性测试（R1_SPEC 7）。

覆盖：候选校验/规则提取/去重/版本链/跨重启状态/状态 apply/注入格式。
"""
from __future__ import annotations

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore, validate_candidate


def _card() -> CharacterCard:
    return CharacterCard.from_dict({
        "name": "Yuki",
        "personality": "洒脱",
        "veranima": {"tones": ["中性", "调侃"]},
    })


def _store(tmp_path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "t.db"), config={"embedding_model": "none"})


def test_validate_candidate_ok():
    cand = {"kind": "user_fact", "content": "用户喜欢下雨天", "confidence": 0.8,
            "importance": 0.6, "source": "rule_extract", "source_message_id": 1}
    assert validate_candidate(cand) == []


def test_validate_candidate_rejects():
    assert validate_candidate({"kind": "evil", "content": "x", "source_message_id": 1})
    assert validate_candidate({"kind": "user_fact", "content": "", "source_message_id": 1})
    assert validate_candidate({"kind": "user_fact", "content": "x" * 501, "source_message_id": 1})
    assert validate_candidate({"kind": "user_fact", "content": "x", "confidence": 1.5,
                               "importance": -0.1, "source_message_id": 1})
    assert validate_candidate({"kind": "user_fact", "content": "x"})  # 缺 source


def test_validate_conversation_event_contract():
    base = {
        "kind": "conversation_event", "content": "待跟进的临时事项",
        "confidence": 0.8, "importance": 0.6, "source": "llm_extract",
        "source_message_id": 1, "topic": "临时事项", "status": "active",
        "follow_up_days": 3,
    }
    assert validate_candidate(base) == []
    assert validate_candidate({**base, "topic": ""})
    assert validate_candidate({**base, "follow_up_days": 8})
    assert validate_candidate({**base, "status": "done"})
    assert validate_candidate({**base, "status": "completed", "follow_up_days": 1})


def test_rule_extract_user_fact():
    a = object.__new__(Agent)  # 纯规则方法，跳过 __init__（不依赖 memory/LLM）
    cands = a._rule_extract("我喜欢下雨天", 1)
    assert len(cands) == 1
    assert cands[0]["kind"] == "user_fact"
    assert cands[0]["content"] == "我喜欢下雨天"


def test_rule_extract_commitment_question_skipped():
    a = object.__new__(Agent)
    # 问句不命中承诺
    cands = a._rule_extract("你答应过我吗？", 1)
    assert all(c["kind"] != "commitment" for c in cands)


def test_rule_extract_shared_episode_needs_event():
    a = object.__new__(Agent)
    assert a._rule_extract("上次一起吃了火锅", 1)[0]["kind"] == "shared_episode"
    # 无事件/结果表达不提取
    assert not a._rule_extract("我们一起", 1)


def test_state_apply_and_snapshot():
    st = AgentState()
    st.apply("user_message", {"social_appetite": 0.3, "attention_topic": "游戏"}, cause="用户聊游戏")
    assert st.social_appetite == 0.3
    assert st.attention_topic == "游戏"
    assert st.last_cause == "用户聊游戏"
    snap = st.to_snapshot()
    st2 = AgentState.from_snapshot(snap)
    assert st2.social_appetite == 0.3
    assert st2.attention_topic == "游戏"
    assert st2.last_cause == "用户聊游戏"


def test_state_old_snapshot_compat():
    # 旧快照（无 R1 字段）→ 默认值
    st = AgentState.from_snapshot({"energy": 80.0, "mood": "平静", "attachment": 0.5,
                                   "mood_score": 0.0, "total_messages": 3})
    assert st.social_appetite == 0.8
    assert st.attention_scene == "normal"
    assert st.last_cause == "startup"


def test_memory_r1_layer_map(tmp_path):
    store = _store(tmp_path)
    e = store.store("user_fact", "用户喜欢下雨天")
    assert e.layer == "semantic"  # R1 类型名映射到旧 layer
    hits = store.recall("下雨天", top_k=5, layer="user_fact")
    assert any(h.content == "用户喜欢下雨天" for h in hits)


def test_memory_version_chain(tmp_path):
    store = _store(tmp_path)
    e1 = store.store("user_fact", "用户喜欢猫")
    e2 = store.update_latest(e1.id, "用户喜欢猫和狗", meta={"supersedes": e1.id})
    assert e2.version == e1.version + 1
    assert e2.meta["supersedes"] == e1.id
    assert store.get(e1.id) is not None  # 旧版本保留


def test_format_memory_line_prefix():
    from veranima.core.prompts import format_memory_line
    from veranima.memory.store import MemoryEntry
    e = MemoryEntry(id=1, layer="semantic", content="用户喜欢下雨天", strength=0.9,
                    meta={"kind": "user_fact"})
    line = format_memory_line(e)
    assert line.startswith("[用户事实|置信度:高]")
    e2 = MemoryEntry(id=2, layer="episodic", content="一起看过烟花", strength=0.6,
                     meta={"kind": "shared_episode", "event_time": "上周"})
    line2 = format_memory_line(e2)
    assert line2.startswith("[共同经历|置信度:中|时间:上周]")


def test_conversation_event_uses_versioned_lifecycle(tmp_path):
    store = _store(tmp_path)
    agent = object.__new__(Agent)
    agent.memory = store

    agent._store_candidate({
        "kind": "conversation_event",
        "topic": "临时作息",
        "content": "用户最近的日常安排发生了临时偏移",
        "status": "active",
        "intent": "remind",
        "follow_up_days": 3,
        "confidence": 0.82,
        "importance": 0.65,
        "source": "llm_extract",
        "source_message_id": 11,
    })
    first = next(e for e in store.list_layer("episodic")
                 if e.meta.get("kind") == "conversation_event")

    assert first.meta["topic"] == "临时作息"
    assert first.meta["status"] == "active"
    assert first.meta["source_message_ids"] == [11]
    assert first.meta["expires_at"]

    agent._store_candidate({
        "kind": "conversation_event",
        "topic": "临时作息",
        "content": "用户说明这项临时安排已经结束",
        "status": "completed",
        "intent": "check_in",
        "follow_up_days": 0,
        "confidence": 0.9,
        "importance": 0.65,
        "source": "llm_extract",
        "source_message_id": 19,
    })
    current = [e for e in store.list_layer("episodic")
               if e.meta.get("kind") == "conversation_event"]

    assert len(current) == 1
    assert current[0].version == 2
    assert current[0].meta["status"] == "completed"
    assert current[0].meta["supersedes"] == first.id
    assert current[0].meta["source_message_ids"] == [11, 19]
    assert current[0].meta.get("expires_at") == ""


def test_conversation_event_status_change_versions_even_when_content_is_same(tmp_path):
    store = _store(tmp_path)
    agent = object.__new__(Agent)
    agent.memory = store
    base = {
        "kind": "conversation_event", "topic": "阶段性事项",
        "content": "用户有一项阶段性事项", "intent": "check_in",
        "confidence": 0.85, "importance": 0.6, "source": "llm_extract",
    }
    agent._store_candidate({**base, "status": "active", "follow_up_days": 2, "source_message_id": 1})
    agent._store_candidate({**base, "status": "paused", "follow_up_days": 0, "source_message_id": 2})

    current = [e for e in store.list_layer("episodic")
               if e.meta.get("kind") == "conversation_event"]

    assert len(current) == 1
    assert current[0].version == 2
    assert current[0].meta["status"] == "paused"
