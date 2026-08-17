"""8.6.3 表情包记忆库：没见过 → 入库标注；回复时按情绪宽松匹配发送。

数据：data/stickers/{index.json, <hash>.png}
- dHash 感知哈希判重（Pillow 自实现，不依赖 imagehash）：汉明距离 ≤ 5 视为见过
- LLM 标注（含义/情绪/适用情景），JSON 格式化输出
- 发送匹配：情绪标签相交 → 候选；低使用次数优先（防固定几张反复用）
"""

from __future__ import annotations

import io
import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

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

    def to_dict(self) -> dict:
        return {
            "hash": self.hash, "file": self.file,
            "meaning": self.meaning, "moods": self.moods,
            "scenarios": self.scenarios, "uses": self.uses,
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
            self._entries = [StickerEntry(**e) for e in data.get("entries", [])]
        except Exception as e:
            logger.error("sticker index load failed: %s", e)

    def _save(self) -> None:
        self._index_path.write_text(
            json.dumps({"entries": [e.to_dict() for e in self._entries]},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

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
        h = dhash(data)
        try:
            ext = ".png"
            try:
                ext = "." + Image.open(io.BytesIO(data)).format.lower()
            except Exception:
                pass
            fname = f"{h[:12]}{ext}"
            (self.root / fname).write_bytes(data)
        except Exception as e:
            logger.error("sticker save failed: %s", e)
            return None
        entry = StickerEntry(
            hash=h, file=fname, meaning=meaning,
            moods=moods or [], scenarios=scenarios or [],
        )
        self._entries.append(entry)
        self._save()
        logger.info("sticker added: %s (%s)", fname, meaning[:30])
        return entry

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

    def record_use(self, entry: StickerEntry) -> None:
        entry.uses += 1
        self._save()

    def __len__(self) -> int:
        return len(self._entries)
