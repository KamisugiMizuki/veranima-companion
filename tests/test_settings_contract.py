"""设置页封装契约：UI 字段存在，后端配置读写白名单覆盖。"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_settings_ui_ids_and_config_contract():
    html = (ROOT / "pet/settings.html").read_text(encoding="utf-8")
    js = (ROOT / "pet/settings-renderer.js").read_text(encoding="utf-8")
    server = (ROOT / "src/veranima/pet_server.py").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r"\$\('([^']+)'\)", js))
    assert used <= ids
    for field in ("max_tokens", "timeout", "embedding_model", "recall_top_k", "max_injected_chars", "curator_turns", "observe_daily_budget", "channels", "proactive"):
        assert field in server
    assert '"****" not in str(llm["api_key"])' in server
    assert "已暂停观察与截图" in html
    assert "暂停后不会进行屏幕扫描、截图或视觉主动触发" in html
    assert "pro-quiet" in html + js + server
    preload = (ROOT / "pet/preload.js").read_text(encoding="utf-8")
    for field in ("stt-enabled", "stt-device", "input_device_id"):
        assert field in html + js + server
    assert "getSttInputDevice" in preload
    for field in ("llm-profile-select", "llm-profile-name", "llm-add-profile", "llm-switch", "llm-delete"):
        assert field in html + js
    for field in ("pro-qq-gap", "pro-pet-gap", "pro-qq-source-gap", "pro-pet-source-gap"):
        assert field in html + js
    for action in ("add", "update", "switch", "delete"):
        assert f"'{action}'" in js
    assert "llm_profile_action" in server
    assert "profileConfig" in preload + js


def test_settings_does_not_serialize_masked_key():
    js = (ROOT / "pet/settings-renderer.js").read_text(encoding="utf-8")
    assert "if (key) data.api_key = key" in js
    assert "llm-key-status" in js
