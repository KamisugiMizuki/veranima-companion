from __future__ import annotations

from veranima.config import llm_profile_action, llm_profiles_payload, migrate_llm_profile, normalize_llm_profiles
from veranima.llm.client import LLMClient


def test_proactive_channel_defaults_split_legacy_config():
    from veranima.config import normalize_proactive_channels
    data = {"proactive": {"min_gap_minutes": 30, "max_per_day": 2}}

    normalize_proactive_channels(data)

    assert data["proactive"]["channels"]["qq"]["min_gap_minutes"] == 30
    assert "source_gap_minutes" not in data["proactive"]["channels"]["pet"]


def test_relationship_tension_defaults_are_normalized():
    from veranima.config import normalize_relationship_tension

    data = {}
    normalize_relationship_tension(data)

    assert data["relationship_tension"]["enabled"] is True
    assert data["relationship_tension"]["decay_interval_hours"] == 6
    assert data["relationship_tension"]["high_tension_proactive"] is False


def _legacy():
    return {
        "llm": {
            "base_url": "https://example.test/v1",
            "model": "remote-model",
            "api_key": "secret-value",
            "temperature": 0.7,
            "max_tokens": 2048,
            "timeout": 90,
        }
    }


def test_legacy_llm_is_migrated_to_default_profile_without_losing_effective_fields():
    cfg = normalize_llm_profiles(_legacy())

    assert cfg["llm"]["active_profile"] == "default"
    assert cfg["llm"]["profiles"]["default"]["model"] == "remote-model"
    assert cfg["llm"]["base_url"] == "https://example.test/v1"
    assert cfg["llm"]["api_key"] == "secret-value"


def test_profile_add_update_switch_and_delete():
    cfg = normalize_llm_profiles(_legacy())
    added = llm_profile_action(cfg, "add", {
        "name": "LM Studio",
        "base_url": "http://localhost:12345/v1",
        "model": "local-model",
        "temperature": 0.8,
        "max_tokens": 4096,
        "api_key": "",
        "timeout": 180,
    })
    profile_id = added["profile_id"]
    assert profile_id == "lm-studio"
    assert cfg["llm"]["active_profile"] == "default"

    llm_profile_action(cfg, "update", {
        "profile_id": profile_id,
        "model": "local-model-updated",
        "api_key": "****",
    })
    assert cfg["llm"]["profiles"][profile_id]["model"] == "local-model-updated"
    assert cfg["llm"]["profiles"][profile_id]["api_key"] == ""

    llm_profile_action(cfg, "switch", {"profile_id": profile_id})
    assert cfg["llm"]["active_profile"] == profile_id
    assert cfg["llm"]["model"] == "local-model-updated"

    llm_profile_action(cfg, "switch", {"profile_id": "default"})
    llm_profile_action(cfg, "delete", {"profile_id": profile_id})
    assert profile_id not in cfg["llm"]["profiles"]


def test_profile_payload_masks_keys_and_marks_active():
    cfg = normalize_llm_profiles(_legacy())
    payload = llm_profiles_payload(cfg)
    assert payload["active_profile"] == "default"
    assert payload["profiles"][0]["api_key"] == "secr****alue"
    assert payload["api_key"] == "secr****alue"


def test_profile_cannot_delete_active_or_last_profile():
    cfg = normalize_llm_profiles(_legacy())
    try:
        llm_profile_action(cfg, "delete", {"profile_id": "default"})
    except ValueError as exc:
        assert "当前" in str(exc)
    else:
        raise AssertionError("deleting active profile should fail")


def test_lm_studio_import_keeps_local_defaults():
    cfg = normalize_llm_profiles(_legacy())
    pid = migrate_llm_profile(cfg, name="LM Studio", source={
        "base_url": "http://localhost:12345/v1",
        "model": "qwen3.5-9b-uncensored-hauhaucs-aggressive@q4_k_m",
        "temperature": 0.8,
        "max_tokens": 4096,
        "timeout": 180,
    })
    assert pid == "lm-studio"
    assert cfg["llm"]["profiles"][pid]["api_key"] == ""
    assert cfg["llm"]["profiles"][pid]["timeout"] == 180


def test_switch_changes_effective_llm_client_config():
    cfg = normalize_llm_profiles(_legacy())
    added = llm_profile_action(cfg, "add", {
        "name": "LM Studio", "base_url": "http://localhost:12345/v1",
        "model": "local-model", "temperature": 0.6, "max_tokens": 3072,
        "api_key": "", "timeout": 180,
    })
    llm_profile_action(cfg, "switch", {"profile_id": added["profile_id"]})
    client = LLMClient(cfg["llm"])
    assert client.base_url == "http://localhost:12345/v1"
    assert client.model == "local-model"
    assert client.temperature == 0.6
    assert client.max_tokens == 3072
    assert client._timeout == 180
