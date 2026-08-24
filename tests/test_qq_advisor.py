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
