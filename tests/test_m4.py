"""R2+R4 测试：表情标签驱动（segments 解析/词表校验/渲染链）+ 联想式主动。"""
import sys

import pytest

@pytest.fixture
def agent(tmp_path):
    from veranima.core.character import CharacterCard
    from veranima.memory.store import MemoryStore
    mem = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, llm_config={})
    card = CharacterCard(name="测试", first_mes="hi")
    return card, mem


# ---------- 表情标签驱动（R4_SPEC 2.1） ----------

# ---------- R4：L0 在场 + 联想式主动（旧 VisualAttention 测试随 vision.py 删除，VISION_SPEC） ----------

def test_presence_non_windows():
    """非 Windows 降级：恒在场、前台空。"""
    from veranima.core.presence import foreground_app, presence
    if sys.platform != "win32":
        assert presence() is True
        assert foreground_app() == ""


def test_proactive_from_visual(agent, monkeypatch):
    """联想式主动：episodic 层含 tag 记忆 → 生成消息。"""
    from veranima.core.agent import Agent
    from veranima.core.state import AgentState
    card, memory = agent
    # 植入事件记忆（episodic 层）
    memory.store(layer="episodic", content="用户上次打游戏打到凌晨三点，第二天上班迟到了", provenance="test")
    a = Agent(card=card, memory=memory, llm=None, state=AgentState(), config={})
    a.state.energy = 80
    # mock _short_task 返回 (中文, 日语) 双语（2026-08-19：主动发起双语化）
    monkeypatch.setattr(a, "_short_task",
                        lambda task, max_tokens=512, bilingual=False:
                        ("咦，你又在打游戏？上次通宵的事忘了？", "またゲーム？"))
    reply = a.proactive_from_visual("游戏")
    assert "游戏" in reply[0]
    assert len(a._history) == 1


def test_proactive_from_visual_no_memory(agent):
    """无匹配记忆 → 返回空（不发起）。"""
    from veranima.core.agent import Agent
    from veranima.core.state import AgentState
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=None, state=AgentState(), config={})
    a.state.energy = 80
    assert a.proactive_from_visual("办公") == ("", "")


# ---------- 表情词表校验（R4_SPEC 2.2，走 agent） ----------

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
