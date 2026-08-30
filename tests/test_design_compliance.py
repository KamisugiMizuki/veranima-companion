"""设计合规行为测试：对先前仅符号级验证的条款做行为级断言。

覆盖：
- R2_SPEC 1：Reply 契约字段（stance/follow_up/memory_candidates）解析提取
- R1_SPEC 3：去重版本链（>=0.92 忽略 / 0.78-0.92 update_latest supersedes）
- VISION_SPEC 3：habituated 状态迁移
"""
from __future__ import annotations

import time

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.reply import Reply, parse_reply
from veranima.memory.store import MemoryStore
from veranima.core.attention import AttentionScheduler, AttentionEvent


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


# ---------- R2_SPEC 1：Reply 契约字段 ----------

def test_reply_contract_fields_present():
    """Reply 必须含 segments/stance/follow_up/memory_candidates/degraded。"""
    r = Reply(segments=[])
    for field in ("segments", "stance", "follow_up", "memory_candidates", "degraded"):
        assert hasattr(r, field), f"Reply 缺字段: {field}"
    assert r.stance == ""
    assert r.follow_up == "none"
    assert r.memory_candidates == []
    assert r.degraded == ""


def test_parse_reply_extracts_top_level_fields():
    """R2_SPEC 2：TTS JSON 顶层 stance/follow_up/memory_candidates 解析提取。"""
    raw = (
        '{"segments":[{"ja":"こんにちは","zh":"你好","tone":"温柔","portrait":"微笑"}],'
        '"stance":"agree","follow_up":"ask",'
        '"memory_candidates":[{"content":"用户喜欢猫","kind":"user_fact"}]}'
    )
    r = parse_reply(raw, channel="tts", bilingual=True)
    assert r.stance == "agree"
    assert r.follow_up == "ask"
    assert r.memory_candidates == [{"content": "用户喜欢猫", "kind": "user_fact"}]
    assert len(r.segments) == 1
    assert r.segments[0].text == "你好"


# ---------- R1_SPEC 3：去重与版本链 ----------

def _agent_with_memory(tmp_path) -> Agent:
    card = CharacterCard(
        name="小V", description="", personality="温和", scenario="",
        first_mes="你好", mes_example="", tones=["中性", "平静", "温柔"], veranima={},
    )
    store = MemoryStore(tmp_path / "mem.db")
    return Agent(card=card, memory=store, llm=None, state=None, config={})


def test_candidate_dedup_high_similarity_ignored(tmp_path):
    """R1_SPEC 3：同层同主题相似度 >=0.92 → 忽略重复。"""
    a = _agent_with_memory(tmp_path)
    a.memory.store("semantic", "用户喜欢喝咖啡，每天一杯", importance=0.6, confidence=0.7)
    n_before = len(a.memory.list_layer("semantic"))
    # 高度相似的新候选（仅措辞微差）
    a._store_candidate({"kind": "user_fact", "content": "用户喜欢喝咖啡，每天一杯咖啡", "confidence": 0.7})
    assert len(a.memory.list_layer("semantic")) == n_before  # 未新增


def test_candidate_version_chain_supersedes(tmp_path):
    """R1_SPEC 3：0.78-0.92 → 新版本入链，meta.supersedes=old_id，旧版本保留。"""
    a = _agent_with_memory(tmp_path)
    old = a.memory.store("semantic", "用户养了一只猫，叫咪咪", importance=0.6, confidence=0.7)
    # 中等相似的新信息（纠正/补充）
    a._store_candidate({"kind": "user_fact", "content": "用户养了一只猫，叫咪咪，是只橘猫",
                        "confidence": 0.8, "source": "rule_extract", "source_message_id": 1})
    entries = a.memory.list_layer("semantic")
    assert len(entries) == 1  # M-1：默认只返回 current 版本
    assert entries[0].content == "用户养了一只猫，叫咪咪，是只橘猫"
    chain = a.memory.get_history(old.id)  # 旧版本保留在链中（审计可追溯）
    assert len(chain) == 2
    assert chain[0].id == old.id
    newest = max(entries, key=lambda e: e.id)
    assert newest.meta.get("supersedes") == old.id
    assert "橘猫" in newest.content


def test_candidate_low_similarity_new_entry(tmp_path):
    """不同主题 → 直接新增（不进版本链）。"""
    a = _agent_with_memory(tmp_path)
    a.memory.store("semantic", "用户喜欢下雨天", importance=0.6, confidence=0.7)
    a._store_candidate({"kind": "user_fact", "content": "用户明天要去面试",
                        "confidence": 0.7, "source": "rule_extract", "source_message_id": 1})
    assert len(a.memory.list_layer("semantic")) == 2


# ---------- VISION_SPEC 3：habituated 状态迁移 ----------

def test_scheduler_habituated_state(monkeypatch):
    """fixating --no novelty 60s--> habituated（VISION_SPEC 3 状态机）。"""
    import veranima.core.attention.perception as perception
    import numpy as np

    monkeypatch.setattr(perception, "grab_gray_downsampled",
                        lambda scale=8: np.zeros((60, 80), dtype=np.uint8))

    att = AttentionScheduler(config={"away_idle_s": 9999})
    monkeypatch.setattr(
        att.policy, "policy_action",
        lambda *args: {"action": "skip", "category": "sensitive", "reason": "test"},
    )
    # 构造一个已超习惯化阈值的注视区域
    att.focus = {"center": (40, 30), "since": time.time() - 120, "last_change": time.time() - 120}
    att._habituation["40,30"] = time.time() - 120
    events = att.tick()
    assert any(e.kind == "habituation" for e in events)
    assert att.state == "habituated"


# ---------- 指令稀释防线（2026-08-30） ----------

def test_output_format_instruction_is_last_block():
    """输出格式指令必须压在 system prompt 末尾（首尾服从度最高，可机器校验的指令放最后）。"""
    from veranima.core.prompts import (
        build_system_prompt, IM_STRUCTURED_OUTPUT_INSTRUCTION,
        SEGMENTED_OUTPUT_INSTRUCTION, BILINGUAL_OUTPUT_INSTRUCTION)
    from veranima.core.state import AgentState

    card = CharacterCard(name="小V", first_mes="你好")
    mem = MemoryStore(db_path=":memory:", config={}, provider=FakeEmbed())
    for ch, marker in (("im", IM_STRUCTURED_OUTPUT_INSTRUCTION), ("tts", SEGMENTED_OUTPUT_INSTRUCTION)):
        sp = build_system_prompt(card, AgentState(), mem, channel=ch)
        assert sp.rstrip().endswith(marker.rstrip()), f"{ch} 通道输出格式指令未压尾"


def test_hard_directive_count_regression_line():
    """硬指令（必须/不要/不得/只能）行数回归上限：新功能往 prompt 加规则时超线即红。

    稀释是一次次各加一行累积出来的——这条线是刹车，不是设计目标。抬线需要有意识修改本测试。
    """
    from veranima.core.prompts import build_system_prompt
    from veranima.core.state import AgentState
    import pathlib
    import re

    card = CharacterCard.from_file(str(pathlib.Path(__file__).resolve().parents[1] / "characters/lin/character.json"))
    mem = MemoryStore(db_path=":memory:", config={}, provider=FakeEmbed())
    sp = build_system_prompt(card, AgentState(), mem, channel="im")
    hard = [l for l in sp.splitlines() if re.search(r"必须|不要|不得|只能|禁止", l)]
    assert len(hard) <= 15, f"硬指令 {len(hard)} 行，超回归线（现基线 12）：新规则先问能否进校验器而不是 prompt"


def test_format_brief_dedups_identical_lines():
    """同层重复注入的记忆行只保留一条（重复模板行占预算又稀释指令）。"""
    from veranima.memory.brief import MemoryBriefItem, format_brief

    dup = [MemoryBriefItem(memory_id=i, kind="k", text="未闭合问题", confidence_label="中",
                           temporal_label="", score=0.5, label="共同回忆") for i in range(3)]
    out = format_brief(dup)
    assert out.count("未闭合问题") == 1
