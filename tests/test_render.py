"""IM 通道渲染器测试（DESIGN 4.8）：感叹号限频 / 波浪号阈值 / 换行压缩 / 表情限频。

09-08 用户裁决「情绪太平稳」后契约变更：限频只删不替换、句尾句号删除、
arousal 高时感叹号放宽——旧断言（多余感叹号降级成句号）已作废。
"""
import pytest

from veranima.core.render import render_im


def test_exclamation_limited_per_paragraph():
    t = "太好了！！我们成功了！！！"
    out = render_im(t, attachment=0.9)
    assert out.count("！") == 1
    assert "。" not in out  # 只删不替换：不再把多余感叹号降级成句号
    assert out == "太好了！我们成功了"


def test_multi_paragraph_each_keeps_one():
    t = "第一段！！\n\n第二段！！！"
    out = render_im(t, attachment=0.9)
    assert out.count("！") == 2  # 每段各 1 个


def test_tilde_stripped_below_threshold():
    out = render_im("好的呀～那明天见～", attachment=0.5)
    assert "～" not in out
    assert "。" not in out  # 删掉而不是替换成句号


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


def test_trailing_period_trimmed():
    """网络聊天不打句尾句号（09-08 实测：角色 91% 带句尾标点、用户 17%）。"""
    assert render_im("我先睡了。", attachment=0.5) == "我先睡了"
    assert render_im("行吧。\n明天再说。", attachment=0.5) == "行吧\n明天再说"
    assert render_im("气气气。。", attachment=0.5) == "气气气。。"  # 情绪重复保留


def test_hot_arousal_keeps_more_exclamations():
    """被调戏/激动时（arousal 高）允许连打标点（09-08 Q2 用户裁决）。"""
    from veranima.core.reply import Reply, ReplySegment
    from veranima.core.state import AgentState

    st = AgentState()
    st.arousal = 0.8
    st.attachment = 0.9
    out = render_im(Reply(segments=[ReplySegment(text="啊啊啊！！！好烦！！！")]), st)
    assert out.count("！") == 3

    st2 = AgentState()
    st2.arousal = 0.5
    st2.attachment = 0.9
    out2 = render_im(Reply(segments=[ReplySegment(text="啊啊啊！！！好烦！！！")]), st2)
    assert out2.count("！") == 1


def test_channel_context_in_system_prompt():
    """build_system_prompt 注入通道语境（im 不含 tts 专属块，反之亦然）。"""
    from veranima.core.prompts import build_system_prompt, CHANNEL_CONTEXT
    assert "文字聊天" in CHANNEL_CONTEXT["im"]
    assert "填充词" in CHANNEL_CONTEXT["tts"]
    assert CHANNEL_CONTEXT["im"] != CHANNEL_CONTEXT["tts"]


def test_handle_accepts_channel_param():
    """handle(channel=...) 签名存在且默认 im（不破坏既有调用）。"""
    import inspect
    from veranima.core.agent import Agent
    sig = inspect.signature(Agent.handle)
    assert "channel" in sig.parameters
    assert sig.parameters["channel"].default == "im"


def test_bridge_render_never_returns_non_str():
    """09-08 实锤：reply_obj 空段 + `render_im(...) or text` 回退 → 把 Reply 对象
    当返回值 → bridge json.dumps TypeError。契约：任何入参都返回 str。"""
    import importlib.util
    from pathlib import Path

    from veranima.core.reply import Reply, ReplySegment

    path = Path(__file__).resolve().parents[1] / "android/fuyuno/app/src/main/python/bridge.py"
    spec = importlib.util.spec_from_file_location("fuyuno_bridge_render_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class _Agent:
        card = None

        class state:
            attachment = 0.5

    assert mod._render(_Agent(), Reply()) == ""
    assert mod._render(_Agent(), Reply(), "delta 你好") == "delta 你好"
    assert isinstance(mod._render(_Agent(), Reply(segments=[ReplySegment(text="你好")])), str)
