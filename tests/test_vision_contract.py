"""VISION_SPEC 契约测试（9 验收相关）。

覆盖：AttentionEvent 信封（event_id/expires_at）、Observation 契约解析、
失败降级（unknown/0）、VisibilityPolicy 敏感窗口/暂停、away 状态机。
"""
from __future__ import annotations

import time

from veranima.core.attention import AttentionScheduler, AttentionEvent, Observation
from veranima.core.attention.observer import _parse_observation
from veranima.core.attention.visibility_policy import VisibilityPolicy


class FakeLLM:
    def __init__(self, resp: str | None = None, raise_exc: bool = False):
        self._resp = resp
        self._raise = raise_exc
        self.calls = 0

    def observe_image(self, b64, prompt=""):
        self.calls += 1
        if self._raise:
            raise RuntimeError("llm down")
        return self._resp


def test_event_envelope():
    ev = AttentionEvent(kind="window_switch", note="Chrome", source="foreground")
    assert ev.event_id
    assert ev.expires_at > ev.ts
    assert ev.confidence == 0.7
    assert ev.source == "foreground"
    assert ev.reason == ""


def test_event_compat_fields():
    """旧字段 kind/region/tag/note/ts 保留兼容（VISION_SPEC 1）。"""
    ev = AttentionEvent(kind="fixation_shift", region=(0.1, 0.1, 0.5, 0.5), tag="游戏")
    assert ev.kind == "fixation_shift"
    assert ev.region == (0.1, 0.1, 0.5, 0.5)
    assert ev.tag == "游戏"


def test_observation_parse_ok():
    resp = '{"summary":"用户在看代码编辑器，窗口有报错信息",' \
           '"category":"coding","notable":["红色报错","终端"],' \
           '"confidence":0.85,"sensitive_redacted":false}'
    d = _parse_observation(resp)
    assert d["summary"].startswith("用户在看代码编辑器")
    assert d["category"] == "coding"
    assert len(d["notable"]) == 2
    assert d["confidence"] == 0.85


def test_observation_parse_fence_and_fallback():
    resp = '```json\n{"summary":"x","category":"bad_cat","confidence":2.0}\n```'
    d = _parse_observation(resp)
    assert d["category"] == "unknown"  # 白名单外回退
    assert d["confidence"] == 1.0      # clamp
    assert d["notable"] == []


def test_observation_parse_garbage():
    assert _parse_observation("不是 JSON") == {}
    assert _parse_observation("") == {}


def test_observe_success():
    from veranima.core.attention.observer import observe
    llm = FakeLLM(resp='{"summary":"用户在看视频","category":"video","confidence":0.8}')
    obs = observe(llm, "BASE64", window_title="浏览器")
    assert obs.is_valid
    assert obs.summary == "用户在看视频"
    assert obs.category == "video"
    assert obs.confidence == 0.8
    assert not obs.expired


def test_observe_failure_degrades():
    from veranima.core.attention.observer import observe
    # LLM 异常
    obs = observe(FakeLLM(raise_exc=True), "BASE64")
    assert not obs.is_valid
    assert obs.category == "unknown" and obs.confidence == 0.0
    # 空响应
    obs2 = observe(FakeLLM(resp=None), "BASE64")
    assert not obs2.is_valid
    # 无 llm
    obs3 = observe(None, "BASE64")
    assert not obs3.is_valid


def test_observation_expired():
    obs = Observation(summary="x", category="coding", confidence=0.8,
                      expires_at=time.time() - 1)
    assert obs.expired


def test_policy_sensitive_blocked():
    p = VisibilityPolicy({})
    verdict = p.policy_action("银行App", "")
    assert verdict["action"] == "block"
    assert verdict["category"] == "sensitive"
    verdict2 = p.policy_action("Chrome", "某网站")
    assert verdict2["action"] == "capture"


def test_policy_paused_and_disabled():
    assert VisibilityPolicy({"paused": True}).paused_now()
    p = VisibilityPolicy({"enabled": False})
    assert p.policy_action("Chrome")["action"] == "block"


def test_policy_sensitive_keyword_in_title():
    p = VisibilityPolicy({})
    assert p.is_sensitive("Chrome", "工商银行登录")
    assert not p.is_sensitive("Chrome", "天气查询")


def test_scheduler_away_state(monkeypatch):
    """VISION_SPEC 3：30min 无输入 → away；note_user_input 恢复。"""
    import veranima.core.attention.perception as perception
    import numpy as np

    monkeypatch.setattr(perception, "grab_gray_downsampled",
                        lambda scale=8: np.zeros((60, 80), dtype=np.uint8))

    att = AttentionScheduler(config={"away_idle_s": 1.0})
    att._last_input_ts = time.time() - 2.0  # 模拟超时
    events = att.tick()
    assert any(e.kind == "away" for e in events)
    assert att.state == "away"
    # away 期间不再产出其他事件
    assert all(e.kind == "away" for e in att.tick())
    # 用户输入恢复
    att.note_user_input()
    assert att.state == "fixation"
