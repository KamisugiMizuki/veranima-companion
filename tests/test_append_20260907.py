"""2026-09-07 外部借鉴评估（design_append §12）落码行为验收。

C（否决台账）：judges 源名闭集裁决 → 词面兜底 → 台账捕获（带保质期）→
tick 收集端剔除 → 到期自动解除 → 随快照持久化（重启读回）。
D（复盘维度）：夜间 digest 的 portrait 引导含固定角度且要求「有依据才写」。
A/B/E 为纯文档裁决（A 移交仓库外；B 挂账 SHARED_CREATION §3.6；
E 并入 CHARPKG §3.2），无代码断言。
"""
from __future__ import annotations

import datetime

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.judges import MessageJudgment, _coerce
from veranima.core.proactive import MealReminderScheduler, veto_from_keywords
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
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


def _agent(tmp_path, llm=None):
    card = CharacterCard(name="小V", first_mes="你好")
    memory = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    return Agent(card=card, memory=memory, llm=llm or FakeLLM(),
                 state=AgentState(), config={})


# ---------- C：judges 源名闭集 ----------

def test_veto_coerce_closed_set():
    j = _coerce({"veto_source": "meal", "veto_days": 3})
    assert j.veto_source == "meal" and j.veto_days == 3
    # 源名表外=丢弃（同 _VALID_* 纪律）；天数越界截断、非数字归 0
    assert _coerce({"veto_source": "喝水提醒", "veto_days": 2}).veto_source == ""
    assert _coerce({"veto_source": "greeting", "veto_days": 999}).veto_days == 365
    assert _coerce({"veto_source": "greeting", "veto_days": "三天"}).veto_days == 0


def test_veto_keyword_fallback():
    assert veto_from_keywords("别老提醒我吃饭了") == ("meal", 0)
    assert veto_from_keywords("这三天不用跟我问好") == ("greeting", 3)
    # 否决句式但不含已知类型 / 普通聊天 → 不否决
    assert veto_from_keywords("别再提那件事了") is None
    assert veto_from_keywords("今天天气不错") is None


def test_veto_capture_and_tick_suppresses_source(tmp_path):
    """否决 meal 后：晚餐到点不产素材；未否决的 occasion 等其余源不受影响。"""
    a = _agent(tmp_path)
    a._capture_proactive_veto("别老提醒我吃饭了",
                              MessageJudgment(veto_source="meal", veto_days=0))
    assert a._proactive_veto.get("meal") == ""
    target = MealReminderScheduler().scheduled_at(datetime.date(2026, 8, 4), "dinner")
    assert a.tick_proactive(now=target) == []
    # 去重键未被消耗：解除否决后同一天饭点仍可发（闸在收集前）
    a._proactive_veto.pop("meal")
    assert a.tick_proactive(now=target) != []


def test_veto_judgment_present_no_keyword_second_guess(tmp_path):
    """判断点在场且裁决无否决（veto_source 空）→ 不再跑词面兜底。"""
    a = _agent(tmp_path)
    a._capture_proactive_veto("别老提醒我吃饭了", MessageJudgment())  # LLM 判=无否决
    assert a._proactive_veto == {}


def test_veto_expiry(tmp_path):
    a = _agent(tmp_path)
    today = datetime.date(2026, 9, 7)
    a._proactive_veto["greeting"] = (today + datetime.timedelta(days=3)).isoformat()
    assert a._vetoed("greeting", datetime.datetime(2026, 9, 9, 8, 0)) is True
    assert a._vetoed("greeting", datetime.datetime(2026, 9, 11, 8, 0)) is False
    assert "greeting" not in a._proactive_veto  # 到期即清账


def test_veto_persists_across_restart(tmp_path):
    a = _agent(tmp_path)
    a._capture_proactive_veto("别催我报作息了",
                              MessageJudgment(veto_source="sleep_hint", veto_days=0))
    a._persist_state()
    b = _agent(tmp_path)  # 同库新 Agent（快照读回）
    assert b._proactive_veto.get("sleep_hint") == ""
    assert b._vetoed("sleep_hint", datetime.datetime.now()) is True


# ---------- D：复盘维度（digest 提示词引导） ----------

def test_digest_portrait_guidance_words(tmp_path):
    """portrait 引导含固定角度 + 「有依据才写」护栏，且仍是角色口吻（非判词腔）。"""
    import json as _json
    llm = FakeLLM(raw=_json.dumps({"content": "摘要", "portrait": "观察"}, ensure_ascii=False))
    a = _agent(tmp_path, llm=llm)
    ids = []
    for txt in ("周一加班到十一点", "周二继续改方案", "周三终于提测了"):
        mid = a.memory.store_message("user", txt)
        ids.append(mid)
        a._store_candidate({"kind": "shared_episode", "content": txt,
                            "source_message_id": mid, "confidence": 0.9,
                            "subject": "user", "source": "rule_extract"})
    out = a.maybe_nightly_digest()
    assert out.get("created") is True
    task = llm.calls[-1]["messages"][-1]["content"]
    assert "什么时候最爱来找我" in task and "回避" in task and "有依据才写" in task
