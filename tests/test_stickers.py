"""8.6.3 表情包库测试：dHash 判重 / 入库 / 情绪匹配 / 标注解析。

不连真实 LLM：FakeLLM 可编程返回标注 JSON；图片用 Pillow 生成内存 PNG。
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from veranima.core.agent import _parse_sticker_json
from veranima.core.stickers import StickerLibrary, dhash, hamming


def make_png(kind="red_rect", size=(64, 64)) -> bytes:
    """生成一张带结构的 PNG（Pillow 内存绘制）。

    纯色块图 dHash 全 0（无梯度，感知哈希已知边界），
    真实表情包有内容，故测试用矩形/圆等结构图。
    """
    from PIL import Image, ImageDraw
    buf = io.BytesIO()
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    if kind == "red_rect":
        d.rectangle([0, 0, size[0] - 1, size[1] - 1], fill=(255, 0, 0))
    elif kind == "blue_circle":
        d.ellipse([8, 8, size[0] - 9, size[1] - 9], fill=(0, 0, 255))
    elif kind == "green_triangle":
        d.polygon([(size[0] // 2, 8), (8, size[1] - 9), (size[0] - 9, size[1] - 9)], fill=(0, 255, 0))
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------- dHash 判重 ----------

def test_dhash_same_image_same_hash():
    a = make_png()
    assert dhash(a) == dhash(a)


def test_dhash_similar_image_close():
    """同图不同尺寸：dHash 距离小（≤5），md5 会完全不同。"""
    small = make_png("red_rect", size=(32, 32))
    large = make_png("red_rect", size=(128, 128))
    assert hamming(dhash(small), dhash(large)) <= 5


def test_dhash_different_image_far():
    red_rect = make_png("red_rect")
    blue_circle = make_png("blue_circle")
    assert hamming(dhash(red_rect), dhash(blue_circle)) > 5


# ---------- 入库 ----------

def test_add_and_find_similar(tmp_path):
    lib = StickerLibrary(root=tmp_path / "stickers")
    raw = make_png("red_rect")
    entry = lib.add(raw, meaning="红", moods=["开心"], scenarios=["测试"])
    assert entry is not None
    assert len(lib) == 1
    # 同图（不同尺寸）能识别为见过
    assert lib.find_similar(make_png("red_rect", size=(32, 32))) is not None
    # 不同结构图识别为没见过
    assert lib.find_similar(make_png("blue_circle")) is None


def test_persist_across_reload(tmp_path):
    lib = StickerLibrary(root=tmp_path / "stickers")
    lib.add(make_png(), meaning="红", moods=["开心"])
    lib2 = StickerLibrary(root=tmp_path / "stickers")
    assert len(lib2) == 1
    assert lib2._entries[0].meaning == "红"
    assert lib2._entries[0].moods == ["开心"]


def test_index_json_written(tmp_path):
    lib = StickerLibrary(root=tmp_path / "stickers")
    lib.add(make_png(), meaning="红", moods=["开心"])
    idx = tmp_path / "stickers" / "index.json"
    assert idx.exists()
    assert "红" in idx.read_text(encoding="utf-8")


def test_sticker_files_do_not_collide_on_short_hash_prefix(tmp_path, monkeypatch):
    import veranima.core.stickers as module

    first = make_png("blue_circle")
    second = make_png("green_triangle")
    hashes = {first: "0" * 64, second: "0" * 12 + "1" * 52}
    monkeypatch.setattr(module, "dhash", lambda raw, size=module.DEFAULT_SIZE: hashes[raw])

    lib = StickerLibrary(root=tmp_path / "stickers")
    a = lib.add(first, meaning="A")
    b = lib.add(second, meaning="B")

    assert a is not None and b is not None
    assert a.file != b.file
    assert (lib.root / a.file).read_bytes() == first
    assert (lib.root / b.file).read_bytes() == second


# ---------- 情绪匹配（宽松） ----------

def test_find_for_mood_low_use_first(tmp_path):
    lib = StickerLibrary(root=tmp_path / "stickers")
    a = lib.add(make_png((255, 0, 0)), meaning="A", moods=["开心"])
    b = lib.add(make_png((0, 255, 0)), meaning="B", moods=["开心"])
    c = lib.add(make_png((0, 0, 255)), meaning="C", moods=["难过"])
    a.uses = 5  # A 用过多次 → 优先级降低
    b.uses = 1
    # 开心匹配：候选 a/b（低使用次数优先）→ b 排前；难过不混入
    cands = lib.find_for_mood("开心", limit=3)
    assert cands[0].file == b.file
    assert all(e.file in (a.file, b.file) for e in cands)
    # 无匹配情绪 → 空
    assert lib.find_for_mood("生气", limit=3) == []


def test_record_use_persisted(tmp_path):
    lib = StickerLibrary(root=tmp_path / "stickers")
    e = lib.add(make_png(), meaning="A", moods=["开心"])
    lib.record_use(e)
    lib2 = StickerLibrary(root=tmp_path / "stickers")
    assert lib2._entries[0].uses == 1


# ---------- 标注 JSON 解析 ----------

def test_parse_sticker_json_plain():
    d = _parse_sticker_json('{"is_sticker": true, "meaning": "没问题", "moods": ["开心"], "scenarios": ["用户答应请求"]}')
    assert d["meaning"] == "没问题"
    assert d["moods"] == ["开心"]


def test_parse_sticker_json_fenced():
    """thinking 模型常输出 ```json 围栏，需剥离。"""
    d = _parse_sticker_json('```json\n{"is_sticker": true, "meaning": "哈哈", "moods": ["调侃"], "scenarios": ["开玩笑"]}\n```')
    assert d["meaning"] == "哈哈"
    assert d["moods"] == ["调侃"]


def test_parse_sticker_json_noise():
    """围栏外有杂文本也能提取。"""
    d = _parse_sticker_json('好的，这是标注：{"is_sticker": true, "meaning": "加油", "moods": ["鼓励"]} 完毕')
    assert d["meaning"] == "加油"


def test_parse_sticker_json_invalid():
    assert _parse_sticker_json("无法解析的内容") is None
    assert _parse_sticker_json('{"meaning": "不完整"') is None


def test_parse_sticker_json_requires_literal_boolean_true():
    assert _parse_sticker_json('{"meaning": "普通照片"}')["is_sticker"] is False
    assert _parse_sticker_json('{"is_sticker": "false", "meaning": "普通照片"}')["is_sticker"] is False
