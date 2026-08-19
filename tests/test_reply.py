"""R0 统一 Reply 解析测试（R0_SPEC 7）。

覆盖：纯文本/JSON/fence/残缺/多段/非法标签/双语缺 ja/空输出/截断。
"""
from __future__ import annotations

from veranima.core.reply import Reply, parse_reply


class FakeCard:
    """最小角色卡（R0_SPEC 4.6 白名单用）。"""
    name = "Yuki"
    tones = ["中性", "平静", "温柔", "调侃", "疲惫"]
    veranima = {"avatar": {"expressions": {"微笑": "smile.png", "闲置": "idle.png"}}}


def test_im_plain_text():
    r = parse_reply("你好呀", channel="im")
    assert not r.degraded
    assert r.text == "你好呀"
    assert len(r.segments) == 1


def test_im_truncated():
    r = parse_reply("x" * 2000, channel="im", max_chars=1200)
    assert len(r.text) == 1200


def test_empty_degraded():
    r = parse_reply("   ", channel="im")
    assert r.degraded == "empty_output"
    r = parse_reply("", channel="tts")
    assert r.degraded == "empty_output"


def test_tts_json_single():
    raw = '{"segments":[{"text":"你好","tone":"平静","portrait":"微笑"}]}'
    r = parse_reply(raw, channel="tts", card=FakeCard())
    assert not r.degraded
    assert r.text == "你好"
    assert r.tone == "平静"
    assert r.portrait == "微笑"


def test_tts_json_fence():
    raw = '```json\n{"segments":[{"text":"你好","tone":"中性"}]}\n```'
    r = parse_reply(raw, channel="tts", card=FakeCard())
    assert not r.degraded
    assert r.text == "你好"


def test_tts_multi_segment_capped():
    segs = [{"text": f"s{i}"} for i in range(10)]
    raw = '{"segments":' + str(segs).replace("'", '"') + "}"
    r = parse_reply(raw, channel="tts", card=FakeCard(), max_segments=6)
    assert len(r.segments) == 6


def test_tts_broken_json_regex_fallback():
    # 残缺开头 + 一段可解析对象（历史实测：LLM 输出两段，第一段残缺）
    raw = '前导杂讯 {"segments":[{"text":"有效内容"}]} 尾部'
    r = parse_reply(raw, channel="tts", card=FakeCard())
    assert not r.degraded
    assert r.text == "有效内容"


def test_tts_invalid_tone_portrait_fallback():
    raw = '{"segments":[{"text":"你好","tone":"暴怒","portrait":"不存在的表情"}]}'
    r = parse_reply(raw, channel="tts", card=FakeCard())
    assert r.tone == "中性"       # 白名单外回退
    assert r.portrait == ""        # 词表外回退


def test_tts_bilingual_missing_ja_suppress():
    raw = '{"segments":[{"zh":"只有中文","ja":"","tone":"中性"}]}'
    r = parse_reply(raw, channel="tts", card=FakeCard(), bilingual=True)
    assert r.text == "只有中文"
    assert r.segments[0].suppress_tts is True
    assert r.ja_text == ""


def test_tts_bilingual_with_ja():
    raw = '{"segments":[{"ja":"こんにちは","zh":"你好","tone":"平静"}]}'
    r = parse_reply(raw, channel="tts", card=FakeCard(), bilingual=True)
    assert r.text == "你好"
    assert r.ja_text == "こんにちは"
    assert r.segments[0].suppress_tts is False


def test_tts_truncate_chars():
    raw = '{"segments":[{"text":"' + "长" * 3000 + '"}]}'
    r = parse_reply(raw, channel="tts", card=FakeCard(), max_chars=1200)
    assert len(r.text) == 1200


def test_tts_not_object_degraded():
    r = parse_reply("纯文本没有 JSON", channel="tts", card=FakeCard())
    assert not r.degraded
    assert r.text == "纯文本没有 JSON"  # fallback 原文


def test_reply_properties_empty():
    r = Reply()
    assert r.text == ""
    assert r.tone == "中性"
    assert r.portrait == ""
    assert r.ja_text == ""
