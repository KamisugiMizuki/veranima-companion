"""Style Learning 离线语料流水线行为测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from veranima.core.style_corpus import StyleCorpusStore


def test_ingest_cleans_splits_deduplicates_and_records_provenance(tmp_path):
    source = tmp_path / "dialogue.md"
    source.write_text(
        "这是第一段。结论先说，然后补充原因。\n\n"
        "这是第一段。结论先说，然后补充原因。\n\n"
        "我的邮箱是 alice@example.com，API_KEY=[REDACTED]，请尽快处理。\n\n"
        "Authorization: Bearer [REDACTED]\n\n"
        "我住在杭州市西湖区某街道 123 号，请记录。\n\n"
        "> 这是引用内容，不是目标说话者。\n\n"
        "```python\nprint('不是自然语言风格')\n```\n\n"
        "嘛，先看输入，再处理，最后给输出。",
        encoding="utf-8",
    )
    store = StyleCorpusStore(tmp_path / "corpora")

    manifest = store.ingest(
        "owned-sample",
        [source],
        source="用户自有文本",
        owner="user",
        license="private-local-consent",
        consent=True,
    )
    segments = store.read_segments("owned-sample")
    persisted = json.dumps(segments, ensure_ascii=False)

    assert manifest["version"] == 1
    assert manifest["authorization"]["consent_at"]
    assert manifest["sources"][0]["sha256"]
    assert manifest["stats"]["duplicate_count"] >= 1
    assert segments and all("weak_labels" in row for row in segments)
    assert all(row["language"] in {"zh", "en", "ja", "mixed", "other"} for row in segments)
    assert all(row["content_type"] in {"natural", "dialogue", "list"} for row in segments)
    assert "alice@example.com" not in persisted
    assert "杭州市西湖区某街道 123 号" not in persisted
    assert "print('不是自然语言风格')" not in persisted
    assert "这是引用内容" not in persisted
    assert manifest["stats"]["excluded_count"] >= 3


def test_ingest_never_persists_sensitive_filename_or_fact_segments(tmp_path):
    source = tmp_path / "person-private-diary.txt"
    secret_line = "pass" + "word: " + "correct horse " + "battery staple。"
    card_line = "我的银行卡号是 " + "6222 0212 " + "3456 7890 123。"
    source.write_text(
        "\n\n".join([
            secret_line,
            card_line,
            "我的工作是南京市第一医院精神科医生。",
            "普通表达样本，先给结论再说明原因。",
        ]),
        encoding="utf-8",
    )
    store = StyleCorpusStore(tmp_path / "corpora")
    manifest = store.ingest(
        "private", [source], source="用户自有文本", owner="user",
        license="private-local-consent", consent=True,
    )
    persisted = json.dumps({
        "manifest": manifest,
        "segments": store.read_segments("private"),
    }, ensure_ascii=False)

    assert "李雷" not in persisted and "HIV" not in persisted
    assert "correct horse" not in persisted
    assert "6222 0212" not in persisted
    assert "南京市第一医院" not in persisted
    assert manifest["sources"][0]["source_index"] == 0


@pytest.mark.parametrize("private_text", [
    "我在南京市第一医院工作，负责心理咨询。",
    "姓名：李雷，这是我常用的表达方式。",
    "我的学校是北京大学，专业是计算机。",
    "我的公司是字节跳动，职位是产品经理。",
    "手机号是 138 0013 8000，请下班后联系我。",
])
def test_ingest_drops_residual_identity_and_spaced_phone_facts(tmp_path, private_text):
    source = tmp_path / "private.txt"
    source.write_text(private_text + "\n\n普通表达样本，先给结论再说明理由。", encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest("private-facts", [source], source="self", owner="user", license="private", consent=True)

    persisted = json.dumps(store.read_segments("private-facts"), ensure_ascii=False)
    assert private_text not in persisted
    assert "普通表达样本" in persisted


def test_replace_failure_keeps_entire_previous_corpus(tmp_path, monkeypatch):
    import veranima.core.style_corpus as module

    old_source = tmp_path / "old.txt"
    new_source = tmp_path / "new.txt"
    old_source.write_text("\n\n".join(f"旧版本样本{i}，保持稳定。" for i in range(24)), encoding="utf-8")
    new_source.write_text("\n\n".join(f"新版本样本{i}，完全不同。" for i in range(24)), encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest("replaceable", [old_source], source="owned", owner="user", license="private", consent=True)
    store.export_review("replaceable", limit=8)
    before_segments = store.read_segments("replaceable")
    before_queue = store.review_path("replaceable").read_bytes()
    real_atomic_json = module._atomic_json

    def fail_new_manifest(path, data):
        if path.name == "manifest.json" and data.get("version") == 2:
            raise OSError("manifest failed")
        return real_atomic_json(path, data)

    monkeypatch.setattr(module, "_atomic_json", fail_new_manifest)
    with pytest.raises(OSError, match="manifest failed"):
        store.ingest(
            "replaceable", [new_source], source="owned", owner="user",
            license="private", consent=True, replace=True,
        )

    assert store.manifest("replaceable")["version"] == 1
    assert store.read_segments("replaceable") == before_segments
    assert store.review_path("replaceable").read_bytes() == before_queue


def test_multilingual_sentence_metrics_handle_english_and_japanese(tmp_path):
    source = tmp_path / "multilingual.txt"
    source.write_text(
        "First sentence is concise. Second sentence adds context. Third sentence asks why?\n\n"
        "これは短い文です。次に理由を説明します。最後に確認しますか？",
        encoding="utf-8",
    )
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest(
        "languages", [source], source="用户自有文本", owner="user",
        license="private-local-consent", consent=True,
    )
    segments = store.read_segments("languages")
    english = next(row for row in segments if row["language"] == "en")
    japanese = next(row for row in segments if row["language"] == "ja")

    assert english["weak_labels"]["avg_sentence_chars"] < english["weak_labels"]["chars"] / 2
    assert japanese["weak_labels"]["avg_sentence_chars"] < japanese["weak_labels"]["chars"] / 2


def test_review_queue_is_small_diverse_and_versioned(tmp_path):
    source = tmp_path / "mixed.txt"
    rows = []
    for i in range(18):
        rows.append(f"直接说结论{i}。然后补充一点。")
    for i in range(18):
        rows.append(f"麻烦您详细解释第{i}项原因，可以吗？谢谢。")
    rows.append("- 第一项只是清单\n- 第二项也只是清单")
    source.write_text("\n\n".join(rows), encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest(
        "mixed", [source], source="用户自有文本", owner="user",
        license="private-local-consent", consent=True,
    )
    queue = store.export_review("mixed", limit=12)
    review_file = store.review_path("mixed")

    assert len(queue) == 12
    assert len({row["bucket"] for row in queue}) >= 2
    assert any(row["risk_flags"] for row in queue)
    assert all(row["decision"] == "pending" for row in queue)
    assert {row["selection_reason"] for row in queue} >= {"risk", "representative"}

    reviewed = []
    for index, row in enumerate(queue):
        row["decision"] = "reject" if index >= 10 else "accept"
        row["annotator"] = "user"
        row["reason"] = "代表性抽查"
        if index == 0:
            row["corrected_labels"] = {"directness": 0.9}
        reviewed.append(row)
    review_file.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in reviewed) + "\n",
        encoding="utf-8",
    )

    summary = store.apply_reviews("mixed")
    saved = store.read_reviews("mixed")

    assert summary == {"reviewed": 12, "accepted": 10, "rejected": 2}
    assert saved[0]["corpus_version"] == 1
    assert saved[0]["reviewed_at"]
    assert saved[0]["annotator"] == "user"

    duplicate = [reviewed[0], reviewed[0]]
    review_file.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in duplicate) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="重复"):
        store.apply_reviews("mixed")


def test_review_apply_rejects_known_but_unexported_segment(tmp_path):
    source = tmp_path / "samples.txt"
    source.write_text("\n\n".join(f"自然表达样本{i}，内容各不相同。" for i in range(24)), encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest("managed", [source], source="owned", owner="user", license="private", consent=True)
    queue = store.export_review("managed", limit=4)
    selected = {row["segment_id"] for row in queue}
    replacement = next(row for row in store.read_segments("managed") if row["segment_id"] not in selected)
    queue[0]["segment_id"] = replacement["segment_id"]
    queue[0]["decision"] = "accept"
    store.review_path("managed").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in queue) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="未导出|复核集合"):
        store.apply_reviews("managed")


def test_activation_requires_review_and_only_injects_aggregate_style(tmp_path, monkeypatch):
    files = []
    for scene in range(3):
        path = tmp_path / f"scene-{scene}.txt"
        path.write_text(
            "\n\n".join(
                f"结论先说，第{scene}-{i}项可以执行。（细节随后补充。）"
                for i in range(20)
            ),
            encoding="utf-8",
        )
        files.append(path)
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest(
        "target-style", files, source="用户自有文本", owner="user",
        license="private-local-consent", consent=True,
    )
    from veranima.core.learning import StyleLearner
    learner = StyleLearner(persist_path=str(tmp_path / "style.json"))

    with pytest.raises(ValueError, match="复核"):
        store.activate("target-style", learner)

    queue = store.export_review("target-style", limit=12)
    review_file = store.review_path("target-style")
    for row in queue:
        row.update({"decision": "accept", "annotator": "user", "reason": "代表样本确认"})
    review_file.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in queue) + "\n",
        encoding="utf-8",
    )
    store.apply_reviews("target-style")

    real_save = learner.save
    monkeypatch.setattr(learner, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        store.activate("target-style", learner)
    assert store.manifest("target-style")["status"] == "preview"
    assert learner.active_corpus_id == ""
    monkeypatch.setattr(learner, "save", real_save)

    import veranima.core.style_corpus as style_corpus_module
    real_atomic_json = style_corpus_module._atomic_json

    def fail_active_manifest(path, data):
        if path.name == "manifest.json" and data.get("status") == "active":
            raise OSError("manifest write failed")
        return real_atomic_json(path, data)

    monkeypatch.setattr(style_corpus_module, "_atomic_json", fail_active_manifest)
    with pytest.raises(OSError, match="manifest write failed"):
        store.activate("target-style", learner)
    assert learner.active_corpus_id == ""
    assert store.manifest("target-style")["status"] == "preview"
    monkeypatch.setattr(style_corpus_module, "_atomic_json", real_atomic_json)

    profile = store.activate("target-style", learner)
    im_brief = learner.build_style_brief("im")
    tts_brief = learner.build_style_brief("tts")

    assert profile.sample_count >= 20 and profile.scene_count == 3
    assert learner.active_corpus_id == "target-style"
    assert im_brief and "【表达适配】" in im_brief.to_prompt_block()
    assert "结论先说，第" not in im_brief.to_prompt_block()
    assert "事实、身份、经历、立场" in im_brief.to_prompt_block()
    assert tts_brief and "emoji" not in tts_brief.to_prompt_block().lower()
    assert "括号" not in tts_brief.to_prompt_block()
    assert learner.load()  # activate 已持久化到现有 style.json
    with pytest.raises(ValueError, match="active|启用"):
        store.ingest(
            "target-style", files, source="用户自有文本", owner="user",
            license="private-local-consent", consent=True, replace=True,
        )

    monkeypatch.setattr(learner, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        store.deactivate("target-style", learner)
    assert store.manifest("target-style")["status"] == "active"
    assert learner.active_corpus_id == "target-style"


def test_delete_clears_active_profile_and_keeps_text_free_audit(tmp_path, monkeypatch):
    source = tmp_path / "owned.txt"
    source.write_text("\n\n".join(f"这是可删除样本{i}，请直接处理。" for i in range(24)), encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest(
        "erasable", [source], source="用户自有文本", owner="user",
        license="private-local-consent", consent=True,
    )
    queue = store.export_review("erasable", limit=8)
    review_file = store.review_path("erasable")
    for row in queue:
        row["decision"] = "accept"
    review_file.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in queue) + "\n",
        encoding="utf-8",
    )
    store.apply_reviews("erasable")
    from veranima.core.learning import StyleLearner
    learner = StyleLearner(persist_path=str(tmp_path / "style.json"))
    store.activate("erasable", learner)

    real_save = learner.save
    monkeypatch.setattr(learner, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        store.delete("erasable", learner)
    assert (tmp_path / "corpora" / "erasable").exists()
    assert not (tmp_path / "corpora" / "deletions.jsonl").exists()
    monkeypatch.setattr(learner, "save", real_save)

    assert store.delete("erasable", learner)
    audit = (tmp_path / "corpora" / "deletions.jsonl").read_text(encoding="utf-8")

    assert not (tmp_path / "corpora" / "erasable").exists()
    assert learner.active_corpus_id == ""
    assert "这是可删除样本" not in audit
    assert '"corpus_id": "erasable"' in audit

    recreated = store.ingest(
        "erasable", [source], source="用户自有文本", owner="user",
        license="private-local-consent", consent=True,
    )
    assert recreated["version"] == 2


def test_delete_failure_does_not_claim_success(tmp_path, monkeypatch):
    import veranima.core.style_corpus as module

    source = tmp_path / "owned.txt"
    source.write_text("普通表达样本，先结论后原因。", encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest("locked", [source], source="owned", owner="user", license="private", consent=True)
    monkeypatch.setattr(module.shutil, "rmtree", lambda path: (_ for _ in ()).throw(PermissionError("locked")))

    with pytest.raises(PermissionError, match="locked"):
        store.delete("locked")

    assert store._dir("locked").exists()
    assert not (tmp_path / "corpora" / "deletions.jsonl").exists()


def test_external_deactivate_refreshes_live_learner_and_cannot_be_resurrected(tmp_path):
    from veranima.core.learning import StyleLearner, UserStyleProfile

    path = tmp_path / "style.json"
    profile = UserStyleProfile(
        sample_count=20, confidence=0.9, source_id="private",
        avg_message_chars=100, detail_preference=0.8,
    )
    live = StyleLearner(persist_path=str(path))
    live.activate_corpus("private", profile)
    admin = StyleLearner(persist_path=str(path))
    assert admin.load()
    assert admin.clear_corpus("private")

    assert live.build_style_brief("im") is None
    live.params.formality = 0.2
    live.save()
    persisted = StyleLearner(persist_path=str(path))
    assert persisted.load()
    assert persisted.active_corpus_id == ""


def test_reset_tombstone_prevents_live_learner_from_resurrecting_corpus(tmp_path):
    from veranima.core.learning import StyleLearner, UserStyleProfile

    path = tmp_path / "style.json"
    writer = StyleLearner(persist_path=str(path))
    writer.activate_corpus("private", UserStyleProfile(
        sample_count=20, char_count=2000, avg_message_chars=100,
        confidence=1.0, source_id="private", scene_count=3,
        reviewed_count=12, quality_score=1.0,
    ))
    live = StyleLearner(persist_path=str(path))
    assert live.load()
    admin = StyleLearner(persist_path=str(path))
    assert admin.load()

    admin.reset()
    live.save()

    persisted = StyleLearner(persist_path=str(path))
    assert persisted.load()
    assert persisted.active_corpus_id == ""


def test_expired_active_corpus_is_deleted_before_style_injection(tmp_path):
    from veranima.core.learning import StyleLearner, UserStyleProfile

    store = StyleCorpusStore(tmp_path / "style_corpora")
    target = store._dir("expired")
    target.mkdir()
    (target / "manifest.json").write_text(json.dumps({
        "corpus_id": "expired", "version": 1, "status": "active",
        "retention_until": "2000-01-01T00:00:00+00:00",
        "delete_scope": "corpus", "sources": [],
    }), encoding="utf-8")
    learner = StyleLearner(persist_path=str(tmp_path / "style.json"))
    learner.activate_corpus("expired", UserStyleProfile(
        sample_count=20, confidence=0.9, source_id="expired",
        avg_message_chars=100, detail_preference=0.8,
    ))

    assert learner.build_style_brief("im") is None
    assert not target.exists()
    assert learner.active_corpus_id == ""
    assert '"corpus_id": "expired"' in (tmp_path / "style_corpora" / "deletions.jsonl").read_text(encoding="utf-8")


def test_expired_preview_corpus_is_deleted_without_active_profile(tmp_path):
    from veranima.core.learning import StyleLearner

    source = tmp_path / "expired.txt"
    source.write_text("普通表达样本，先说结论再补原因。", encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "style_corpora")
    store.ingest(
        "expired-preview", [source], source="self", owner="user", license="private",
        consent=True, retention_until="2000-01-01T00:00:00+00:00",
    )
    learner = StyleLearner(persist_path=str(tmp_path / "style.json"))

    assert learner.enforce_retention()
    assert not store._dir("expired-preview").exists()


def test_style_cli_import_and_status_use_production_paths(tmp_path, monkeypatch, capsys):
    source = tmp_path / "cli-source.txt"
    source.write_text("\n\n".join(f"CLI 样本{i}，保持自然表达。" for i in range(24)), encoding="utf-8")
    from veranima import cli

    monkeypatch.setattr(cli, "load_config", lambda: {"root": str(tmp_path)})
    assert cli.main([
        "style", "import", "cli-style", str(source),
        "--source", "用户自有文本", "--owner", "user",
        "--license", "private-local-consent", "--consent",
    ]) == 0
    assert cli.main(["style", "review-export", "cli-style", "--limit", "8"]) == 0
    review_file = tmp_path / "data" / "style_corpora" / "cli-style" / "review_queue.jsonl"
    review_rows = [json.loads(line) for line in review_file.read_text(encoding="utf-8").splitlines()]
    for row in review_rows:
        row.update({"decision": "accept", "annotator": "user", "reason": "CLI 闭环"})
    review_file.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in review_rows) + "\n",
        encoding="utf-8",
    )
    assert cli.main(["style", "review-apply", "cli-style"]) == 0
    assert cli.main(["style", "activate", "cli-style"]) == 0
    assert cli.main(["style", "status", "cli-style"]) == 0
    output = capsys.readouterr().out

    assert "cli-style" in output
    assert (tmp_path / "data" / "style_corpora" / "cli-style" / "manifest.json").exists()
    style_data = json.loads((tmp_path / "data" / "style.json").read_text(encoding="utf-8"))
    assert style_data["active_corpus_id"] == "cli-style"
    assert style_data["schema_version"] == 3

    assert cli.main(["style", "deactivate", "cli-style"]) == 0
    style_data = json.loads((tmp_path / "data" / "style.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (tmp_path / "data" / "style_corpora" / "cli-style" / "manifest.json").read_text(encoding="utf-8")
    )
    assert style_data["active_corpus_id"] == ""
    assert manifest["status"] == "preview"


def test_response_plan_uses_style_length_but_explicit_request_and_state_win():
    from types import SimpleNamespace
    from veranima.core.persona import PersonaBrief, build_response_plan

    brief = PersonaBrief(relevant_user_frameworks=[{"content": "先判断再回答"}])
    normal = SimpleNamespace(conflict_tension=0.0, valence=0.5)
    low = SimpleNamespace(conflict_tension=0.0, valence=0.2)

    assert build_response_plan(
        {"user_text": "普通问题", "style_length": "long"}, brief, normal,
    ).desired_length == "long"
    assert build_response_plan(
        {"user_text": "请详细展开", "style_length": "short"}, brief, normal,
    ).desired_length == "long"
    assert build_response_plan(
        {"user_text": "不要详细说明，简单说就好", "style_length": "long"}, brief, normal,
    ).desired_length == "short"
    assert build_response_plan(
        {"user_text": "普通问题", "explicit_style_length": "short", "style_length": "long"}, brief, normal,
    ).desired_length == "short"
    assert build_response_plan(
        {"user_text": "普通问题", "style_length": "long"}, brief, low,
    ).desired_length == "short"


def test_ingest_rejects_oversized_file_before_processing(tmp_path):
    from veranima.core.style_corpus import MAX_SOURCE_BYTES

    source = tmp_path / "too-large.txt"
    with source.open("wb") as fh:
        fh.seek(MAX_SOURCE_BYTES)
        fh.write(b"x")
    store = StyleCorpusStore(tmp_path / "corpora")
    with pytest.raises(ValueError, match="超过"):
        store.ingest(
            "large", [source], source="用户自有文本", owner="user",
            license="private-local-consent", consent=True,
        )


def test_tampered_unknown_reviews_cannot_satisfy_gate(tmp_path):
    source = tmp_path / "samples.txt"
    source.write_text("\n\n".join(f"自然文本{i}，内容各不相同。" for i in range(24)), encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest(
        "tampered", [source], source="用户自有文本", owner="user",
        license="private-local-consent", consent=True,
    )
    fake = [
        {"segment_id": f"unknown-{i}", "corpus_version": 1, "decision": "accept", "corrected_labels": {}}
        for i in range(12)
    ]
    reviews_path = tmp_path / "corpora" / "tampered" / "reviews.jsonl"
    reviews_path.write_text("\n".join(json.dumps(row) for row in fake) + "\n", encoding="utf-8")
    from veranima.core.learning import StyleLearner

    with pytest.raises(ValueError, match="未通过当前版本 apply|有效复核|至少复核"):
        store.activate("tampered", StyleLearner())


def test_offline_profile_is_order_independent(tmp_path):
    from veranima.core.learning import StyleLearner

    texts = [
        f"第{i}项先给结论，再说明具体原因和处理步骤，可以吗？"
        for i in range(24)
    ]
    profiles = []
    for corpus_id, sequence in (("forward", texts), ("reverse", list(reversed(texts)))):
        source = tmp_path / f"{corpus_id}.txt"
        source.write_text("\n\n".join(sequence), encoding="utf-8")
        store = StyleCorpusStore(tmp_path / "corpora")
        store.ingest(
            corpus_id, [source], source="用户自有文本", owner="user",
            license="private-local-consent", consent=True,
        )
        queue = store.export_review(corpus_id, limit=8)
        review_file = store.review_path(corpus_id)
        for row in queue:
            row["decision"] = "accept"
        review_file.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in queue) + "\n",
            encoding="utf-8",
        )
        store.apply_reviews(corpus_id)
        profiles.append(store.activate(corpus_id, StyleLearner()).snapshot())

    ignored = {"updated_at", "source_id"}
    left = {k: v for k, v in profiles[0].items() if k not in ignored}
    right = {k: v for k, v in profiles[1].items() if k not in ignored}
    assert left == right


def test_review_apply_rejects_modified_exported_content(tmp_path):
    source = tmp_path / "samples.txt"
    source.write_text("\n\n".join(f"这是第{i}条自然表达，用来覆盖不同句子。" for i in range(24)), encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest("immutable", [source], source="self", owner="user", license="private-local-consent", consent=True)
    queue = store.export_review("immutable", limit=4)
    queue[0]["text"] = "被篡改的复核正文。"
    store.review_path("immutable").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in queue) + "\n", encoding="utf-8",
    )
    with pytest.raises(ValueError, match="受管复核内容"):
        store.apply_reviews("immutable")


def test_ingest_caps_segment_amplification(tmp_path, monkeypatch):
    import veranima.core.style_corpus as module

    source = tmp_path / "many.txt"
    source.write_text("\n\n".join(f"唯一自然片段{i}，内容足够长。" for i in range(3)), encoding="utf-8")
    monkeypatch.setattr(module, "MAX_SEGMENTS", 2)
    with pytest.raises(ValueError, match="片段数量"):
        StyleCorpusStore(tmp_path / "corpora").ingest(
            "bounded", [source], source="self", owner="user", license="private-local-consent", consent=True,
        )


@pytest.mark.parametrize("value", ["?", "N/A", "none", "unknown", "unlicensed"])
def test_ingest_rejects_ambiguous_license_placeholders(tmp_path, value):
    source = tmp_path / "owned.txt"
    source.write_text("这是用户本人拥有的自然语言文本。", encoding="utf-8")
    with pytest.raises(ValueError, match="license"):
        StyleCorpusStore(tmp_path / "corpora").ingest(
            "license", [source], source="self", owner="user", license=value, consent=True,
        )


def test_partial_delete_never_reactivates_incomplete_corpus(tmp_path, monkeypatch):
    import veranima.core.style_corpus as module
    from veranima.core.learning import StyleLearner, UserStyleProfile

    source = tmp_path / "owned.txt"
    source.write_text("这是用户本人拥有的自然语言文本。", encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest("partial", [source], source="self", owner="user", license="private-local-consent", consent=True)
    learner = StyleLearner(persist_path=str(tmp_path / "style.json"))
    learner.activate_corpus("partial", UserStyleProfile(
        sample_count=20, char_count=2000, avg_message_chars=100,
        confidence=1.0, source_id="partial", scene_count=1,
        reviewed_count=4, quality_score=1.0,
    ))
    original = module.shutil.rmtree

    def partial_failure(path, *args, **kwargs):
        path = Path(path)
        (path / "segments.jsonl").unlink(missing_ok=True)
        raise PermissionError("locked")

    monkeypatch.setattr(module.shutil, "rmtree", partial_failure)
    with pytest.raises(PermissionError):
        store.delete("partial", learner)
    assert learner.active_corpus_id == ""
    assert not (store.root / "partial").exists()
    assert list(store.root.glob(".partial.deleting*"))
    assert not (store.root / "deletions.jsonl").exists()

    with pytest.raises(RuntimeError, match="删除中"):
        store.ingest(
            "partial", [source], source="self", owner="user",
            license="private-local-consent", consent=True,
        )

    monkeypatch.setattr(module.shutil, "rmtree", original)
    assert store.delete("partial", learner)
    assert not list(store.root.glob(".partial.deleting*"))
    audit = [json.loads(line) for line in (store.root / "deletions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit[0]["corpus_id"] == "partial"


def test_replace_backup_cleanup_failure_is_reported_and_delete_retries(tmp_path, monkeypatch):
    import veranima.core.style_corpus as module

    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.write_text("旧版本自然表达，先结论后原因。", encoding="utf-8")
    new.write_text("新版本自然表达，先输入后输出。", encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest("replace-backup", [old], source="self", owner="user", license="private", consent=True)
    real_rmtree = module.shutil.rmtree

    def fail_backup(path, *args, **kwargs):
        if ".backup-" in Path(path).name:
            raise PermissionError("backup locked")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "rmtree", fail_backup)
    with pytest.raises(PermissionError, match="backup locked"):
        store.ingest(
            "replace-backup", [new], source="self", owner="user", license="private",
            consent=True, replace=True,
        )
    assert list(store.root.glob(".replace-backup.backup-*"))

    monkeypatch.setattr(module.shutil, "rmtree", real_rmtree)
    assert store.delete("replace-backup")
    assert not list(store.root.glob(".replace-backup.backup-*"))


def test_style_reset_restores_memory_when_save_fails(tmp_path, monkeypatch):
    from veranima.core.learning import StyleLearner, UserStyleProfile

    learner = StyleLearner(persist_path=str(tmp_path / "style.json"))
    learner.activate_corpus("active", UserStyleProfile(
        sample_count=20, confidence=1.0, source_id="active", avg_message_chars=80,
    ))
    before = learner.snapshot()
    monkeypatch.setattr(learner, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        learner.reset()
    assert learner.snapshot() == before


def test_activation_failure_restores_every_changed_manifest(tmp_path, monkeypatch):
    import veranima.core.style_corpus as module
    from veranima.core.learning import StyleLearner, UserStyleProfile

    source = tmp_path / "beta.txt"
    source.write_text("\n\n".join(f"自然表达样本{i}，先结论后原因。" for i in range(24)), encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest("beta", [source], source="self", owner="user", license="private", consent=True)
    queue = store.export_review("beta", limit=8)
    for row in queue:
        row["decision"] = "accept"
    store.review_path("beta").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in queue) + "\n", encoding="utf-8",
    )
    store.apply_reviews("beta")
    alpha = store._dir("alpha")
    alpha.mkdir()
    (alpha / "manifest.json").write_text(json.dumps({
        "corpus_id": "alpha", "version": 1, "status": "active",
    }), encoding="utf-8")
    learner = StyleLearner(persist_path=str(tmp_path / "style.json"))
    learner.activate_corpus("alpha", UserStyleProfile(
        sample_count=20, confidence=1.0, source_id="alpha", avg_message_chars=80,
    ))
    real_atomic_json = module._atomic_json

    def fail_beta(path, data):
        if Path(path) == store._dir("beta") / "manifest.json" and data.get("status") == "active":
            raise OSError("beta manifest failed")
        return real_atomic_json(path, data)

    monkeypatch.setattr(module, "_atomic_json", fail_beta)
    with pytest.raises(OSError, match="beta manifest failed"):
        store.activate("beta", learner)

    assert learner.active_corpus_id == "alpha"
    assert store.manifest("alpha")["status"] == "active"
    assert store.manifest("beta")["status"] == "preview"


def test_activate_rejects_reviews_tampered_after_apply(tmp_path):
    from veranima.core.learning import StyleLearner

    source = tmp_path / "samples.txt"
    source.write_text("\n\n".join(f"自然文本{i}，先结论后说明原因。" for i in range(24)), encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest("review-integrity", [source], source="self", owner="user", license="private-local-consent", consent=True)
    queue = store.export_review("review-integrity", limit=8)
    for row in queue:
        row["decision"] = "accept"
    store.review_path("review-integrity").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in queue) + "\n", encoding="utf-8",
    )
    store.apply_reviews("review-integrity")
    reviews_path = store._dir("review-integrity") / "reviews.jsonl"
    reviews = [json.loads(line) for line in reviews_path.read_text(encoding="utf-8").splitlines()]
    reviews[0]["decision"] = "reject"
    reviews_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in reviews) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="apply 后被修改"):
        store.activate("review-integrity", StyleLearner())
    store.apply_reviews("review-integrity")
    reviews = [json.loads(line) for line in reviews_path.read_text(encoding="utf-8").splitlines()]
    reviews_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in reviews[1:]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="决策集合"):
        store.activate("review-integrity", StyleLearner())


def test_activate_rejects_duplicate_segment_rows(tmp_path):
    from veranima.core.learning import StyleLearner

    source = tmp_path / "samples.txt"
    source.write_text("\n\n".join(f"自然文本{i}，先结论后说明原因。" for i in range(24)), encoding="utf-8")
    store = StyleCorpusStore(tmp_path / "corpora")
    store.ingest("segment-integrity", [source], source="self", owner="user", license="private-local-consent", consent=True)
    segments_path = store._dir("segment-integrity") / "segments.jsonl"
    first = segments_path.read_text(encoding="utf-8").splitlines()[0]
    with segments_path.open("a", encoding="utf-8") as stream:
        stream.write(first + "\n")
    with pytest.raises(ValueError, match="唯一性"):
        store.activate("segment-integrity", StyleLearner())
