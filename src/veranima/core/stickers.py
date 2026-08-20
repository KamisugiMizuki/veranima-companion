"""8.6.3 表情包记忆库：没见过 → 入库标注；回复时按情绪宽松匹配发送。

数据：data/stickers/{index.json, <hash>.png}
- dHash 感知哈希判重（Pillow 自实现，不依赖 imagehash）：汉明距离 ≤ 5 视为见过
- LLM 标注（含义/情绪/适用情景），JSON 格式化输出
- 发送匹配：情绪标签相交 → 候选；低使用次数优先（防固定几张反复用）
"""

from __future__ import annotations

import io
import hashlib
import json
import logging
import random
import os
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .image_payload import ImagePayload, ImagePayloadError, make_image_payload

logger = logging.getLogger(__name__)

DEDUP_HAMMING = 5      # dHash 汉明距离阈值（≤ 视为同一张图）
DEFAULT_SIZE = 8       # dHash 缩略图尺寸（8x9 灰度 → 64 bit）


def dhash(data: bytes, size: int = DEFAULT_SIZE) -> str:
    """感知哈希：转灰度 → resize(size, size+1) → 逐行相邻像素比较 → hex。

    同一张图不同尺寸/压缩率下 dHash 近似，md5 会变而 dHash 能识别。
    """
    img = Image.open(io.BytesIO(data)).convert("L")
    img = img.resize((size, size + 1), Image.LANCZOS)
    bits = []
    for y in range(size + 1):
        row = [img.getpixel((x, y)) for x in range(size)]
        bits.extend(1 if row[x] > row[x + 1] else 0 for x in range(size - 1))
    return "".join(str(b) for b in bits)


def hamming(a: str, b: str) -> int:
    return sum(1 for x, y in zip(a, b) if x != y)


@dataclass
class StickerEntry:
    """库中一张表情包及其标注。"""

    hash: str
    file: str          # 相对 data/stickers/ 的文件名
    meaning: str = ""  # 含义
    moods: list[str] = field(default_factory=list)   # 情绪标签
    scenarios: list[str] = field(default_factory=list)  # 适用情景
    uses: int = 0      # 使用次数
    content_type: str = "image/png"
    animated: bool = False
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "hash": self.hash, "file": self.file,
            "meaning": self.meaning, "moods": self.moods,
            "scenarios": self.scenarios, "uses": self.uses,
            "content_type": self.content_type, "animated": self.animated,
            "created_at": self.created_at,
        }


class StickerLibrary:
    """表情包库：判重入库 + 标注 + 情绪匹配。"""

    def __init__(self, root: str | Path = "data/stickers"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._entries: list[StickerEntry] = []
        self._load()

    # ---------- 持久化 ----------

    def _load(self) -> None:
        if not self._index_path.exists():
            return
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            entries = []
            for raw in data.get("entries", []):
                entry = StickerEntry(**raw)
                # index.json is local but still untrusted after manual edits.
                target = (self.root / entry.file).resolve()
                if target.parent != self.root.resolve() or not target.is_file():
                    logger.warning("skip unsafe/missing sticker entry: %s", entry.file)
                    continue
                entries.append(entry)
            self._entries = entries
        except Exception as e:
            logger.error("sticker index load failed: %s", e)

    def _save(self) -> None:
        payload = json.dumps({"entries": [e.to_dict() for e in self._entries]},
                             ensure_ascii=False, indent=1)
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

    def find_similar(self, data: bytes) -> StickerEntry | None:
        """按 dHash 查重：返回最相似条目（汉明距离 ≤ DEDUP_HAMMING）或 None。"""
        h = dhash(data)
        best, best_d = None, DEDUP_HAMMING
        for e in self._entries:
            d = hamming(h, e.hash)
            if d <= best_d:
                best, best_d = e, d
        return best

    def add(self, data: bytes, meaning: str = "", moods: list[str] | None = None,
            scenarios: list[str] | None = None) -> StickerEntry | None:
        """入库一张表情包（调用方保证已判重）。返回条目；写入失败返回 None。"""
        try:
            payload = make_image_payload(data, source="sticker")
            return self.add_payload(payload, meaning=meaning, moods=moods, scenarios=scenarios)
        except Exception as e:
            logger.error("sticker validation failed: %s", e)
            return None

    def add_payload(self, payload: ImagePayload, meaning: str = "",
                    moods: list[str] | None = None,
                    scenarios: list[str] | None = None) -> StickerEntry | None:
        """保存经过统一校验的静态图片；动图永不进入表情包库。"""
        if payload.animated:
            return None
        h = dhash(payload.raw)
        if self.find_similar(payload.raw):
            return self.find_similar(payload.raw)
        try:
            ext = {
                "image/png": ".png", "image/jpeg": ".jpg",
                "image/gif": ".gif", "image/webp": ".webp",
            }[payload.content_type]
            fname = f"{hashlib.sha256(payload.raw).hexdigest()}{ext}"
            target = self.root / fname
            with target.open("xb") as stream:
                stream.write(payload.raw)
        except Exception as e:
            logger.error("sticker save failed: %s", e)
            return None
        entry = StickerEntry(
            hash=h, file=fname, meaning=meaning,
            moods=moods or [], scenarios=scenarios or [],
            content_type=payload.content_type,
            animated=False,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self._entries.append(entry)
        self._save()
        logger.info("sticker added: %s (%s)", fname, meaning[:30])
        return entry

    def delete(self, entry_or_hash: StickerEntry | str) -> bool:
        """删除图片和索引；路径只允许库根目录的文件。"""
        key = entry_or_hash.hash if isinstance(entry_or_hash, StickerEntry) else str(entry_or_hash)
        entry = next((e for e in self._entries if e.hash == key or e.file == key), None)
        if entry is None:
            return False
        target = (self.root / entry.file).resolve()
        if target.parent != self.root.resolve():
            return False
        try:
            target.unlink(missing_ok=True)
            self._entries.remove(entry)
            self._save()
            return True
        except OSError as exc:
            logger.error("sticker delete failed: %s", exc)
            return False

    def path_for(self, entry: StickerEntry) -> Path:
        """Return a validated absolute path for OneBot sending."""
        target = (self.root / entry.file).resolve()
        if target.parent != self.root.resolve() or not target.is_file():
            raise FileNotFoundError(entry.file)
        return target

    # ---------- 发送匹配 ----------

    def find_for_mood(self, mood: str, limit: int = 3) -> list[StickerEntry]:
        """按情绪宽松匹配：标签相交即候选；低使用次数优先（防固定几张反复用）。"""
        if not mood or not self._entries:
            return []
        mood = mood.lower()
        cands = [e for e in self._entries if any(mood in m.lower() for m in e.moods)]
        if not cands:
            return []
        cands.sort(key=lambda e: (e.uses, random.random()))
        return cands[:limit]

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

    def record_use(self, entry: StickerEntry) -> None:
        entry.uses += 1
        self._save()

    def __len__(self) -> int:
        return len(self._entries)
