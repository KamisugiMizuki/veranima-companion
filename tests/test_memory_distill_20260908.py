"""2026-09-08 记忆库粒度修复（用户拍板：写入时蒸馏 + 存量逐条处理）。

真库审计（exports/mumu_20260908.db）：记忆库页 35 条可见条目里 25 条是原话
直存——「不是，没反应了？」被当用户事实、内部 slug、粘贴的聊天记录整块。
三个写入侧病灶：_CORRECTION_RULES 裸词「不是/错了」、偏好/事件直存 user_text、
promise 正则命中假设句。本文件按行为断言验收。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.distill import distill
from veranima.core.promises import PromiseBook
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    """只有 chat——蒸馏视为未裁决，调用方回退旧行为存原话。"""
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


class DistillLLM(FakeLLM):
    """按 prompt 里出现的关键词映射蒸馏结果；未映射 → null。"""

    def __init__(self, mapping):
        super().__init__()
        self.mapping = mapping

    def chat_structured(self, messages, **kw):
        text = messages[-1]["content"]
        for key, out in self.mapping.items():
            if key in text:
                return "null" if out is None else json.dumps({"memory": out}, ensure_ascii=False)
        return '{"memory": null}'


def _agent(tmp_path, llm=None):
    card = CharacterCard(name="小V", first_mes="你好")
    memory = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    return Agent(card=card, memory=memory, llm=llm or FakeLLM(),
                 state=AgentState(), config={})


def _judgment(kind="preference"):
    return SimpleNamespace(memory_kind=kind, emotion="none")


# ---------- 病灶一：裸词纠正规则 ----------

def test_plain_negation_is_not_a_correction(tmp_path):
    a = _agent(tmp_path)
    assert [c for c in a._rule_extract("不是，没反应了？", 1) if c["kind"] == "user_fact"] == []
    assert [c for c in a._rule_extract("好吧好吧我错了。今天有什么安排？", 2)
            if c["kind"] == "user_fact"] == []


def test_real_correction_still_detected(tmp_path):
    a = _agent(tmp_path)
    cands = a._rule_extract("这个不是我的错，是系统的问题", 1)
    assert any(c["kind"] == "user_fact" and c["confidence"] == 0.85 for c in cands)


# ---------- 病灶三：假设句不是承诺 ----------

def test_hypothetical_is_not_a_promise(tmp_path):
    pb = PromiseBook(_agent(tmp_path).memory)
    assert pb.extract("如果你能帮我记住的话我会很开心的") is None
    assert pb.record("如果你能帮我记住的话我会很开心的") is None
    assert pb.extract("帮我记住下周三要去体检") is not None


# ---------- 蒸馏器本体 ----------

def test_distill_parses_fenced_json_and_null():
    class L:
        def __init__(self, out):
            self.out = out

        def chat_structured(self, messages, **kw):
            return self.out

    assert distill("我特别喜欢下雨天",
                   llm=L('```json\n{"memory": "用户喜欢下雨天"}\n```')) == "用户喜欢下雨天"
    assert distill("我特别喜欢下雨天", llm=L('{"memory": null}')) is None

    class Boom:
        def chat_structured(self, messages, **kw):
            raise AssertionError("垃圾输入不该调模型")

    assert distill("reminder:myhealthnote-1", llm=Boom()) is None
    assert distill("a\nb\nc", llm=Boom()) is None


def test_distill_cache_avoids_duplicate_calls(tmp_path):
    calls: list[str] = []

    class C(DistillLLM):
        def chat_structured(self, messages, **kw):
            calls.append(messages[-1]["content"])
            return '{"memory": "用户喜欢下雨天"}'

    a = _agent(tmp_path, llm=C({}))
    a._distill_for_memory("我特别喜欢下雨天", "preference")
    a._distill_for_memory("我特别喜欢下雨天", "user_fact")
    assert len(calls) == 1


# ---------- 写入侧：偏好/事件 ----------

def test_preference_stored_as_distilled_sentence(tmp_path):
    llm = DistillLLM({"我最近胃不好，辣的先不吃了": "用户有胃酸反流，近期忌辣"})
    a = _agent(tmp_path, llm=llm)
    a._maybe_extract_events("我最近胃不好，辣的先不吃了", _judgment("preference"))
    rows = a.memory.list_layer("semantic", limit=10)
    assert [r.content for r in rows] == ["用户有胃酸反流，近期忌辣"]


def test_distilled_to_nothing_is_not_stored(tmp_path):
    llm = DistillLLM({"好吧好吧我错了。今天有什么安排？": None})
    a = _agent(tmp_path, llm=llm)
    a._maybe_extract_events("好吧好吧我错了。今天有什么安排？", _judgment("preference"))
    assert a.memory.list_layer("semantic", limit=10) == []
    assert a.memory.list_layer("episodic", limit=10) == []


def test_llm_unavailable_falls_back_to_raw(tmp_path):
    a = _agent(tmp_path)  # FakeLLM 没有 chat_structured
    a._maybe_extract_events("我特别喜欢下雨天", _judgment("preference"))
    rows = a.memory.list_layer("semantic", limit=10)
    assert rows and "下雨天" in rows[0].content


# ---------- 写入侧：规则候选 ----------

def test_rule_candidate_stored_distilled(tmp_path):
    llm = DistillLLM({"我特别喜欢下雨天": "用户喜欢下雨天"})
    a = _agent(tmp_path, llm=llm)
    a._store_candidate({"kind": "user_fact", "content": "我特别喜欢下雨天", "confidence": 0.8,
                        "importance": 0.6, "source": "rule_extract",
                        "source_message_id": 1, "subject": "user"})
    assert [r.content for r in a.memory.list_layer("semantic", limit=10)] == ["用户喜欢下雨天"]


def test_rule_candidate_dropped_when_no_memory_value(tmp_path):
    llm = DistillLLM({"如果你能帮我记住的话我会很开心的": None})
    a = _agent(tmp_path, llm=llm)
    a._store_candidate({"kind": "user_fact", "content": "如果你能帮我记住的话我会很开心的",
                        "confidence": 0.85, "importance": 0.7, "source": "rule_extract",
                        "source_message_id": 1, "subject": "user"})
    assert a.memory.list_layer("semantic", limit=10) == []


def test_manual_candidate_kept_verbatim(tmp_path):
    a = _agent(tmp_path, llm=DistillLLM({}))  # 一律判 null
    a._store_candidate({"kind": "user_fact", "content": "用户是浙大生仪的学生",
                        "confidence": 0.9, "importance": 0.8, "source": "manual",
                        "source_message_id": 1, "subject": "user"})
    assert [r.content for r in a.memory.list_layer("semantic", limit=10)] == ["用户是浙大生仪的学生"]


# ---------- 存量逐条处理 ----------

def test_backfill_rewrites_raw_and_drops_junk(tmp_path):
    llm = DistillLLM({"不是，没反应了？": None, "我特别喜欢下雨天": "用户喜欢下雨天"})
    a = _agent(tmp_path, llm=llm)
    junk = a.memory.store("semantic", "不是，没反应了？", importance=0.6, confidence=0.85,
                          provenance="auto-extract", category="常识", meta={"kind": "user_fact"})
    a.memory.store("semantic", "我特别喜欢下雨天", importance=0.7, confidence=0.6,
                   provenance="auto-extract", category="preference", meta={"kind": "user_fact"})

    out = a.maybe_distill_backfill(limit=5)
    assert out["dropped"] == 1 and out["rewritten"] == 1
    assert a.memory.get(junk.id) is None  # 整条版本链删掉
    rows = a.memory.list_layer("semantic", limit=10)
    assert [r.content for r in rows] == ["用户喜欢下雨天"]
    assert rows[0].meta.get("distilled_at")
    # 幂等：处理过的条目不再调模型
    assert a.maybe_distill_backfill(limit=5)["rewritten"] == 0


def test_backfill_preserves_promise_prefix_and_meta(tmp_path):
    llm = DistillLLM({"下周三提醒你去体检": "用户下周三要去体检复查"})
    a = _agent(tmp_path, llm=llm)
    a.memory.store("procedural", "承诺：下周三提醒你去体检", importance=0.9, confidence=0.9,
                   provenance="promise-book", category="promise",
                   meta={"promise": True, "status": "open"})
    assert a.maybe_distill_backfill(limit=5)["rewritten"] == 1
    rows = a.memory.list_layer("procedural", limit=10)
    assert [r.content for r in rows] == ["承诺：用户下周三要去体检复查"]
    assert rows[0].meta.get("status") == "open" and rows[0].meta.get("promise") is True
    assert [x.content for x in PromiseBook(a.memory).open_promises()] == \
        ["承诺：用户下周三要去体检复查"]


def test_backfill_leaves_digest_and_third_person_alone(tmp_path):
    calls: list[int] = []

    class CountingLLM(DistillLLM):
        def chat_structured(self, messages, **kw):
            calls.append(1)
            return '{"memory": "改写"}'

    a = _agent(tmp_path, llm=CountingLLM({}))
    a.memory.store("episodic", "用户和小V一起吐槽了加班", provenance="nightly-digest",
                   category="日常", meta={"kind": "shared_meaning"})
    a.memory.store("episodic", "用户下周三要去体检复查", provenance="judge",
                   category="日常", meta={"kind": "conversation_event"})
    out = a.maybe_distill_backfill(limit=5)
    assert out["rewritten"] == 0 and out["dropped"] == 0 and not calls
    assert len(a.memory.list_layer("episodic", limit=10)) == 2


def test_backfill_waits_when_llm_undecided(tmp_path):
    a = _agent(tmp_path)  # FakeLLM：蒸馏抛 AttributeError = 未裁决
    a.memory.store("semantic", "我特别喜欢下雨天", provenance="auto-extract",
                   category="preference", meta={"kind": "user_fact"})
    out = a.maybe_distill_backfill(limit=5)
    assert out["pending"] == 1 and out["rewritten"] == 0 and out["dropped"] == 0
    assert [r.content for r in a.memory.list_layer("semantic", limit=10)] == ["我特别喜欢下雨天"]


def test_backfill_drops_hypothetical_promise(tmp_path):
    """写入侧 extract 不收的假设句，存量按同一套处理：整链删，且不浪费一次模型调用。"""
    calls: list[int] = []

    class CountingLLM(DistillLLM):
        def chat_structured(self, messages, **kw):
            calls.append(1)
            return '{"memory": "用户表示希望被记住"}'

    a = _agent(tmp_path, llm=CountingLLM({}))
    e = a.memory.store("procedural", "承诺：如果你能帮我记住的话我会很开心的",
                       importance=0.9, confidence=0.9, provenance="promise-book",
                       category="promise", meta={"promise": True, "status": "open"})
    out = a.maybe_distill_backfill(limit=5)
    assert out["dropped"] == 1 and not calls
    assert a.memory.get(e.id) is None
