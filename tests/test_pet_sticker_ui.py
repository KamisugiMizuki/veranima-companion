from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sticker_settings_ui_has_a_complete_bridge_contract():
    html = (ROOT / "pet" / "settings.html").read_text(encoding="utf-8")
    renderer = (ROOT / "pet" / "settings-renderer.js").read_text(encoding="utf-8")
    preload = (ROOT / "pet" / "preload.js").read_text(encoding="utf-8")
    main = (ROOT / "pet" / "main.js").read_text(encoding="utf-8")
    server = (ROOT / "src" / "veranima" / "pet_server.py").read_text(encoding="utf-8")

    for element_id in (
        "sticker-learning-mode", "sticker-send-rate", "sticker-min-gap",
        "sticker-ttl", "sticker-max-items", "sticker-dir", "sticker-list",
        "qq-image-roots", "qq-trusted-image-proxy",
    ):
        assert f'id="{element_id}"' in html
        assert f"$('{element_id}')" in renderer
    assert "settings-sticker-list" in preload and "settings-sticker-action" in preload
    assert "settings-sticker-list" in main and "settings-sticker-action" in main
    assert 'mtype == "sticker_list"' in server
    assert 'mtype == "sticker_action"' in server
    assert "_validate_sticker_settings" in server


def test_sticker_settings_ui_uses_native_directory_picker_and_selects():
    html = (ROOT / "pet" / "settings.html").read_text(encoding="utf-8")
    renderer = (ROOT / "pet" / "settings-renderer.js").read_text(encoding="utf-8")

    assert '<select id="sticker-learning-mode">' in html
    assert '<select id="sticker-send-rate">' in html
    assert "pickPath({ type: 'dir'" in renderer
    assert "sticker-dir-browse" in renderer
    assert "qq-image-root-browse" in renderer


def test_sticker_settings_reject_nonexistent_or_file_image_roots(tmp_path):
    from veranima.pet_server import _validate_image_roots

    file_path = tmp_path / "not-a-directory"
    file_path.write_text("x", encoding="utf-8")
    assert _validate_image_roots([str(tmp_path / "missing")])
    assert _validate_image_roots([str(file_path)])
    assert _validate_image_roots([str(tmp_path)]) == []
