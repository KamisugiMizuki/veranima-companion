"""Style corpus ingestion, review and activation.

Raw source files are never copied into the runtime store.  Only redacted
segments, aggregate features and provenance hashes are persisted.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

CORPUS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_CORPUS_BYTES = 50 * 1024 * 1024
MAX_SOURCE_FILES = 100
MAX_REVIEW_BYTES = 5 * 1024 * 1024
MAX_SEGMENT_CHARS = 400
MAX_SEGMENTS = 20_000
MAX_PERSISTED_SEGMENT_BYTES = 50 * 1024 * 1024
_ALLOWED_SUFFIXES = {".txt", ".md", ".jsonl"}
_ALLOWED_LICENSES = {
    "private", "private-local-consent", "self-owned", "user-owned", "project-original",
    "cc0-1.0", "cc-by-4.0", "cc-by-sa-4.0", "mit", "apache-2.0",
    "bsd-2-clause", "bsd-3-clause",
}

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|password|passwd|secret)"
    r"\s*[:=]\s*(?:['\"][^'\"\r\n]{1,500}['\"]|[^，。；;\r\n]{1,500})"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
_KEY_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_-]{12,}|github_pat_[A-Za-z0-9_]{12,}|"
    r"xox[baprs]-[A-Za-z0-9-]{12,}|AIza[A-Za-z0-9_-]{20,})\b"
)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_SPACED_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[ -]?)?(?:1[3-9][ -]?\d[ -]?\d[ -]?\d[ -]?\d[ -]?\d[ -]?\d[ -]?\d[ -]?\d[ -]?\d)(?!\d)")
_ID_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
_BANK_RE = re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?![ -]?\d)")
_ADDRESS_RE = re.compile(r"(?:(我住在|我家在|住址|地址)\s*[:：是]?\s*)([^，。；;\n]{3,80})")
_NAME_RE = re.compile(r"(?:(我叫|我的名字是?)\s*)([\u4e00-\u9fffA-Za-z·]{2,30})")
_SENSITIVE_HINT_RE = re.compile(r"(?:身份证|银行卡|支付密码|私钥|家庭住址|住址|邮箱|电话)", re.I)
_IDENTITY_FACT_RE = re.compile(
    r"(?:我叫|我的名字|我住在|我家在|我在.{0,30}(?:工作|就职|任教)|我的工作|我的学校|我的公司|"
    r"姓名\s*[:：]|手机号|手机号码|我是.{0,20}(?:人|学生|工程师|老师|医生|任教|工作|就职|上学|读书))"
)
_EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]")
_JP_RE = re.compile(r"[\u3040-\u30ff]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_REVIEW_IMMUTABLE_KEYS = (
    "segment_id", "corpus_version", "scene", "text", "weak_labels",
    "risk_flags", "bucket", "selection_reason",
)


def _review_digest(row: dict) -> str:
    canonical = {key: row.get(key) for key in _REVIEW_IMMUTABLE_KEYS}
    return _sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _decision_digest(row: dict) -> str:
    canonical = {
        key: row.get(key)
        for key in ("segment_id", "corpus_version", "decision", "corrected_labels", "annotator", "reason")
    }
    return _sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _segment_is_canonical(corpus_id: str, version: int, row: dict) -> bool:
    text = str(row.get("text") or "")
    source_index = int(row.get("source_index", -1))
    redacted, flags = _redact(text)
    if redacted != text or flags or _SENSITIVE_HINT_RE.search(text) or _IDENTITY_FACT_RE.search(text):
        return False
    labels = _weak_labels(text)
    content_type = _content_type(text)
    risk_flags = ["non_natural_content"] if content_type == "list" else []
    expected = {
        "segment_id": _sha256(f"{corpus_id}:{version}:{source_index}:{text}".encode("utf-8"))[:20],
        "corpus_version": version,
        "source_index": source_index,
        "scene": f"source-{source_index + 1}",
        "language": _language(text),
        "content_type": content_type,
        "weak_labels": labels,
        "risk_flags": risk_flags,
        "bucket": _bucket(labels, risk_flags),
        "review_required": bool(risk_flags),
    }
    return all(row.get(key) == value for key, value in expected.items())


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _atomic_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _redact(text: str) -> tuple[str, list[str]]:
    flags: list[str] = []

    def sub(pattern: re.Pattern, value, flag: str) -> str:
        nonlocal flags
        new = pattern.sub(value, text_holder[0])
        if new != text_holder[0]:
            flags.append(flag)
            text_holder[0] = new
        return new

    text_holder = [text]
    sub(_EMAIL_RE, "[REDACTED]", "pii_redacted")
    sub(_SECRET_RE, lambda m: f"{m.group(1)}=[REDACTED]", "secret_redacted")
    sub(_BEARER_RE, "Bearer [REDACTED]", "secret_redacted")
    sub(_KEY_TOKEN_RE, "[REDACTED]", "secret_redacted")
    sub(_PHONE_RE, "[REDACTED]", "pii_redacted")
    sub(_SPACED_PHONE_RE, "[REDACTED]", "pii_redacted")
    sub(_ID_RE, "[REDACTED]", "pii_redacted")
    sub(_BANK_RE, "[REDACTED]", "pii_redacted")
    sub(_ADDRESS_RE, lambda m: f"{m.group(1)}[REDACTED]", "pii_redacted")
    sub(_NAME_RE, lambda m: f"{m.group(1)}[REDACTED]", "pii_redacted")
    return text_holder[0], sorted(set(flags))


def _source_blocks(path: Path) -> list[str]:
    if path.suffix.lower() not in _ALLOWED_SUFFIXES:
        raise ValueError(f"不支持的语料文件类型: {path.suffix}")
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError(f"语料文件超过 {MAX_SOURCE_BYTES // 1024 // 1024}MB: {path.name}")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() != ".jsonl":
        return [text]
    blocks = []
    for line in text.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict) and isinstance(value.get("text"), str):
            blocks.append(value["text"])
        elif isinstance(value, str):
            blocks.append(value)
    return blocks


def _split_text(text: str) -> tuple[list[str], int]:
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    code_count = 0
    in_code = False
    non_code_lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if not in_code:
                code_count += 1
            in_code = not in_code
            continue
        if not in_code:
            non_code_lines.append(line)
    text = "\n".join(non_code_lines)
    quote_count = 0
    kept_lines = []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            quote_count += 1
            continue
        kept_lines.append(line.rstrip())
    text = "\n".join(kept_lines)
    out: list[str] = []
    for paragraph in re.split(r"\n{2,}", text):
        paragraph = re.sub(r"[ \t]+", " ", paragraph).strip()
        if not paragraph:
            continue
        pieces = [
            x.strip() for x in re.split(
                r"(?<=[。！？!?；;])\s*|(?<=\.)\s+(?=[A-Z0-9\"'])",
                paragraph,
            ) if x.strip()
        ]
        current = ""
        for piece in pieces:
            separator = " " if current and current[-1].isascii() and piece[0].isascii() else ""
            candidate = current + separator + piece
            if len(piece) > MAX_SEGMENT_CHARS:
                if current:
                    out.append(current)
                    current = ""
                out.extend(piece[i:i + MAX_SEGMENT_CHARS] for i in range(0, len(piece), MAX_SEGMENT_CHARS))
            elif current and len(candidate) > MAX_SEGMENT_CHARS:
                out.append(current)
                current = piece
            else:
                current = candidate
        if current:
            out.append(current)
    return out, code_count + quote_count


def _weak_labels(text: str) -> dict:
    length = max(1, len(text))
    sentences = [s for s in re.split(r"[。！？!?；;\n]|(?<!\d)\.(?!\d)", text) if s.strip()]
    avg_sentence = sum(map(len, sentences)) / max(1, len(sentences))
    short_ratio = sum(len(s) <= 12 for s in sentences) / max(1, len(sentences))
    polite = sum(text.count(w) for w in ("请", "麻烦", "谢谢", "您", "是否", "能否"))
    direct = sum(text.count(w) for w in ("直接", "先", "必须", "不要", "帮我", "给我", "尽快"))
    return {
        "chars": len(text),
        "avg_sentence_chars": round(avg_sentence, 3),
        "short_sentence_ratio": round(short_ratio, 4),
        "newline_ratio": round(text.count("\n") / length, 4),
        "question_ratio": round((text.count("?") + text.count("？")) / length, 4),
        "exclamation_ratio": round((text.count("!") + text.count("！")) / length, 4),
        "ellipsis_ratio": round((text.count("…") + text.count("...")) / length, 4),
        "parenthetical_ratio": round((text.count("（") + text.count("(")) / length, 4),
        "emoji_ratio": round(len(_EMOJI_RE.findall(text)) / length, 4),
        "ascii_ratio": round(sum(ch.isascii() and ch.isalnum() for ch in text) / length, 4),
        "japanese_ratio": round(len(_JP_RE.findall(text)) / length, 4),
        "formality": round(min(1.0, polite * 8 / length), 4),
        "directness": round(min(1.0, direct * 6 / length + short_ratio * 0.4), 4),
        "detail_preference": 1.0 if len(text) > 80 else (0.5 if len(text) >= 30 else 0.0),
    }


def _language(text: str) -> str:
    clean = text.replace("[REDACTED]", "")
    han = sum("\u4e00" <= ch <= "\u9fff" for ch in clean)
    kana = len(_JP_RE.findall(clean))
    latin = sum(ch.isascii() and ch.isalpha() for ch in clean)
    if kana:
        return "mixed" if latin > max(4, kana + han) else "ja"
    if han and latin > max(4, han * 0.35):
        return "mixed"
    if han:
        return "zh"
    if latin:
        return "en"
    return "other"


def _content_type(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    list_lines = sum(bool(re.match(r"^(?:[-*+]\s|\d+[.)、]\s*)", line)) for line in lines)
    if lines and list_lines >= max(1, len(lines) // 2):
        return "list"
    if re.match(r"^(?:用户|助手|A|B|Q|问|答)\s*[:：]", text, re.I):
        return "dialogue"
    return "natural"


def _bucket(labels: dict, risk_flags: list[str]) -> str:
    chars = int(labels["chars"])
    length = "short" if chars < 30 else "long" if chars > 120 else "medium"
    mixing = "mixed" if labels["ascii_ratio"] > 0.12 or labels["japanese_ratio"] > 0.05 else "plain"
    risk = "risk" if risk_flags else "clean"
    return f"{length}:{mixing}:{risk}"


def _feature_vector(row: dict) -> tuple[float, ...]:
    labels = row["weak_labels"]
    return (
        min(1.0, float(labels["chars"]) / 160),
        min(1.0, float(labels["avg_sentence_chars"]) / 80),
        float(labels["short_sentence_ratio"]),
        min(1.0, float(labels["question_ratio"]) * 20),
        min(1.0, float(labels["parenthetical_ratio"]) * 20),
        float(labels["formality"]),
        float(labels["directness"]),
        float(labels["detail_preference"]),
    )


def _distance(left: dict, right: dict) -> float:
    a, b = _feature_vector(left), _feature_vector(right)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


class StyleCorpusStore:
    """Local, deletable corpus store.  It never writes to MemoryStore."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, corpus_id: str) -> Path:
        if not CORPUS_ID_RE.fullmatch(corpus_id or ""):
            raise ValueError("corpus_id 仅允许字母、数字、下划线和连字符，最长 64 字符")
        return self.root / corpus_id

    def _cleanup_backups(self, corpus_id: str, *, restore_if_missing: bool = False) -> None:
        paths = list(self.root.glob(f".{corpus_id}.backup-*"))
        target = self._dir(corpus_id)
        if restore_if_missing and not target.exists() and paths:
            recovered = max(paths, key=lambda path: path.stat().st_mtime_ns)
            os.replace(recovered, target)
            paths.remove(recovered)
        for path in paths:
            shutil.rmtree(path)

    def ingest(
        self,
        corpus_id: str,
        files: Iterable[str | Path],
        *,
        source: str,
        owner: str,
        license: str,
        consent: bool,
        delete_scope: str = "corpus",
        retention_until: str = "",
        replace: bool = False,
    ) -> dict:
        target = self._dir(corpus_id)
        if (self.root / f".{corpus_id}.deleting").exists() or (self.root / f".{corpus_id}.deleting.audit.json").exists():
            raise RuntimeError(f"语料集仍在删除中，请先重试 delete: {corpus_id}")
        self._cleanup_backups(corpus_id, restore_if_missing=True)
        if not consent:
            raise ValueError("必须明确授权本地风格分析")
        source_value = source.strip()
        owner_value = owner.strip()
        license_value = license.strip().lower()
        ambiguous = {"?", "n/a", "na", "none", "unknown", "unlicensed", "tbd", "unspecified"}
        if (
            not source_value or not owner_value
            or source_value.lower() in ambiguous or owner_value.lower() in ambiguous
            or license_value not in _ALLOWED_LICENSES
        ):
            raise ValueError("source/owner/license 必须明确，license 使用受支持的授权标识")
        if delete_scope != "corpus":
            raise ValueError("当前只支持 corpus 级删除")
        if retention_until:
            try:
                datetime.fromisoformat(retention_until.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("retention_until 必须是 ISO-8601 日期或时间") from exc
        source_paths = [Path(item).resolve() for item in files]
        if not source_paths:
            raise ValueError("至少提供一个语料文件")
        if len(source_paths) > MAX_SOURCE_FILES:
            raise ValueError(f"单个语料集最多 {MAX_SOURCE_FILES} 个文件")
        total_source_bytes = 0
        for path in source_paths:
            if path.suffix.lower() not in _ALLOWED_SUFFIXES:
                raise ValueError(f"不支持的语料文件类型: {path.suffix}")
            if not path.is_file():
                raise FileNotFoundError(path)
            size = path.stat().st_size
            if size > MAX_SOURCE_BYTES:
                raise ValueError(f"语料文件超过 {MAX_SOURCE_BYTES // 1024 // 1024}MB: {path.name}")
            total_source_bytes += size
        if total_source_bytes > MAX_CORPUS_BYTES:
            raise ValueError(f"语料集总大小超过 {MAX_CORPUS_BYTES // 1024 // 1024}MB")
        old_manifest = self.manifest(corpus_id) if target.exists() else {}
        if old_manifest and not replace:
            raise FileExistsError(f"语料集已存在: {corpus_id}；显式 replace 才能创建新版本")
        if old_manifest.get("status") == "active" and replace:
            raise ValueError("语料集正在启用；请先 delete，再导入新版本，避免运行时双真值")
        deleted_versions = [
            int(row.get("version") or 0)
            for row in _read_jsonl(self.root / "deletions.jsonl")
            if row.get("corpus_id") == corpus_id
        ]
        version = max([int(old_manifest.get("version", 0)), *deleted_versions]) + 1
        rows: list[dict] = []
        persisted_segment_bytes = 0
        seen: set[str] = set()
        duplicate_count = 0
        excluded_count = 0
        sources = []
        for source_index, path in enumerate(source_paths):
            blocks = _source_blocks(path)
            raw = path.read_bytes()  # 已完成单文件与总量上限检查
            sources.append({
                "source_index": source_index,
                "sha256": _sha256(raw),
                "bytes": len(raw),
            })
            for block in blocks:
                segments, excluded = _split_text(block)
                excluded_count += excluded
                for segment in segments:
                    segment, flags = _redact(segment.strip())
                    if flags or _SENSITIVE_HINT_RE.search(segment) or _IDENTITY_FACT_RE.search(segment):
                        excluded_count += 1
                        continue
                    if len(segment) < 4 or not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in segment):
                        excluded_count += 1
                        continue
                    fingerprint = _sha256(re.sub(r"[\W_]+", "", segment).lower().encode("utf-8"))
                    if fingerprint in seen:
                        duplicate_count += 1
                        continue
                    seen.add(fingerprint)
                    labels = _weak_labels(segment)
                    content_type = _content_type(segment)
                    if content_type == "list":
                        flags = sorted(set(flags + ["non_natural_content"]))
                    segment_id = _sha256(f"{corpus_id}:{version}:{source_index}:{segment}".encode("utf-8"))[:20]
                    row = {
                        "segment_id": segment_id,
                        "corpus_version": version,
                        "source_index": source_index,
                        "scene": f"source-{source_index + 1}",
                        "text": segment,
                        "language": _language(segment),
                        "content_type": content_type,
                        "weak_labels": labels,
                        "risk_flags": flags,
                        "bucket": _bucket(labels, flags),
                        "review_required": bool(flags),
                    }
                    row_bytes = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
                    if len(rows) + 1 > MAX_SEGMENTS:
                        raise ValueError(f"清洗后片段数量超过 {MAX_SEGMENTS}")
                    if persisted_segment_bytes + row_bytes > MAX_PERSISTED_SEGMENT_BYTES:
                        raise ValueError("segments.jsonl 预计大小超过 50MB")
                    rows.append(row)
                    persisted_segment_bytes += row_bytes
        if not rows:
            raise ValueError("清洗后没有合格自然语言片段")
        manifest = {
            "schema_version": 1,
            "corpus_id": corpus_id,
            "version": version,
            "status": "preview",
            "source": source_value[:500],
            "owner": owner_value[:100],
            "license": license_value[:100],
            "authorization": {"consent_at": _now(), "local_only": True},
            "collected_at": _now(),
            "retention_until": retention_until,
            "delete_scope": delete_scope,
            "sources": sources,
            "stats": {
                "segment_count": len(rows),
                "duplicate_count": duplicate_count,
                "excluded_count": excluded_count,
                "reviewed_count": 0,
                "accepted_count": 0,
                "persisted_segment_bytes": persisted_segment_bytes,
            },
        }
        staging = self.root / f".{corpus_id}.staging-{uuid.uuid4().hex}"
        backup = self.root / f".{corpus_id}.backup-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            _atomic_jsonl(staging / "segments.jsonl", rows)
            _atomic_jsonl(staging / "reviews.jsonl", [])
            _atomic_json(staging / "manifest.json", manifest)
            if target.exists():
                os.replace(target, backup)
            try:
                os.replace(staging, target)
            except Exception:
                if backup.exists():
                    os.replace(backup, target)
                raise
            self._cleanup_backups(corpus_id)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise
        return manifest

    def manifest(self, corpus_id: str) -> dict:
        path = self._dir(corpus_id) / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def read_segments(self, corpus_id: str) -> list[dict]:
        return _read_jsonl(self._dir(corpus_id) / "segments.jsonl")

    def read_reviews(self, corpus_id: str) -> list[dict]:
        return _read_jsonl(self._dir(corpus_id) / "reviews.jsonl")

    def review_path(self, corpus_id: str) -> Path:
        return self._dir(corpus_id) / "review_queue.jsonl"

    def export_review(self, corpus_id: str, *, limit: int = 24) -> list[dict]:
        """Export a bounded risk-first, diverse review queue."""
        segments = self.read_segments(corpus_id)
        if not segments:
            raise ValueError("语料集没有可审核片段")
        limit = max(1, min(100, int(limit), len(segments)))
        selected: list[dict] = []
        reasons: dict[str, str] = {}
        remaining = list(segments)
        # 风险样本优先，但最多占 1/3，避免挤掉普通语料的代表覆盖。
        risk_limit = max(1, limit // 3)
        for row in sorted(remaining, key=lambda item: (-len(item["risk_flags"]), item["segment_id"])):
            if row["risk_flags"] and len(selected) < risk_limit:
                selected.append(row)
                reasons[row["segment_id"]] = "risk"
        chosen_ids = {row["segment_id"] for row in selected}
        remaining = [row for row in remaining if row["segment_id"] not in chosen_ids]

        # 靠近弱标签边界的样本用于校准规则，而不是要求人工标全量。
        uncertainty_limit = min(len(remaining), max(1, limit // 4))

        def uncertainty(row: dict) -> float:
            labels = row["weak_labels"]
            values = (labels["formality"], labels["directness"], labels["detail_preference"])
            return 1.0 - sum(abs(float(value) - 0.5) * 2 for value in values) / len(values)

        for row in sorted(remaining, key=lambda item: (-uncertainty(item), item["segment_id"]))[:uncertainty_limit]:
            selected.append(row)
            reasons[row["segment_id"]] = "uncertain"
        chosen_ids = {row["segment_id"] for row in selected}
        remaining = [row for row in remaining if row["segment_id"] not in chosen_ids]

        # ponytail: O(n*k)，k<=100（CLI 默认 24）；本地文件规模无需额外聚类服务。
        while remaining and len(selected) < limit:
            best = max(
                remaining,
                key=lambda row: (
                    min(_distance(row, picked) for picked in selected),
                    row["segment_id"],
                ),
            )
            selected.append(best)
            reasons[best["segment_id"]] = "representative"
            remaining.remove(best)
        manifest = self.manifest(corpus_id)
        queue = [{
            "segment_id": row["segment_id"],
            "corpus_version": manifest["version"],
            "scene": row["scene"],
            "text": row["text"],
            "weak_labels": row["weak_labels"],
            "risk_flags": row["risk_flags"],
            "bucket": row["bucket"],
            "selection_reason": reasons[row["segment_id"]],
            "decision": "pending",
            "corrected_labels": {},
            "annotator": "",
            "reason": "",
        } for row in selected[:limit]]
        previous_ids = set(manifest.get("review_segment_ids") or [])
        manifest["review_segment_ids"] = sorted(previous_ids | {row["segment_id"] for row in queue})
        digests = dict(manifest.get("review_segment_digests") or {})
        digests.update({row["segment_id"]: _review_digest(row) for row in queue})
        manifest["review_segment_digests"] = digests
        manifest["review_selection_version"] = manifest["version"]
        _atomic_json(self._dir(corpus_id) / "manifest.json", manifest)
        _atomic_jsonl(self.review_path(corpus_id), queue)
        return queue

    def apply_reviews(self, corpus_id: str) -> dict:
        manifest = self.manifest(corpus_id)
        segment_rows = self.read_segments(corpus_id)
        segments = {row["segment_id"]: row for row in segment_rows}
        expected_count = int(manifest.get("stats", {}).get("segment_count", -1))
        if len(segment_rows) != len(segments) or len(segment_rows) != expected_count:
            raise ValueError("segments.jsonl 数量或 segment_id 唯一性校验失败")
        if not manifest or not segments:
            raise FileNotFoundError(corpus_id)
        review_path = self.review_path(corpus_id)
        if not review_path.is_file():
            raise FileNotFoundError(review_path)
        if review_path.stat().st_size > MAX_REVIEW_BYTES:
            raise ValueError(f"复核文件超过 {MAX_REVIEW_BYTES // 1024 // 1024}MB")
        allowed_ids = set(manifest.get("review_segment_ids") or [])
        allowed_digests = dict(manifest.get("review_segment_digests") or {})
        if int(manifest.get("review_selection_version", 0)) != int(manifest["version"]):
            allowed_ids.clear()
        try:
            canonical_segments = all(
                _segment_is_canonical(corpus_id, int(manifest["version"]), row)
                for row in segments.values()
            )
        except (TypeError, ValueError):
            canonical_segments = False
        if not canonical_segments:
            raise ValueError("segments.jsonl 内容与派生字段不一致")
        current = {row["segment_id"]: row for row in self.read_reviews(corpus_id)}
        seen: set[str] = set()
        for row in _read_jsonl(review_path):
            segment_id = str(row.get("segment_id") or "")
            if segment_id in seen:
                raise ValueError(f"复核文件包含重复 segment_id: {segment_id or '<empty>'}")
            seen.add(segment_id)
            if segment_id not in segments:
                raise ValueError(f"复核文件包含未知 segment_id: {segment_id or '<empty>'}")
            if segment_id not in allowed_ids:
                raise ValueError(f"segment_id 不在受管复核集合（未导出）: {segment_id}")
            if _review_digest(row) != allowed_digests.get(segment_id):
                raise ValueError(f"受管复核内容被修改: {segment_id}")
            if int(row.get("corpus_version", 0)) != int(manifest["version"]):
                raise ValueError(f"审核文件版本不匹配: {segment_id}")
            decision = row.get("decision")
            if decision == "pending":
                continue
            if decision not in {"accept", "reject"}:
                raise ValueError(f"非法审核决定: {segment_id}")
            corrected = {}
            for key, value in (row.get("corrected_labels") or {}).items():
                if key not in {"formality", "directness", "detail_preference"}:
                    continue
                value = float(value)
                if not 0.0 <= value <= 1.0:
                    raise ValueError(f"修正标签超出 0-1: {key}")
                corrected[key] = value
            current[segment_id] = {
                "segment_id": segment_id,
                "corpus_version": manifest["version"],
                "decision": decision,
                "corrected_labels": corrected,
                "annotator": str(row.get("annotator") or "user")[:80],
                "reason": str(row.get("reason") or "")[:300],
                "reviewed_at": _now(),
            }
        saved = sorted(current.values(), key=lambda row: row["segment_id"])
        _atomic_jsonl(self._dir(corpus_id) / "reviews.jsonl", saved)
        manifest["applied_review_version"] = manifest["version"]
        manifest["applied_review_digests"] = {
            segment_id: _decision_digest(row) for segment_id, row in current.items()
        }
        accepted = sum(row["decision"] == "accept" for row in saved)
        rejected = sum(row["decision"] == "reject" for row in saved)
        manifest["stats"].update({
            "reviewed_count": len(saved),
            "accepted_count": accepted,
            "rejected_count": rejected,
        })
        _atomic_json(self._dir(corpus_id) / "manifest.json", manifest)
        return {"reviewed": len(saved), "accepted": accepted, "rejected": rejected}

    def activate(self, corpus_id: str, learner) -> object:
        """Pass the review gate and install an aggregate profile into StyleLearner."""
        from .learning import UserStyleProfile

        manifest = self.manifest(corpus_id)
        segments = self.read_segments(corpus_id)
        expected_count = int(manifest.get("stats", {}).get("segment_count", -1))
        segment_ids = [str(row.get("segment_id") or "") for row in segments]
        if len(segments) != len(set(segment_ids)) or len(segments) != expected_count:
            raise ValueError("segments.jsonl 数量或 segment_id 唯一性校验失败")
        try:
            canonical_segments = all(
                _segment_is_canonical(corpus_id, int(manifest.get("version", 0)), row)
                for row in segments
            )
        except (TypeError, ValueError):
            canonical_segments = False
        if not canonical_segments:
            raise ValueError("segments.jsonl 内容与派生字段不一致")
        segment_map = {row["segment_id"]: row for row in segments}
        raw_reviews = self.read_reviews(corpus_id)
        if int(manifest.get("applied_review_version", 0)) != int(manifest.get("version", 0)):
            raise ValueError("复核决策未通过当前版本 apply")
        decision_digests = dict(manifest.get("applied_review_digests") or {})
        raw_review_ids = [str(row.get("segment_id") or "") for row in raw_reviews]
        if len(raw_reviews) != len(set(raw_review_ids)):
            raise ValueError("复核决策包含重复 segment_id")
        if set(raw_review_ids) != set(decision_digests):
            raise ValueError("复核决策集合在 apply 后被修改")
        if any(_decision_digest(row) != decision_digests.get(str(row.get("segment_id") or "")) for row in raw_reviews):
            raise ValueError("复核决策内容在 apply 后被修改")
        allowed_ids = set(manifest.get("review_segment_ids") or [])
        if int(manifest.get("review_selection_version", 0)) != int(manifest.get("version", 0)):
            allowed_ids.clear()
        reviews: dict[str, dict] = {}
        invalid_reviews = 0
        for row in raw_reviews:
            segment_id = str(row.get("segment_id") or "")
            if (
                segment_id not in segment_map
                or segment_id not in allowed_ids
                or int(row.get("corpus_version", 0)) != int(manifest.get("version", 0))
                or row.get("decision") not in {"accept", "reject"}
            ):
                invalid_reviews += 1
                continue
            reviews[segment_id] = row
        if invalid_reviews:
            raise ValueError(f"有效复核记录校验失败：发现 {invalid_reviews} 条未知、陈旧或非法记录")
        if not manifest or len(segments) < UserStyleProfile.MIN_SAMPLES:
            raise ValueError(f"至少需要 {UserStyleProfile.MIN_SAMPLES} 条合格片段")
        retention = str(manifest.get("retention_until") or "").strip()
        if retention:
            expiry = datetime.fromisoformat(retention.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                raise ValueError("语料保留期已到，不能启用")
        required = min(12, max(4, math.ceil(math.sqrt(len(segments)))))
        if len(reviews) < required:
            raise ValueError(f"至少复核 {required} 条代表/冲突样本，当前 {len(reviews)} 条")
        accepted = sum(row["decision"] == "accept" for row in reviews.values())
        acceptance = accepted / max(1, len(reviews))
        if acceptance < 0.75:
            raise ValueError(f"复核接受率 {acceptance:.0%} 低于 75%，语料不能启用")

        by_bucket: dict[str, list[str]] = {}
        for segment_id, review in reviews.items():
            segment = segment_map.get(segment_id)
            if segment:
                by_bucket.setdefault(segment["bucket"], []).append(review["decision"])
        noisy_buckets = {
            bucket for bucket, decisions in by_bucket.items()
            if len(decisions) >= 2 and decisions.count("accept") / len(decisions) < 0.5
        }
        included = []
        for row in segments:
            decision = reviews.get(row["segment_id"], {}).get("decision")
            if decision == "reject":
                continue
            if row["risk_flags"] and decision != "accept":
                continue
            if row["bucket"] in noisy_buckets and decision != "accept":
                continue
            included.append(row)
        if len(included) < UserStyleProfile.MIN_SAMPLES:
            raise ValueError("复核过滤后合格片段不足，不能启用")

        profile = UserStyleProfile()
        profile.sample_count = len(included)
        profile.char_count = sum(int(row["weak_labels"]["chars"]) for row in included)
        profile.avg_message_chars = profile.char_count / len(included)
        profile.avg_sentence_chars = sum(
            float(row["weak_labels"]["avg_sentence_chars"]) for row in included
        ) / len(included)
        ratio_fields = (
            "question_ratio", "newline_ratio", "emoji_ratio", "exclamation_ratio",
            "ellipsis_ratio", "parenthetical_ratio", "ascii_ratio", "japanese_ratio",
        )
        for field in ratio_fields:
            weighted = sum(
                float(row["weak_labels"][field]) * int(row["weak_labels"]["chars"])
                for row in included
            ) / max(1, profile.char_count)
            setattr(profile, field, weighted)
        for field in ("formality", "directness", "detail_preference"):
            setattr(
                profile,
                field,
                sum(float(row["weak_labels"][field]) for row in included) / len(included),
            )
        profile.updated_at = _now()
        for key in ("formality", "directness", "detail_preference"):
            deltas = []
            for segment_id, review in reviews.items():
                if review["decision"] != "accept" or key not in review.get("corrected_labels", {}):
                    continue
                weak = float(segment_map[segment_id]["weak_labels"][key])
                deltas.append(float(review["corrected_labels"][key]) - weak)
            if deltas:
                setattr(profile, key, max(0.0, min(1.0, getattr(profile, key) + sum(deltas) / len(deltas))))
        quality = acceptance * min(1.0, len(reviews) / required)
        profile.source_id = corpus_id
        profile.scene_count = len({row["scene"] for row in included})
        profile.reviewed_count = len(reviews)
        profile.quality_score = round(quality, 4)
        coverage = 0.3 + 0.5 * min(1.0, len(included) / 100) + 0.2 * min(1.0, profile.scene_count / 3)
        profile.confidence = min(1.0, quality * coverage)

        original_target_manifest = dict(manifest)
        manifest["status"] = "active"
        manifest["activated_at"] = _now()
        manifest["quality_gate"] = {
            "required_reviews": required,
            "reviewed": len(reviews),
            "acceptance": round(acceptance, 4),
            "included_segments": len(included),
            "excluded_noisy_buckets": sorted(noisy_buckets),
        }
        _atomic_json(self._dir(corpus_id) / "profile.json", profile.snapshot())
        updates = []
        target_manifest_path = self._dir(corpus_id) / "manifest.json"
        try:
            for other in self.root.iterdir():
                other_manifest = other / "manifest.json"
                if not other.is_dir() or other.name == corpus_id or not other_manifest.exists():
                    continue
                data = json.loads(other_manifest.read_text(encoding="utf-8"))
                if data.get("status") == "active":
                    original = dict(data)
                    data["status"] = "preview"
                    data["deactivated_at"] = _now()
                    updates.append((other_manifest, data, original))
            updates.append((target_manifest_path, manifest, original_target_manifest))
            written = []
            for path, updated, original in updates:
                _atomic_json(path, updated)
                written.append((path, original))
            learner.activate_corpus(corpus_id, profile)
        except Exception:
            for path, original in reversed(locals().get("written", [])):
                _atomic_json(path, original)
            raise
        return profile

    def delete(self, corpus_id: str, learner=None) -> bool:
        target = self._dir(corpus_id)
        self._cleanup_backups(corpus_id)
        tombstone = self.root / f".{corpus_id}.deleting"
        audit_sidecar = self.root / f".{corpus_id}.deleting.audit.json"
        started_with_target = target.exists()
        if target.exists() and tombstone.exists():
            raise RuntimeError(f"删除 tombstone 冲突: {corpus_id}")
        if audit_sidecar.exists():
            audit_row = json.loads(audit_sidecar.read_text(encoding="utf-8"))
        else:
            manifest_path = (target if target.exists() else tombstone) / "manifest.json"
            if not manifest_path.exists():
                return False
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            audit_row = {
                "corpus_id": corpus_id,
                "version": manifest.get("version"),
                "deleted_at": _now(),
                "delete_scope": manifest.get("delete_scope", "corpus"),
                "manifest_sha256": _sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")),
                "source_hashes": [row.get("sha256") for row in manifest.get("sources", [])],
            }
            _atomic_json(audit_sidecar, audit_row)
        previous = (learner.active_corpus_id, learner.corpus_profile) if learner is not None else ("", None)
        cleared = False
        renamed = tombstone.exists()
        original_files = {
            str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()
        } if target.exists() else set()
        try:
            if learner is not None:
                cleared = learner.clear_corpus(corpus_id)
            if target.exists():
                os.replace(target, tombstone)
                renamed = True
            if tombstone.exists():
                shutil.rmtree(tombstone)
            audit_path = self.root / "deletions.jsonl"
            rows = _read_jsonl(audit_path)
            if not any(
                row.get("corpus_id") == corpus_id and row.get("version") == audit_row.get("version")
                for row in rows
            ):
                _atomic_jsonl(audit_path, [*rows, audit_row])
            audit_sidecar.unlink(missing_ok=True)
        except Exception:
            if not renamed:
                audit_sidecar.unlink(missing_ok=True)
                previous_id, previous_profile = previous
                if cleared and previous_id:
                    learner.activate_corpus(previous_id, previous_profile)
            elif started_with_target and tombstone.exists():
                remaining_files = {
                    str(path.relative_to(tombstone)) for path in tombstone.rglob("*") if path.is_file()
                }
                if remaining_files == original_files:
                    os.replace(tombstone, target)
                    audit_sidecar.unlink(missing_ok=True)
                    previous_id, previous_profile = previous
                    if cleared and previous_id:
                        learner.activate_corpus(previous_id, previous_profile)
            # 已重命名后若删除部分失败，保持运行时指针撤销并保留 tombstone/sidecar 供重试。
            raise
        return True

    def deactivate(self, corpus_id: str, learner=None) -> bool:
        """Disable aggregate runtime style while retaining governed corpus data."""
        manifest = self.manifest(corpus_id)
        if not manifest:
            return False
        previous = (learner.active_corpus_id, learner.corpus_profile) if learner is not None else ("", None)
        cleared = learner.clear_corpus(corpus_id) if learner is not None else False
        try:
            if manifest.get("status") == "active":
                manifest["status"] = "preview"
                manifest["deactivated_at"] = _now()
                _atomic_json(self._dir(corpus_id) / "manifest.json", manifest)
        except Exception:
            previous_id, previous_profile = previous
            if cleared and previous_id:
                learner.activate_corpus(previous_id, previous_profile)
            raise
        return True

    def expire_active(self, learner) -> bool:
        """Delete every corpus whose declared retention period has expired."""
        learner.refresh_activation()
        deleted = False
        now = datetime.now(timezone.utc)
        for item in list(self.root.iterdir()):
            if not item.is_dir() or item.name.startswith(".") or not (item / "manifest.json").exists():
                continue
            retention = str(self.manifest(item.name).get("retention_until") or "").strip()
            if not retention:
                continue
            expiry = datetime.fromisoformat(retention.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= now:
                deleted = self.delete(
                    item.name, learner if item.name == learner.active_corpus_id else None,
                ) or deleted
        return deleted

    def status(self, corpus_id: str | None = None) -> list[dict]:
        ids = [corpus_id] if corpus_id else sorted(
            item.name for item in self.root.iterdir()
            if item.is_dir() and (item / "manifest.json").exists()
        )
        return [self.manifest(item) for item in ids if self.manifest(item)]
