"""DESIGN 4.3 能力匹配层 + 4.5 L3 不静默 + 4.7 连续失败抑制测试。"""
import json

import pytest

from veranima.core.capability import capability_level, dont_know_line, dont_know_much_line
from veranima.core.character import CharacterCard


# ---------- 4.3 能力匹配层 ----------

def _card_with_caps():
    return CharacterCard.from_dict({
        "extensions": {"veranima": {"capabilities": {
            "擅长": ["游戏", "TRPG"],
            "略知": ["编程"],
            "完全不懂": ["时尚", "体育"],
        }}},
    })


def test_capability_level():
    """话题熟悉度判定：擅长/略知/完全不懂/未知。"""
    card = _card_with_caps()
    assert capability_level(card, "我最近在玩那个 TRPG 跑团") == "擅长"
    assert capability_level(card, "这个编程问题怎么解决") == "略知"
    assert capability_level(card, "这季的时尚穿搭") == "完全不懂"
    assert capability_level(card, "量子物理") == "未知"


def test_dont_know_much_lines():
    """四型话术池非空且各不相同。"""
    lines = {dont_know_much_line(s) for s in ("好奇", "共情", "关联", "走神")}
    assert len(lines) >= 3
    assert dont_know_line()


def test_example_card_has_capabilities():
    """example 角色卡含 capabilities 字段（4.3）。"""
    import io
    d = json.load(io.open(r"D:\Hermes_workspace\veranima\config\character.example.json", encoding="utf-8"))
    caps = d["extensions"]["veranima"].get("capabilities", {})
    assert "擅长" in caps and "略知" in caps and "完全不懂" in caps


# ---------- 4.5 L3 不静默 ----------

def test_interrupt_prompt_l3_work_mode():
    """L3 指令含工作模式（不静默，必须继续回复）。"""
    from veranima.core.agent import _interrupt_prompt
    p = _interrupt_prompt(3)
    assert "五遍" in p
    assert "极简" in p
    assert "继续回复" in p  # 不静默


# ---------- 4.7 连续失败抑制 ----------

def test_arbitrator_failure_escalation():
    """连续 2 次失败 → 冷却指数增长；成功后清零。"""
    from veranima.core.ambient import Arbitrator
    a = Arbitrator(now=1000.0)
    a.note_failure("idle")
    assert a.request("idle", scene="normal") is True  # 第 1 次失败后仍可发起
    a.note_failure("idle")
    assert a.request("idle", scene="normal") is False  # 第 2 次失败 → 冷却 60min
    a._now = 1000.0 + 1801  # 30min 后
    assert a.request("idle", scene="normal") is False  # 60min 冷却未过
    a._now = 1000.0 + 3601  # 60min 后
    assert a.request("idle", scene="normal") is True
    a.commit("idle")  # 成功 → 失败计数清零 + 30min 冷却
    assert a._fail_streak["idle"] == 0
    a._now = 1000.0 + 3601 + 1801  # commit 冷却过后
    a.note_failure("idle")
    assert a.request("idle", scene="normal") is True  # 清零后重新从 1 开始
