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
    for field in ("max_tokens", "timeout", "embedding_model", "recall_top_k", "max_injected_chars", "curator_turns", "observe_daily_budget", "source_gap_minutes"):
        assert field in server
    assert '"****" not in str(llm["api_key"])' in server


def test_settings_does_not_serialize_masked_key():
    js = (ROOT / "pet/settings-renderer.js").read_text(encoding="utf-8")
    assert "if (key) data.llm.api_key = key" in js
    assert "llm-key-status" in js
