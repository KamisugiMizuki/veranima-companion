"""M4 测试：表情标签驱动（segments 解析/词表校验/渲染链）+ 视觉注意力（三态/像素差异）。"""
import pytest

from veranima.core.segments import extract_segments
from veranima.core.vision import Anchor, VisualAttention


@pytest.fixture
def agent(tmp_path):
    from veranima.core.character import CharacterCard
    from veranima.memory.store import MemoryStore
    mem = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, llm_config={})
    card = CharacterCard(name="测试", first_mes="hi")
    return card, mem


# ---------- 表情标签驱动（M4_SPEC 2.1） ----------

def test_extract_segments_normal_json():
    reply = '{"segments":[{"text":"你好呀","tone":"温柔","portrait":"开心脸红"}]}'
    text, tone, portrait = extract_segments(reply)
    assert text == "你好呀"
    assert tone == "温柔"
    assert portrait == "开心脸红"


def test_extract_segments_plain_text_fallback():
    """非 JSON 回复：整段当文本，portrait/tone 空。"""
    text, tone, portrait = extract_segments("今天天气不错")
    assert text == "今天天气不错"
    assert tone == ""
    assert portrait == ""


def test_extract_segments_markdown_fenced_json():
    """模型包了 markdown 代码块：容错解析出 text。"""
    reply = '```json\n{"segments":[{"text":"嗯，在的","tone":"中性","portrait":"站立待机"}]}\n```'
    text, tone, portrait = extract_segments(reply)
    assert text == "嗯，在的"
    assert portrait == "站立待机"


def test_extract_segments_missing_text_key():
    """缺 text 字段：文本回退原文，tone/portrait 保留。"""
    reply = '{"segments":[{"tone":"中性","portrait":"站立待机"}]}'
    text, tone, portrait = extract_segments(reply)
    assert text == reply
    assert tone == "中性"
    assert portrait == "站立待机"


# ---------- 视觉注意力（M4_SPEC 1.2） ----------

def test_visual_states_transition():
    va = VisualAttention(now=1000.0)
    assert va.state == "stable"
    assert va.interval() == 30.0
    # 不在场 → 游离
    va.tick(presence=False)
    assert va.state == "wander"
    assert va.interval() == 120.0
    # 在场恢复 → 稳定
    va.tick(presence=True)
    assert va.state == "stable"


def test_visual_trigger_reset():
    va = VisualAttention(now=1000.0)
    va.state = "trigger"
    # 连续 TRIGGER_RESET_COUNT 次无变化 → 回稳定
    for _ in range(3):
        va.tick(presence=True)
    assert va.state == "stable"


def test_chi2_distance():
    """相同直方图距离 0，完全不同距离大。"""
    h1 = [1.0] * 64
    h2 = [1.0] * 64
    assert VisualAttention.chi2_distance(h1, h2) == 0.0
    h3 = [0.0] * 64
    h3[0] = 1.0
    assert VisualAttention.chi2_distance(h1, h3) > 0.5


def test_observe_ring_and_cooldown():
    va = VisualAttention(now=1000.0)
    assert va.note_observe("游戏", "在打游戏") is True
    assert va.focus == {"tag": "游戏", "since": 1000.0}
    # 60s 冷却内不记录
    assert va.note_observe("办公") is False
    va._now = 1100.0  # 100s 后
    assert va.note_observe("办公") is True
    assert len(va.observations) == 2


# ---------- 表情词表校验（M4_SPEC 2.2，走 agent） ----------

def test_portrait_valid_from_card(agent):
    """agent 校验 portrait 标签：词表内通过，词表外拒绝。"""
    from veranima.core.agent import Agent
    from veranima.core.state import AgentState
    card, memory = agent
    # 构造带 expressions 的角色卡
    card.veranima = {"avatar": {"expressions": {"开心脸红": "happy.png", "难过": "sad.png"}}}
    a = Agent(card=card, memory=memory, llm=None, state=AgentState(), config={})
    assert a._portrait_valid("开心脸红") is True
    assert a._portrait_valid("随便乱写") is False
