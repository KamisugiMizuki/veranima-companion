"""M1c 画像瘦身 + HARNESS D1 决策留痕的行为级验收（09-06 用户裁决）。

M1c：current_goal/pending_events 两键从画像退役（judges 闭集/注入标签/设置页
三处），存量值 boot 时一次性开进牵挂账本（origin=user，抹键=幂等）。
D1：自发副作用（主动消息织发/苏醒总结/动态发布/联想素材过期）各落一行
decisions——「她为什么说了/没说这句」导出即账本。
"""
from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.judges import _coerce
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore
from veranima.memory.usermodel import PROFILE_KEYS


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    reply = "好的。"

    def chat(self, messages, **kw):
        return self.reply

    def chat_structured(self, messages, **kw):
        return self.reply

    def is_model_loaded(self):
        return True


def _agent(tmp_path, name="小V"):
    card = CharacterCard(name=name, first_mes="你好")
    memory = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    return Agent(card=card, memory=memory, llm=FakeLLM(),
                 state=AgentState(), config={})


# ---------- M1c：画像退役 + 存量迁移 ----------

def test_retired_keys_out_of_closed_sets():
    assert "current_goal" not in PROFILE_KEYS and "pending_events" not in PROFILE_KEYS
    assert len(PROFILE_KEYS) == 11
    # judges 闭集与 PROFILE_KEYS 同步（旧真机 prompt 输出这两键 → 必须被丢弃）
    j = _coerce({"profile": {"city": "杭州", "current_goal": "赶毕设",
                             "pending_events": "下周三答辩"}})
    assert j.profile == {"city": "杭州"}


def test_profile_block_no_event_labels(tmp_path):
    a = _agent(tmp_path)
    a.memory.profile_set("city", "杭州", source="user", confidence=1.0)
    block = a._profile_block()
    assert "城市" in block
    assert "近期在忙" not in block and "pending 的事" not in block


def test_legacy_usermodel_values_migrate_into_threads(tmp_path):
    """旧 usermodel.json（含退役键值）→ Agent init 一次性开进牵挂账本。"""
    (tmp_path / "usermodel.json").write_text(json.dumps({
        "version": 1,
        "profile": {
            "city": {"value": "杭州", "source": "user", "confidence": 1.0,
                     "pinned": True},
            "current_goal": {"value": "毕设答辩准备", "source": "user",
                             "confidence": 1.0, "pinned": False},
        },
        "portraits": {},
    }, ensure_ascii=False), encoding="utf-8")
    card = CharacterCard(name="小V", first_mes="你好")
    # usermodel 锚在 db 同目录 = tmp_path（与真机同形状）
    memory = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    rows = memory.thread_list(a.threads.role)
    assert any("毕设答辩准备" in r["topic"] and r["origin"] == "user" for r in rows)
    # 画像侧两键已被抹：读面与文件都没有（幂等标记）
    assert "current_goal" not in memory.profile_all()
    assert "current_goal" not in json.loads(
        (tmp_path / "usermodel.json").read_text(encoding="utf-8"))["profile"]
    # 二次 init 不重复开线（无键可迁）
    Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    assert sum("毕设答辩准备" in r["topic"] for r in memory.thread_list(a.threads.role)) == 1


def test_settings_page_retired_keys_removed():
    kt = (pathlib.Path(__file__).resolve().parents[1]
          / "android/fuyuno/app/src/main/java/io/github/kamisugimizuki/veranima/Settings.kt")
    src = kt.read_text(encoding="utf-8")
    assert '"current_goal"' not in src and '"pending_events"' not in src


# ---------- D1：决策留痕 ----------

def _decisions(store):
    return [dict(r) for r in store.con.execute(
        "SELECT kind, verdict, reason, digest, object_ref, effect_ref, role_id"
        " FROM decisions ORDER BY id")]


def test_proactive_send_is_logged(tmp_path):
    a = _agent(tmp_path, name="账本")
    a.record_proactive_message("心里有事想说", kind="thread", reason="步点推进",
                               object_ref="thread:9")
    rows = _decisions(a.memory)
    assert len(rows) == 1
    r = rows[0]
    assert (r["kind"], r["verdict"]) == ("thread", "sent")
    assert r["digest"] == "心里有事想说" and r["effect_ref"] > 0 and r["role_id"] == "账本"
    # effect_ref 真指向落库的消息行
    msg = a.memory.con.execute("SELECT content FROM messages WHERE id=?",
                               (r["effect_ref"],)).fetchone()
    assert msg and msg["content"] == "心里有事想说"


def test_stale_probe_expiry_logged(tmp_path):
    a = _agent(tmp_path)
    now = datetime.datetime.now()
    a._last_proactive_sent_at = (now - datetime.timedelta(hours=2)).astimezone().isoformat(timespec="seconds")
    user_ts = (now - datetime.timedelta(minutes=10)).isoformat()
    a.memory.store_message("user", "回来了", 60, "开心")
    a.memory.con.execute("UPDATE messages SET created_at=? WHERE id=(SELECT MAX(id) FROM messages)",
                         (user_ts,))
    a.memory.con.commit()
    a._ritual_pending.append({
        "source": "context_probe", "text": "三个小时没动静了吧",
        "ts": (now - datetime.timedelta(hours=1)).timestamp(), "role": a.card.name,
    })
    a.gate.decide = lambda cand, **kw: type("D", (), {"allow": True})()
    a.tick_proactive(now=now.timestamp(), persist=False, commit=False)
    rows = [r for r in _decisions(a.memory) if r["verdict"] == "expired"]
    assert rows and rows[0]["kind"] == "context_probe" and "三个小时" in rows[0]["digest"]


def test_wake_summary_claim_logged(tmp_path, monkeypatch):
    a = _agent(tmp_path)
    a.state.user_asleep = True
    now = datetime.datetime(2026, 9, 6, 1, 54, tzinfo=datetime.timezone.utc)
    a.memory.open_sleep_cycle((now - datetime.timedelta(hours=11)).isoformat(timespec="seconds"))
    monkeypatch.setattr(a, "_sleep_cycle_summary", lambda c: "醒了？十一个小时。")
    a._note_sleep_report("堂堂起床！", now)
    rows = [r for r in _decisions(a.memory) if r["kind"] == "wakesummary"]
    assert len(rows) == 1 and rows[0]["verdict"] == "sent"
    assert rows[0]["object_ref"].startswith("sleep_cycle:")


def test_decision_log_failure_never_breaks_send(tmp_path):
    """观测面不拖死行为：decisions 表被毁 → record_proactive_message 照常返回消息 id。"""
    a = _agent(tmp_path)
    a.memory.con.execute("DROP TABLE decisions")
    a.memory.con.commit()
    mid = a.record_proactive_message("表没了也要说话")
    assert mid > 0
    assert a.memory.recent_messages(limit=1)[0]["content"] == "表没了也要说话"


def test_tick_weave_logs_pool_sources(tmp_path, monkeypatch):
    """织发出账带素材构成（kind=ritual:greeting+… 可追溯「这条消息为什么包含这些」）。"""
    a = _agent(tmp_path)
    now = datetime.datetime.now()
    monkeypatch.setattr(a, "proactive_merge_open", lambda n=None: True)
    monkeypatch.setattr(a, "_ritual_send_open", lambda n=None: True)
    a.gate.decide = lambda cand, **kw: type("D", (), {"allow": True})()
    monkeypatch.setattr(a, "persona_proactive_blocked", lambda s: False)
    a._ritual_pending.append({
        "source": "thread", "text": "那组对比还压着",
        "ts": now.timestamp(), "role": a.card.name,
    })
    msgs = a.tick_proactive(now=now.timestamp(), commit=False)
    assert msgs
    rows = [r for r in _decisions(a.memory) if r["verdict"] == "sent" and r["kind"].startswith("ritual:")]
    assert rows and "thread" in rows[0]["kind"] and rows[0]["effect_ref"] > 0
