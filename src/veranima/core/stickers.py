"""8.6.3 表情包记忆库：没见过 → 入库标注；回复时按情绪宽松匹配发送。

数据：data/stickers/{index.json, <hash>.png}
- dHash 感知哈希判重（Pillow 自实现，不依赖 imagehash）：汉明距离 ≤ 5 视为见过
- LLM 标注（含义/情绪/适用情景），JSON 格式化输出
- 发送匹配：情绪标签相交 → 候选；低使用次数优先（防固定几张反复用）
"""

from __future__ import annotations

import contextlib
import io
import hashlib
import json
import logging
import random
import os
import shutil
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .image_payload import ImagePayload, ImagePayloadError, make_image_payload

logger = logging.getLogger(__name__)

DEDUP_HAMMING = 5      # dHash 汉明距离阈值（≤ 视为同一张图）
DEFAULT_SIZE = 8       # dHash 缩略图尺寸（9x8 灰度 → 64 bit）
SCHEMA_VERSION = 2

_MOOD_TERMS = {
    "开心": ("开心", "高兴", "太好了", "哈哈", "庆祝"),
    "难过": ("难过", "伤心", "委屈", "低落"),
    "生气": ("生气", "火大", "恼火", "不满"),
    "无语": ("无语", "离谱", "说不出话", "沉默"),
    "惊讶": ("惊讶", "居然", "竟然", "真的假的"),
    "鼓励": ("鼓励", "加油", "相信你", "可以的", "support"),
    "调侃": ("调侃", "逗你", "开玩笑", "teasing"),
    "无奈": ("无奈", "没办法", "算了", "叹气"),
    "敷衍": ("敷衍", "嗯嗯", "行吧"),
    "卖萌": ("卖萌", "撒娇", "可爱", "playful"),
}
_SCENARIO_TERMS = {
    "agreement": ("答应", "同意", "没问题"),
    "praise": ("做得好", "真棒", "厉害"),
    "affection": ("喜欢你", "想你", "亲密"),
    "teasing": ("逗你", "开玩笑", "调侃"),
    "comfort": ("安慰", "别难过", "陪着你"),
    "failure": ("失败", "没成功", "搞砸"),
    "surprise": ("居然", "竟然", "没想到"),
    "refusal": ("不行", "拒绝", "不能答应"),
    "request_help": ("帮帮", "求助", "怎么办"),
    "fatigue": ("累", "困", "没精神"),
    "embarrassment": ("尴尬", "害羞", "不好意思"),
    "celebration": ("庆祝", "成功了", "太好了"),
}
_STICKER_SUPPRESS_TERMS = (
    "自杀", "伤害自己", "急救", "报警", "去医院", "审批", "任务失败", "系统错误",
)


def build_sticker_query(reply_obj, rendered_text: str, user_text: str) -> dict:
    """从回复构造表情查询，失败/任务/严肃语境一律不发。"""
    segments = getattr(reply_obj, "segments", ()) or ()
    tones = " ".join(str(getattr(segment, "tone", "") or "") for segment in segments)
    reply = str(rendered_text or "")
    user = str(user_text or "")
    haystack = f"{tones} {reply} {user}".lower()
    moods = {
        mood for mood, terms in _MOOD_TERMS.items()
        if any(term.lower() in haystack for term in terms)
    }
    scenario_tags = {
        tag for tag, terms in _SCENARIO_TERMS.items()
        if any(term.lower() in haystack for term in terms)
    }
    degraded = bool(getattr(reply_obj, "degraded", ""))
    reply_is_internal = any(token in haystack for token in (
        "任务", "审批", "系统错误", "服务不可用", "正在执行", "执行失败",
    ))
    suppress = degraded or reply_is_internal or any(term in haystack for term in _STICKER_SUPPRESS_TERMS)
    explicit_request = bool(
        "表情" in user and any(term in user for term in ("发", "来", "用一个", "来个"))
    )
    return {
        "moods": moods,
        "scenario_tags": scenario_tags,
        "text": reply,
        "explicit_request": explicit_request,
        "suppress": suppress,
    }


def _entry_id(content_sha256: str, owner_scope: str) -> str:
    """One physical image may have independent consent per owner."""
    return hashlib.sha256(f"{owner_scope}\0{content_sha256}".encode("utf-8")).hexdigest()


def dhash(data: bytes, size: int = DEFAULT_SIZE) -> str:
    """感知哈希：转灰度 → resize(size+1, size) → 逐行相邻像素比较。

    同一张图不同尺寸/压缩率下 dHash 近似，md5 会变而 dHash 能识别。
    """
    img = Image.open(io.BytesIO(data)).convert("L")
    img = img.resize((size + 1, size), Image.LANCZOS)
    bits = []
    for y in range(size):
        row = [img.getpixel((x, y)) for x in range(size + 1)]
        bits.extend(1 if row[x] > row[x + 1] else 0 for x in range(size))
    return "".join(str(b) for b in bits)


def hamming(a: str, b: str) -> int:
    if len(a) != len(b):
        raise ValueError("dHash values must have the same length")
    return sum(1 for x, y in zip(a, b) if x != y)


@dataclass
class StickerEntry:
    """库中一张表情包及其标注。"""

    id: str
    sha256: str
    dhash: str
    dhash_version: int
    file: str          # 相对 data/stickers/ 的文件名
    status: str = "active"
    owner_scope: str = "legacy_global"
    source: dict = field(default_factory=dict)
    consent: str = "explicit"
    meaning: str = ""  # 含义
    moods: list[str] = field(default_factory=list)   # 情绪标签
    scenario_tags: list[str] = field(default_factory=list)
    scenarios: list[str] = field(default_factory=list)  # 适用情景
    confidence: float = 0.0
    uses: int = 0      # 使用次数
    last_used_at: str | None = None
    content_type: str = "image/png"
    animated: bool = False
    created_at: str = ""
    approved_at: str | None = None

    @property
    def hash(self) -> str:
        """v1 compatibility alias; new code uses dhash."""
        return self.dhash

    def to_dict(self) -> dict:
        return {
            "id": self.id, "sha256": self.sha256,
            "dhash": self.dhash, "dhash_version": self.dhash_version,
            "file": self.file, "status": self.status,
            "owner_scope": self.owner_scope, "source": self.source,
            "consent": self.consent,
            "meaning": self.meaning, "moods": self.moods,
            "scenario_tags": self.scenario_tags, "scenarios": self.scenarios,
            "confidence": self.confidence, "uses": self.uses,
            "last_used_at": self.last_used_at,
            "content_type": self.content_type, "animated": self.animated,
            "created_at": self.created_at, "approved_at": self.approved_at,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "StickerEntry":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in raw.items() if key in allowed})


class StickerLibrary:
    """表情包库：判重入库 + 标注 + 情绪匹配。"""

    def __init__(
        self,
        root: str | Path = "data/stickers",
        *,
        legacy_owner_scope: str = "",
        pending_ttl_days: int = 7,
        max_items: int = 100,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._entries: list[StickerEntry] = []
        self._legacy_owner_scope = str(legacy_owner_scope or "")
        self.pending_ttl_days = max(1, int(pending_ttl_days))
        self.max_items = max(0, int(max_items))
        self._lock = threading.RLock()
        self._load()

    # ---------- 持久化 ----------

    def _load(self) -> None:
        if not self._index_path.exists():
            return
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            if int(data.get("schema_version") or 1) < SCHEMA_VERSION:
                data = self._migrate_v1(data)
            entries = []
            for raw in data.get("entries", []):
                entry = StickerEntry.from_dict(raw)
                # index.json is local but still untrusted after manual edits.
                target = (self.root / entry.file).resolve()
                if target.parent != self.root.resolve() or not target.is_file():
                    logger.warning("skip unsafe/missing sticker entry: %s", entry.file)
                    continue
                entries.append(entry)
            self._entries = entries
        except Exception as e:
            logger.error("sticker index load failed: %s", e)

    def _migrate_v1(self, data: dict) -> dict:
        backup = self.root / "index.v1.backup.json"
        if not backup.exists():
            shutil.copy2(self._index_path, backup)
        entries = []
        for raw in data.get("entries", []):
            name = str(raw.get("file") or "")
            target = (self.root / name).resolve()
            if target.parent != self.root.resolve() or not target.is_file():
                continue
            payload = make_image_payload(target.read_bytes(), source="sticker:migration")
            digest = hashlib.sha256(payload.raw).hexdigest()
            owner = self._legacy_owner_scope or "legacy_global"
            active = bool(self._legacy_owner_scope)
            entries.append(StickerEntry(
                id=_entry_id(digest, owner),
                sha256=digest,
                dhash=dhash(payload.raw),
                dhash_version=SCHEMA_VERSION,
                file=name,
                status="active" if active else "disabled",
                owner_scope=owner,
                source={"channel": "qq", "received_at": raw.get("created_at", "")},
                consent="legacy_auto",
                meaning=str(raw.get("meaning") or ""),
                moods=list(raw.get("moods") or []),
                scenarios=list(raw.get("scenarios") or []),
                uses=max(0, int(raw.get("uses") or 0)),
                content_type=payload.content_type,
                animated=False,
                created_at=str(raw.get("created_at") or ""),
            ).to_dict())
        migrated = {"schema_version": SCHEMA_VERSION, "entries": entries}
        self._write_payload(migrated)
        return migrated

    def _save(self) -> None:
        self._write_payload({
            "schema_version": SCHEMA_VERSION,
            "entries": [e.to_dict() for e in self._entries],
        })

    def _write_payload(self, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=1)
        fd, temp_name = tempfile.mkstemp(prefix="index-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self._index_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    # ---------- 判重与入库 ----------

    def find_similar(self, data: bytes, owner_scope: str | None = None) -> StickerEntry | None:
        """按 dHash 查重：返回最相似条目（汉明距离 ≤ DEDUP_HAMMING）或 None。"""
        h = dhash(data)
        best, best_d = None, DEDUP_HAMMING
        with self._lock:
            for e in self._entries:
                if owner_scope is not None and e.owner_scope != owner_scope:
                    continue
                d = hamming(h, e.dhash)
                if d <= best_d:
                    best, best_d = e, d
        return best

    def add(self, data: bytes, meaning: str = "", moods: list[str] | None = None,
            scenarios: list[str] | None = None, **metadata) -> StickerEntry | None:
        """入库一张表情包（调用方保证已判重）。返回条目；写入失败返回 None。"""
        try:
            payload = make_image_payload(data, source="sticker")
            return self.add_payload(
                payload, meaning=meaning, moods=moods, scenarios=scenarios, **metadata,
            )
        except Exception as e:
            logger.error("sticker validation failed: %s", e)
            return None

    def add_payload(self, payload: ImagePayload, meaning: str = "",
                    moods: list[str] | None = None,
                    scenarios: list[str] | None = None,
                    *,
                    status: str = "active",
                    owner_scope: str = "legacy_global",
                    source: dict | None = None,
                    consent: str = "explicit",
                    scenario_tags: list[str] | None = None,
                    confidence: float = 0.0) -> StickerEntry | None:
        """保存经过统一校验的静态图片；动图永不进入表情包库。"""
        if payload.animated:
            return None
        with self._lock:
            similar = self.find_similar(payload.raw, owner_scope=owner_scope)
            if similar:
                return similar
            if self.max_items and len(self._entries) >= self.max_items:
                logger.info("sticker library full: max_items=%d", self.max_items)
                return None
            h = dhash(payload.raw)
            digest = hashlib.sha256(payload.raw).hexdigest()
            ext = {
                "image/png": ".png", "image/jpeg": ".jpg",
                "image/gif": ".gif", "image/webp": ".webp",
            }[payload.content_type]
            fname = f"{digest}{ext}"
            target = self.root / fname
            created = False
            entry = None
            try:
                if not target.exists():
                    with target.open("xb") as stream:
                        stream.write(payload.raw)
                    created = True
                entry = StickerEntry(
                    id=_entry_id(digest, owner_scope),
                    sha256=digest,
                    dhash=h,
                    dhash_version=SCHEMA_VERSION,
                    file=fname,
                    status=status,
                    owner_scope=owner_scope,
                    source=dict(source or {}),
                    consent=consent,
                    meaning=meaning,
                    moods=moods or [],
                    scenario_tags=scenario_tags or [],
                    scenarios=scenarios or [],
                    confidence=max(0.0, min(1.0, float(confidence or 0.0))),
                    content_type=payload.content_type,
                    animated=False,
                    created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
                self._entries.append(entry)
                self._save()
            except Exception as e:
                if entry is not None and entry in self._entries:
                    self._entries.remove(entry)
                if created:
                    target.unlink(missing_ok=True)
                logger.error("sticker save failed: %s", e)
                return None
            logger.info("sticker added: %s (%s)", fname, meaning[:30])
            return entry

    def add_candidate(self, payload: ImagePayload, **metadata) -> StickerEntry | None:
        return self.add_payload(
            payload,
            status="pending",
            consent="review_pending",
            **metadata,
        )

    def list_entries(
        self,
        *,
        status: str | None = None,
        owner_scope: str | None = None,
    ) -> list[StickerEntry]:
        with self._lock:
            return [
                entry for entry in self._entries
                if (status is None or entry.status == status)
                and (owner_scope is None or entry.owner_scope == owner_scope)
            ]

    def approve(self, entry_id: str) -> StickerEntry | None:
        with self._lock:
            entry = next((item for item in self._entries if item.id == entry_id), None)
            if entry is None or entry.status != "pending":
                return None
            entry.status = "active"
            entry.consent = "review_approved"
            entry.approved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._save()
            return entry

    def set_enabled(self, entry_id: str, enabled: bool) -> bool:
        with self._lock:
            entry = next((item for item in self._entries if item.id == entry_id), None)
            if entry is None or entry.status not in {"active", "disabled"}:
                return False
            entry.status = "active" if enabled else "disabled"
            self._save()
            return True

    def delete(self, entry_or_hash: StickerEntry | str) -> bool:
        """删除图片和索引；路径只允许库根目录的文件。"""
        key = entry_or_hash.id if isinstance(entry_or_hash, StickerEntry) else str(entry_or_hash)
        with self._lock:
            entry = next((
                item for item in self._entries
                if item.id == key or item.dhash == key or item.file == key
            ), None)
            if entry is None:
                return False
            target = (self.root / entry.file).resolve()
            if target.parent != self.root.resolve():
                return False
            self._entries.remove(entry)
            try:
                self._save()
                if not any(item.file == entry.file for item in self._entries):
                    target.unlink(missing_ok=True)
                return True
            except OSError as exc:
                self._entries.append(entry)
                with contextlib.suppress(OSError):
                    self._save()
                logger.error("sticker delete failed: %s", exc)
                return False

    def reject(self, entry_id: str) -> bool:
        with self._lock:
            entry = next((item for item in self._entries if item.id == entry_id), None)
            if entry is None or entry.status != "pending":
                return False
        return self.delete(entry_id)

    def cleanup_pending(self, *, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = current - timedelta(days=self.pending_ttl_days)
        with self._lock:
            expired = []
            for entry in self._entries:
                if entry.status != "pending":
                    continue
                try:
                    created = datetime.fromisoformat(entry.created_at)
                except (TypeError, ValueError):
                    created = datetime.min.replace(tzinfo=timezone.utc)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created <= cutoff:
                    expired.append(entry.id)
        return [entry_id for entry_id in expired if self.delete(entry_id)]

    def path_for(self, entry: StickerEntry) -> Path:
        """Return a validated absolute path for OneBot sending."""
        target = (self.root / entry.file).resolve()
        if target.parent != self.root.resolve() or not target.is_file():
            raise FileNotFoundError(entry.file)
        return target

    # ---------- 发送匹配 ----------

    def find_for_query(
        self,
        query: dict,
        *,
        owner_scope: str | None,
        limit: int = 3,
        recent_ids: tuple[str, ...] | list[str] = (),
    ) -> list[StickerEntry]:
        moods = {str(value).strip() for value in query.get("moods", ()) if str(value).strip()}
        tags = {
            str(value).strip() for value in query.get("scenario_tags", ())
            if str(value).strip()
        }
        text = str(query.get("text") or "").lower()
        recent = set(recent_ids)
        scored: list[tuple[int, int, str, str, StickerEntry]] = []
        with self._lock:
            for entry in self._entries:
                if entry.status != "active":
                    continue
                if owner_scope is not None and entry.owner_scope != owner_scope:
                    continue
                try:
                    target = (self.root / entry.file).resolve()
                    if target.parent != self.root.resolve() or not target.is_file():
                        continue
                except OSError:
                    continue
                score = 4 * len(tags.intersection(entry.scenario_tags))
                score += 3 * len(moods.intersection(entry.moods))
                if text and any(
                    value and value.lower() in text
                    for value in (entry.meaning, *entry.scenarios)
                ):
                    score += 1
                if entry.uses == 0:
                    score += 1
                if entry.id in recent:
                    score -= 3
                if score < 3:
                    continue
                scored.append((
                    -score,
                    entry.uses,
                    entry.last_used_at or "",
                    entry.created_at,
                    entry,
                ))
        scored.sort(key=lambda item: item[:-1])
        return [item[-1] for item in scored[:max(0, int(limit))]]

    def find_for_mood(self, mood: str, limit: int = 3) -> list[StickerEntry]:
        """按情绪宽松匹配：标签相交即候选；低使用次数优先（防固定几张反复用）。"""
        if not mood:
            return []
        return self.find_for_query({"moods": {mood}}, owner_scope=None, limit=limit)

    def find_for_context(self, mood: str = "", scenario: str = "", limit: int = 3) -> list[StickerEntry]:
        """按情绪/情境加权；无情境时退化到 find_for_mood。"""
        if not scenario:
            return self.find_for_mood(mood, limit=limit)
        mood = mood.lower().strip()
        scenario = scenario.lower().strip()
        scored = []
        for entry in self._entries:
            mood_hit = any(mood and mood in x.lower() for x in entry.moods)
            scenario_hit = any(scenario in x.lower() or x.lower() in scenario for x in entry.scenarios)
            if not (mood_hit or scenario_hit):
                continue
            scored.append((int(scenario_hit) * 2 + int(mood_hit), -entry.uses, entry))
        scored.sort(key=lambda item: (-item[0], -item[1]))
        return [item[2] for item in scored[:limit]]

    def record_use(self, entry: StickerEntry, *, used_at: datetime | None = None) -> bool:
        with self._lock:
            current = next((item for item in self._entries if item.id == entry.id), None)
            if current is None or current.status != "active":
                return False
            current.uses += 1
            current.last_used_at = (used_at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
            self._save()
            return True

    def __len__(self) -> int:
        return len(self._entries)
