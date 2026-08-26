from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import pytest
from PIL import Image, ImageDraw

from veranima.core.image_payload import make_image_payload
from veranima.core.agent import _parse_sticker_json
from veranima.core.stickers import StickerLibrary, build_sticker_query, dhash, hamming


def make_png(kind: str = "rect", size: tuple[int, int] = (64, 64)) -> bytes:
    buf = io.BytesIO()
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    if kind == "rect":
        draw.rectangle((4, 8, size[0] - 8, size[1] - 5), fill="red")
    elif kind == "circle":
        draw.ellipse((8, 4, size[0] - 5, size[1] - 8), fill="blue")
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_dhash_v2_is_canonical_64_bits_and_hamming_rejects_mixed_versions():
    value = dhash(make_png())

    assert len(value) == 64
    assert set(value) <= {"0", "1"}
    with pytest.raises(ValueError, match="same length"):
        hamming(value, value[:-1])


def test_v1_index_migrates_without_moving_image_or_copying_legacy_hash(tmp_path):
    root = tmp_path / "stickers"
    root.mkdir()
    raw = make_png()
    image = root / "legacy.png"
    image.write_bytes(raw)
    (root / "index.json").write_text(json.dumps({"entries": [{
        "hash": "0" * 63,
        "file": image.name,
        "meaning": "旧表情",
        "moods": ["无奈"],
        "scenarios": ["旧情境"],
        "uses": 2,
        "content_type": "image/png",
        "animated": False,
        "created_at": "2026-08-20T00:00:00+00:00",
    }]}), encoding="utf-8")

    library = StickerLibrary(root, legacy_owner_scope="qq:123")

    migrated = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert (root / "index.v1.backup.json").exists()
    assert image.exists()
    assert len(library._entries) == 1
    entry = library._entries[0]
    assert entry.dhash == dhash(raw)
    assert entry.dhash_version == 2
    assert entry.status == "active"
    assert entry.owner_scope == "qq:123"
    assert entry.consent == "legacy_auto"
    assert entry.uses == 2


def test_review_candidate_is_not_sendable_until_same_owner_approves(tmp_path):
    library = StickerLibrary(tmp_path / "stickers")
    payload = make_image_payload(make_png("circle"), source="test")

    pending = library.add_candidate(
        payload,
        owner_scope="qq:100",
        source={"channel": "qq", "platform_message_id": "m1"},
        meaning="惊讶",
        moods=["惊讶"],
        scenario_tags=["surprise"],
        scenarios=["突然得知消息"],
        confidence=0.91,
    )

    assert pending is not None
    assert pending.status == "pending"
    assert pending.consent == "review_pending"
    assert library.find_for_query({"moods": {"惊讶"}}, owner_scope="qq:100") == []

    approved = library.approve(pending.id)

    assert approved is not None
    assert approved.status == "active"
    assert approved.consent == "review_approved"
    assert library.find_for_query({"moods": {"惊讶"}}, owner_scope="qq:100") == [approved]
    assert library.find_for_query({"moods": {"惊讶"}}, owner_scope="qq:200") == []


def test_delete_removes_metadata_but_keeps_shared_file_until_last_owner(tmp_path):
    library = StickerLibrary(tmp_path / "stickers")
    payload = make_image_payload(make_png("circle"), source="test")
    first = library.add_candidate(payload, owner_scope="qq:100", source={})
    second = library.add_payload(
        payload,
        owner_scope="qq:200",
        source={},
        consent="explicit",
    )

    assert first is not None and second is not None
    assert first.id != second.id
    assert first.file == second.file
    shared_file = library.path_for(first)

    assert library.reject(first.id) is True
    assert shared_file.exists()
    assert library.delete(second.id) is True
    assert not shared_file.exists()
    assert library.list_entries() == []


def test_pending_candidate_expires_and_deletes_its_unreferenced_file(tmp_path):
    library = StickerLibrary(tmp_path / "stickers", pending_ttl_days=7)
    pending = library.add_candidate(
        make_image_payload(make_png(), source="test"),
        owner_scope="qq:100",
        source={},
    )
    assert pending is not None
    pending.created_at = "2026-08-01T00:00:00+00:00"
    library._save()
    path = library.path_for(pending)

    expired = library.cleanup_pending(
        now=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )

    assert expired == [pending.id]
    assert not path.exists()
    assert library.list_entries() == []


def test_library_limit_refuses_new_entry_without_deleting_existing_asset(tmp_path):
    library = StickerLibrary(tmp_path / "stickers", max_items=1)
    first = library.add(
        make_png("rect"),
        owner_scope="qq:100",
        moods=["开心"],
    )

    second = library.add(
        make_png("circle"),
        owner_scope="qq:100",
        moods=["惊讶"],
    )

    assert first is not None
    assert second is None
    assert library.path_for(first).exists()
    assert library.list_entries() == [first]


def test_disabled_entry_is_persisted_but_never_returned_for_sending(tmp_path):
    root = tmp_path / "stickers"
    library = StickerLibrary(root)
    entry = library.add(
        make_png(),
        owner_scope="qq:100",
        moods=["开心"],
    )
    assert entry is not None

    assert library.set_enabled(entry.id, False) is True
    assert library.find_for_query({"moods": {"开心"}}, owner_scope="qq:100") == []

    reloaded = StickerLibrary(root)
    assert reloaded.list_entries(status="disabled")[0].id == entry.id
    assert reloaded.set_enabled(entry.id, True) is True
    assert reloaded.find_for_query({"moods": {"开心"}}, owner_scope="qq:100")


def test_find_for_query_skips_missing_asset_file(tmp_path):
    library = StickerLibrary(tmp_path / "stickers")
    entry = library.add(make_png(), owner_scope="qq:100", moods=["开心"])
    assert entry is not None
    library.path_for(entry).unlink()

    assert library.find_for_query({"moods": {"开心"}}, owner_scope="qq:100") == []


def test_sticker_annotation_requires_governed_kind_confidence_moods_and_tags():
    valid = _parse_sticker_json(json.dumps({
        "is_sticker": True,
        "kind": "sticker",
        "confidence": 0.92,
        "meaning": "表示惊讶",
        "moods": ["惊讶"],
        "scenario_tags": ["surprise"],
        "scenarios": ["突然得知消息"],
    }, ensure_ascii=False))

    assert valid is not None
    assert valid["kind"] == "sticker"
    assert valid["scenario_tags"] == ["surprise"]
    assert _parse_sticker_json(json.dumps({
        "is_sticker": True,
        "kind": "sticker",
        "confidence": 0.9,
        "meaning": "非法标签",
        "moods": ["狂喜"],
        "scenario_tags": ["surprise"],
        "scenarios": [],
    }, ensure_ascii=False)) is None
    assert _parse_sticker_json('{"is_sticker": true, "meaning": "缺少契约字段"}') is None


@pytest.mark.parametrize(("text", "mood"), [
    ("哈哈，太好了", "开心"),
    ("这确实让人难过", "难过"),
    ("这事真的让人生气", "生气"),
    ("离谱，我都无语了", "无语"),
    ("居然会这样？", "惊讶"),
    ("加油，你可以的", "鼓励"),
    ("逗你的，别当真", "调侃"),
    ("算了，确实没办法", "无奈"),
    ("嗯嗯，行吧", "敷衍"),
    ("就当我在卖萌", "卖萌"),
])
def test_build_sticker_query_consumes_every_governed_mood(text, mood):
    query = build_sticker_query(None, text, "")

    assert mood in query["moods"]
