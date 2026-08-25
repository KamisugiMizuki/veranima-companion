from __future__ import annotations

from veranima.core.qq_advisor import QQProactiveAdvisor


class _Entry:
    def __init__(self, content, confidence=0.9, id=1, meta=None):
        self.content = content
        self.confidence = confidence
        self.id = id
        self.meta = meta or {}


class _Memory:
    def __init__(self):
        self.rows = []
        self.feedback = []

    def recent_messages(self, limit=20):
        return self.rows[-limit:]

    def recall(self, query, top_k=3, layer=None):
        return [_Entry("用户之前说过要跟进的事", 0.9, 7)] if query else []

    def recent_proactive_feedback(self, channel=None, limit=10):
        return self.feedback[-limit:]

    def list_layer(self, layer, limit=100):
        return []


def test_advisor_prefers_high_confidence_memory():
    memory = _Memory()
    memory.rows = [{"role": "user", "content": "昨天面试", "created_at": "2026-08-20T10:00:00+08:00", "id": 1}]
    advisor = QQProactiveAdvisor(memory)

    readiness, material = advisor.evaluate(
        __import__("datetime").datetime(2026, 8, 21, 10, tzinfo=__import__("datetime").timezone(__import__("datetime").timedelta(hours=8))),
        query="面试",
    )

    assert material.kind == "memory"
    assert material.source_id == 7
    assert readiness.material_multiplier == 2.0


def test_advisor_cold_start_uses_neutral_routine_and_social():
    advisor = QQProactiveAdvisor(_Memory())

    assert advisor.routine_multiplier(__import__("datetime").datetime.now().astimezone()) == 1.0
    assert advisor.social_multiplier() == 1.0


def test_advisor_skips_internal_tension_memory():
    memory = _Memory()
    memory.rows = [{"role": "user", "content": "今天继续学高数", "created_at": "2026-08-24T09:00:00+08:00", "id": 9}]
    memory.recall = lambda query, top_k=3, layer=None: [
        _Entry(
            "QQ 对话中存在未闭合问题，超过一小时没有后续消息",
            0.85,
            167,
            {"kind": "relational_tension_event"},
        )
    ] if layer == "episodic" else []
    material = QQProactiveAdvisor(memory).material("高数")
    assert material.kind == "time_followup"
    assert material.text == "今天继续学高数"


def test_advisor_skips_closed_conversation_event():
    memory = _Memory()
    memory.rows = [{"role": "user", "content": "用户刚刚说了普通近况", "created_at": "2026-08-24T09:00:00+08:00", "id": 9}]
    memory.recall = lambda query, top_k=3, layer=None: [
        _Entry("这件事已经结束", 0.95, 22, {"kind": "conversation_event", "topic": "某件事", "status": "completed"})
    ] if layer == "episodic" else []

    material = QQProactiveAdvisor(memory).material("某件事")

    assert material.kind == "time_followup"
    assert material.source_id == 9


def test_advisor_returns_active_conversation_event_source():
    memory = _Memory()
    memory.recall = lambda query, top_k=3, layer=None: [
        _Entry("用户近期有一项待跟进安排", 0.85, 31,
               {"kind": "conversation_event", "topic": "待跟进安排", "status": "active"})
    ] if layer == "episodic" else []

    material = QQProactiveAdvisor(memory).material("安排")

    assert material.kind == "memory"
    assert material.source_id == 31


def test_advisor_uses_latest_active_event_without_query_match():
    memory = _Memory()
    memory.rows = [{"role": "user", "content": "今天聊点别的", "created_at": "2026-08-26T10:00:00+08:00", "id": 40}]
    memory.recall = lambda query, top_k=3, layer=None: []
    memory.list_layer = lambda layer, limit=100: [
        _Entry("用户近期有一项有期限的安排", 0.82, 31,
               {"kind": "conversation_event", "topic": "有期限安排", "status": "active", "intent": "remind"})
    ] if layer == "episodic" else []

    material = QQProactiveAdvisor(memory).material("完全不相关")

    assert material.kind == "memory"
    assert material.source_id == 31
