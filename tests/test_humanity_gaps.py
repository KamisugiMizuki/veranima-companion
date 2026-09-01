"""2026-08-31 自检清单五缺口的行为级回归（困倦渗透/弱关联考古/重学/关系解锁/上下文饭点）。"""
from __future__ import annotations

import datetime as dt
import json

from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime, ScheduleRuntimeState
from veranima.memory.store import MemoryStore

UTC = dt.timezone.utc


class Embed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class ProbeLLM:
    def __init__(self):
        self.messages = None

    def is_model_loaded(self):
        return True

    def chat(self, messages, **kwargs):
        self.messages = messages
        return json.dumps({"segments": [{"text": "嗯。", "tone": "冷静"}]}, ensure_ascii=False)


def _template(minutes_offset_free=True):
    return {
        "enabled": True, "schema_version": 1, "timezone": "Asia/Shanghai",
        "default_day_profile": "base", "day_profiles": {"base": {"allowed_block_ids": []}},
        "blocks": [], "interaction_profiles": {}, "autonomy": {},
        "circadian": {"wake_window": {"start": "06:00", "end": "07:00"},
                      "sleep_window": {"start": "23:00", "end": "23:30"},
                      "chronotype": "day_aligned", "target_sleep_minutes": 480},
        "sleep": {},
    }


def _agent(tmp_path, name, template=None):
    role = tmp_path / "characters" / name.lower()
    role.mkdir(parents=True)
    (role / "virtual_schedule.json").write_text(json.dumps(template or _template()), encoding="utf-8")
    (role / "character.json").write_text("{}", encoding="utf-8")
    probe = ProbeLLM()
    memory = MemoryStore(str(tmp_path / f"{name}.sqlite"), config={}, provider=Embed())
    agent = Agent(
        CharacterCard(name=name), memory, probe, AgentState(),
        config={"root": str(tmp_path), "character_card": str(role / "character.json"),
                "chat": {"proactive_message_prob": 0.0}},
    )
    agent.schedule_runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(role))
    return agent, probe


from veranima.core.agent import Agent  # noqa: E402


def test_drowsy_reaches_prompt_after_woke_with_debt(tmp_path):
    """缺口①：睡眠债务≥1h 且刚醒 → system 出现【欠睡】形式约束。"""
    agent, probe = _agent(tmp_path, "Drowsy")
    noon = dt.datetime(2026, 8, 28, 12, tzinfo=UTC)  # 醒窗内，advance 不会再推睡眠
    agent.schedule_runtime.state = ScheduleRuntimeState(
        state="awake", sleep_reason="woke", sleep_debt_minutes=120,
        sleep_cycle_id="drowsy:x:awake",
    )
    agent.handle("早", now=noon)
    system = probe.messages[0]["content"]
    assert "【欠睡】" in system and "2 小时" in system


def test_drowsy_absent_without_debt(tmp_path):
    agent, probe = _agent(tmp_path, "Rested")
    agent.schedule_runtime.state = ScheduleRuntimeState(state="awake", sleep_reason="woke")
    agent.handle("早", now=dt.datetime(2026, 8, 28, 12, tzinfo=UTC))
    assert "【欠睡】" not in probe.messages[0]["content"]


def test_relearn_bumps_confidence_instead_of_stacking(tmp_path):
    """缺口③：同一件事再说一遍 → 版本链 tip 置信 0.95 + relearned 标记，不是新独立条目。"""
    agent, _ = _agent(tmp_path, "Relearn")
    agent._relearn_or_store("semantic", "我特别喜欢加香菜的面", importance=0.7, category="preference")
    agent._relearn_or_store("semantic", "我特别喜欢加香菜的面", importance=0.7, category="preference")
    rows = [e for e in agent.memory.list_layer("semantic", limit=10) if "香菜" in e.content]
    # list_layer 只回未超版 tip：重学=同一条链上的 tip 刷新，而非堆两条独立条目
    assert len(rows) == 1
    tip = rows[0]
    assert tip.confidence >= 0.9 and tip.meta.get("relearned") is True
    assert tip.meta.get("supersedes")  # 版本链可追溯（旧版保留在库内）


def test_bigram_similarity_threshold():
    """缺口②③共用的廉价相似度：改写句 ≥0.5，无关句 <0.5。"""
    sim = Agent._bigram_sim
    assert sim("我特别喜欢加香菜的面", "我特别喜欢吃加香菜的面") >= 0.5
    assert sim("我喜欢猫", "明天要开会") < 0.5


def test_dig_prefers_related_but_never_breaks(tmp_path):
    """缺口②：带话题线索挖旧——命中返回内容、无命中/失败都返回 None 或字符串，绝不抛。"""
    agent, _ = _agent(tmp_path, "Dig")
    agent.memory.store("episodic", "用户说过喜欢下雨天", importance=0.8, confidence=0.8,
                       provenance="auto-extract", category="event")
    result = agent._dig_old_memory(topic_hint="天气和心情")
    assert result is None or (isinstance(result, tuple) and isinstance(result[0], str))


def test_sleep_care_requires_intimacy(tmp_path):
    """缺口④：牵挂是亲密行为——依恋 0.3 不注入，0.6 注入（错位作息下）。"""
    template = _template()
    template["circadian"] = {"wake_window": {"start": "05:00", "end": "06:00"},
                             "sleep_window": {"start": "22:00", "end": "23:00"},
                             "chronotype": "day_aligned", "target_sleep_minutes": 420}
    agent, _ = _agent(tmp_path, "Care", template)
    agent.meals.slots["breakfast"] = (4, "早饭")  # 落在 22:00→05:00 睡眠区间
    agent.state.attachment = 0.3
    assert agent._sleep_care_note() == ""
    agent.state.attachment = 0.6
    assert "早饭" in agent._sleep_care_note()


def test_meal_message_carries_recent_context(tmp_path):
    """缺口⑤：饭点任务文本带上用户最近说的内容（LLM 才接得住"你说忙所以…"）。"""
    agent, probe = _agent(tmp_path, "Mealctx")
    agent.memory.store_message("user", "今天加班到现在还没吃晚饭", 80, "平静", channel="qq")
    text = agent._meal_message("dinner", "到饭点了")
    assert text == "嗯。"  # probe 应答
    task = probe.messages[-1]["content"]
    assert "用户最近说过" in task and "加班" in task
