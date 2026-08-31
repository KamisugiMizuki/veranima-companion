"""统一消息判断点（judges.py）行为测试——2026-08-31 面向样例编程清算。

每个消费点两面断言：有裁决以语义为准；无裁决（LLM 挂/短消息）行为与旧词表一致。
"""
from __future__ import annotations

import json

from veranima.core.ambient import SceneLock
from veranima.core.judges import MessageJudgment, judge_message
from veranima.core.persona import ConflictTracker, note_conflict_from_user_text
from veranima.core.prompts import is_clarification
from veranima.core.task_session import QQTaskSessionManager
from veranima.core.tension_events import classify_user_tension_event
from veranima.tools.search import SearchTrigger


class JudgeLLM:
    def __init__(self, verdict: dict | None = None, boom=False):
        self.verdict = verdict
        self.boom = boom
        self.calls = 0

    def is_model_loaded(self):
        return True

    def chat_structured(self, messages, **kw):
        self.calls += 1
        if self.boom:
            raise RuntimeError("down")
        return json.dumps(self.verdict or {}, ensure_ascii=False)


# ---------- judges 本体 ----------

def test_judge_parses_and_clamps_fields():
    llm = JudgeLLM({"wants_search": True, "scene": "busy", "user_state": "low_mood",
                    "tension": "answered", "conflict": "apology", "memory": "preference",
                    "emotion": "anxious", "clarification": False, "is_task": True,
                    "wants_remember": True, "feedback_like": True, "feedback_dislike": False})
    j = judge_message(llm, "这周四复诊你说要不要空腹去啊")
    assert j.wants_search is True and j.scene == "busy" and j.user_state == "low_mood"
    assert j.memory_kind == "preference" and j.emotion == "anxious"
    assert j.clarification is False and j.is_task is True and j.feedback_like is True


def test_judge_rejects_garbage_values():
    j = judge_message(JudgeLLM({"scene": "nonsense", "memory": "x", "emotion": None}), "随便一句长话测试")
    assert j.scene is None and j.memory_kind == "none" and j.emotion == "none"


def test_judge_fail_open_on_error():
    assert judge_message(JudgeLLM(boom=True), "随便一句长话测试") is None


def test_judge_skips_short_messages():
    assert judge_message(JudgeLLM({}), "嗯") is None  # <6 字且不含场景词
    # 场景词短消息例外（闸门信号不等长度）
    assert judge_message(JudgeLLM({"scene": "away"}), "要睡了") is not None
    assert judge_message(None, "足够长的一条消息内容") is None


# ---------- 消费点：裁决优先 / 兜底不变 ----------

def test_scene_lock_prefers_judgment():
    lock = SceneLock(now=1000.0)
    # 词表没有"电话"，判断点说有 → busy
    assert lock.note("我先打个电话哈", judgment="busy") == "busy"
    assert lock.note("喂我回来啦你猜怎么着", judgment="normal") == "normal"
    # 未裁决：旧词表行为（新实例避免状态残留）
    assert SceneLock(now=1000.0).note("我要去加班了", judgment=None) == "busy"
    assert SceneLock(now=1000.0).note("天气怪好的嘞", judgment=None) == "normal"
    # 词表误伤样本（"游戏"在 busy 表：闲聊提游戏≠在打游戏）：兜底维持旧行为
    assert SceneLock(now=1000.0).note("这游戏剧情真离谱", judgment=None) == "busy"
    # 判断点在场即纠偏
    assert SceneLock(now=1000.0).note("这游戏剧情真离谱你说是不是", judgment="normal") == "normal"


def test_conflict_prefers_judgment_variants():
    tracker = ConflictTracker()
    tracker.open("c1", cause="吵了一架", evidence_ids=[1])
    # 词表外的道歉变体，裁决能推进
    assert note_conflict_from_user_text(tracker, "刚才话说重了哈", judgment="apology") == "clarify"
    # 未裁决时词表外变体不推进（旧行为）
    tracker2 = ConflictTracker()
    tracker2.open("c2", cause="x", evidence_ids=[])
    assert note_conflict_from_user_text(tracker2, "刚才话说重了哈") is None


def test_tension_judgment_overrides_literal_rule():
    # 旧规则：回复≥2字且词面重合低 → +5 skipped；裁决说"none"就豁免
    assert classify_user_tension_event("外面下雨了", direct_question="项目弄完了吗",
                                       judgment="none") is None
    got = classify_user_tension_event("外面下雨了", direct_question="项目弄完了吗")
    assert got is not None and got.event_type == "question_skipped"  # 兜底行为不变
    answered = classify_user_tension_event("弄完了挺顺利", direct_question="项目弄完了吗",
                                           judgment="answered")
    assert answered.event_type == "answered_question"


def test_clarification_judgment_beats_pattern_list():
    assert is_clarification("你确定是这个数？", judgment=True) is True
    assert is_clarification("什么时候", judgment=False) is False  # 词表命中也被裁决否决
    assert is_clarification("你确定是这个数？") is False  # 兜底：旧词表漏判（如实钉住）


def test_search_wants_search_reshapes_decision():
    trig = SearchTrigger()
    # 无时效词的新话题：规则不搜；裁决 True → 搜
    d_rule = trig.determine("那个新出的动画电影口碑崩了你知道吗", allow_implicit=True)
    d_judge = trig.determine("那个新出的动画电影口碑崩了你知道吗", allow_implicit=True,
                             wants_search=True)
    assert d_judge.should_search is True
    # 裁决 False 否决隐式（时效词命中也不搜）
    d_no = trig.determine("最新的Python版本有哪些新功能", allow_implicit=True, wants_search=False)
    assert d_no.should_search is False
    # "别联网"永远高于裁决
    assert trig.determine("别联网了，最新的Python版本有哪些", wants_search=True).should_search is False


def test_task_route_uses_judgment():
    mgr = QQTaskSessionManager.__new__(QQTaskSessionManager)
    mgr.bridge = object()  # enabled property = bridge is not None
    mgr.awaiting_approval = {}
    mgr.pending_confirm = {}
    mgr.running = {}
    # 不带"帮我"且词表全漏的任务说法：裁决命中
    hit = mgr.route("u1", "桌面上那个压缩包你拆开看看里有啥", is_task=True)
    assert hit and hit["action"] == "new_task"
    assert mgr.route("u1", "桌面上那个压缩包你拆开看看里有啥") is None  # 兜底=旧行为（漏）


def test_qq_state_machine_uses_signal():
    from veranima.core.qq_proactive import QQProactiveEngine, QQProactiveState
    eng = QQProactiveEngine({})
    st = QQProactiveState()
    # 词表外变体靠裁决
    eng.note_user_message(st, "emo了 不想理人", user_state_signal="low_mood")
    assert st.user_state.value == "low_mood"
    st2 = QQProactiveState()
    eng.note_user_message(st2, "心情不好")  # 词表兜底仍工作
    assert st2.user_state.value == "low_mood"
