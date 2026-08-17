"""IM 通道渲染器测试（DESIGN 4.8）：感叹号限频 / 波浪号阈值 / 换行压缩 / 表情限频。"""
import pytest

from veranima.core.render import render_im


def test_exclamation_limited_per_paragraph():
    t = "太好了！！我们成功了！！！"
    out = render_im(t, attachment=0.9)
    assert out.count("！") == 1
    assert out.count("。") == 4  # 5 个感叹号降级 4 个为句号


def test_multi_paragraph_each_keeps_one():
    t = "第一段！！\n\n第二段！！！"
    out = render_im(t, attachment=0.9)
    assert out.count("！") == 2  # 每段各 1 个


def test_tilde_stripped_below_threshold():
    out = render_im("好的呀～那明天见～", attachment=0.5)
    assert "～" not in out


def test_tilde_kept_above_threshold():
    out = render_im("好的呀～那明天见～", attachment=0.85)
    assert "～" in out


def test_newline_compression():
    t = "第一句\n\n\n\n\n第二句"
    out = render_im(t, attachment=0.9)
    assert "\n\n\n" not in out


def test_emoji_stripped_when_never():
    out = render_im("好开心😊😊", attachment=0.9, emoji_frequency="never")
    assert "😊" not in out


def test_emoji_kept_when_low():
    out = render_im("好开心😊", attachment=0.9, emoji_frequency="low")
    assert "😊" in out


def test_channel_context_in_system_prompt():
    """build_system_prompt 注入通道语境（im 不含 tts 专属块，反之亦然）。"""
    from veranima.core.prompts import build_system_prompt, CHANNEL_CONTEXT
    assert "打字聊天" in CHANNEL_CONTEXT["im"]
    assert "填充词" in CHANNEL_CONTEXT["tts"]
    assert CHANNEL_CONTEXT["im"] != CHANNEL_CONTEXT["tts"]


def test_handle_accepts_channel_param():
    """handle(channel=...) 签名存在且默认 im（不破坏既有调用）。"""
    import inspect
    from veranima.core.agent import Agent
    sig = inspect.signature(Agent.handle)
    assert "channel" in sig.parameters
    assert sig.parameters["channel"].default == "im"
