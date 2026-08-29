"""P2 情绪标签数据面行为测试：tone 分类调用、tone_at 落库、history 出参。"""

import json

import pytest

from veranima.core.agent import Agent


class _FakeLLM:
    """最小假 LLM：chat_structured 返回词表内 tone；chat 返回主回复。"""

    def __init__(self, tone="温柔"):
        self._tone = tone
        self.calls = []

    def chat(self, messages, *, max_tokens=None, temperature=None):
        self.calls.append(("chat", max_tokens))
        return "这是回复内容。"

    def chat_structured(self, messages, *, max_tokens=None, temperature=None):
        # agent 主生成会优先用 chat_structured；这里必须返回纯文本（真实 LLMClient
        # 也返回 content 字符串），只有 _classify_tone 的调用才期待 JSON。
        self.calls.append(("structured", max_tokens))
        if max_tokens is not None and max_tokens <= 128:
            return json.dumps({"tone": self._tone}, ensure_ascii=False)
        return "这是回复内容。"

    def is_model_loaded(self):
        return True


@pytest.fixture()
def agent(tmp_path):
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.core.state import AgentState
    from veranima.memory.store import MemoryStore

    card = CharacterCard(name="小V", description="测试", personality="温柔")
    card.tones = ["中性", "平静", "温柔", "毒舌"]
    a = Agent(
        card=card,
        memory=MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=_FakeEmbed()),
        llm=_FakeLLM(),
        state=AgentState(),
        config={"llm": {}},
    )
    return a


class _FakeEmbed:
    dim = 8

    def embed(self, text):
        import hashlib
        return [float(hashlib.md5(str(text).encode()).digest()[0]) / 255.0] * self.dim


def test_im_channel_classifies_and_persists_tone(agent):
    res = agent.handle("今天天气不错", channel="im")
    assert res.tone == "温柔"
    rows = agent.memory.recent_messages(limit=5)
    asst = [r for r in rows if r["role"] == "assistant"]
    assert asst and asst[-1]["tone_at"] == "温柔"


def test_classify_off_by_config(agent):
    agent.config["ui"] = {"emotion_tags": False}
    res = agent.handle("今天天气不错", channel="im")
    assert res.tone == ""
    rows = agent.memory.recent_messages(limit=5)
    asst = [r for r in rows if r["role"] == "assistant"]
    assert asst[-1]["tone_at"] == ""


def test_classify_out_of_vocab_falls_back_empty(agent):
    agent.llm._tone = "愤怒得很"  # 词表外
    res = agent.handle("哼", channel="im")
    assert res.tone == ""


def test_classify_failure_does_not_block_reply(agent):
    def boom(*a, **k):
        raise RuntimeError("llm down")
    agent.llm.chat_structured = boom
    res = agent.handle("还在吗", channel="im")
    assert res.reply  # 主回复不受分类失败影响
    assert res.tone == ""


def test_tts_channel_keeps_structured_tone(agent):
    # tts 通道不走 _classify_tone（结构化解析已有 tone）——这里验证不双写/不覆盖
    agent.llm._tone = "毒舌"
    res = agent.handle("说句话", channel="tts")
    # tts 的 parse_reply 在假 LLM 返回纯文本时 tone 为空，且不应被 classify 覆盖为词表外值
    rows = agent.memory.recent_messages(limit=5)
    asst = [r for r in rows if r["role"] == "assistant"]
    assert asst[-1]["tone_at"] == "" or res.tone == ""


def test_recent_messages_carries_mood_and_tone(agent):
    """P2 出参契约：recent_messages 必须带 mood_at/tone_at（漏列会导致 UI 全回退平静）。"""
    agent.memory.store_message("assistant", "今天的回复", mood="开心", tone="温柔")
    rows = agent.memory.recent_messages(limit=3)
    asst = [r for r in rows if r["role"] == "assistant"]
    assert asst[-1]["mood_at"] == "开心"
    assert asst[-1]["tone_at"] == "温柔"
