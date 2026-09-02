"""UserModel — 用户虚拟角色的单一真源（data/usermodel.json）。

一个 json 文档装下「角色视角中的用户」，方便人直接看、直接改：
- profile：13 键客观画像（judges 对话提取 + 设置页手填）。每键
  {value, source, pinned}；source 冲突链 user(亲口) > dialog(提取)，
  pinned=true 时非 user 来源不再覆写（手改锁定，防 nightly 整理冲掉）。
- portraits：按角色隔离的主观解读（role_id -> {text, updated_at}）。
  只由角色的夜间整理产出，用户端只读——「我眼中的你」不该被填。

表 user_profile 退役为本文件的迁移来源（旧库首次读入，幂等）。
ponytail: 全量内存 + 脏则整写（os.replace 原子）。文档就 17 格，
    单写者（核心进程）；多进程共写同一 json 不在设计范围。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PROFILE_KEYS = (
    "real_name", "nickname_pref", "gender", "age", "occupation",
    "city", "love_language", "comfort_style", "teasing_tolerance",
    "health_notes", "personality_traits", "current_goal", "pending_events",
)
# 冲突链：user 亲口 > dialog 对话提取。pinned 另立一道闸（见 set_profile）。
_SOURCE_RANK = {"user": 2, "dialog": 1}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UserModel:
    def __init__(self, path: str | os.PathLike):
        self.path = str(path)
        self._doc: dict = {"version": 1, "profile": {}, "portraits": {}}
        self._mtime: float | None = None
        self._dirty = False
        self._load()

    # ---------- 读写底座 ----------

    def _load(self) -> None:
        try:
            self._mtime = os.path.getmtime(self.path)
        except OSError:
            self._mtime = None
        if self._mtime is None:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict):
                prof = {k: v for k, v in (doc.get("profile") or {}).items()
                        if k in PROFILE_KEYS and isinstance(v, dict)}
                self._doc = {"version": 1, "profile": prof,
                             "portraits": doc.get("portraits") or {}}
        except Exception as e:  # 坏文件 = 空档起步（下次写整文件覆盖）
            logger.warning("usermodel load failed (%s): %s", self.path, e)

    def _ensure(self) -> dict:
        # 外部（adb push / 手改）动过文件 → 重读；本轮改动未落盘时保内存版
        try:
            m = os.path.getmtime(self.path)
        except OSError:
            m = None
        if not self._dirty and m != self._mtime:
            self._load()
        return self._doc

    def _save(self) -> None:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._doc, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self._dirty = False
            try:
                self._mtime = os.path.getmtime(self.path)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---------- profile（13 键客观画像） ----------

    def get_profile(self, key: str) -> dict | None:
        if key not in PROFILE_KEYS:
            return None
        v = self._ensure()["profile"].get(key)
        return dict(v) if v else None

    def all_profile(self) -> dict[str, dict]:
        doc = self._ensure()
        return {k: dict(v) for k, v in doc["profile"].items()
                if v.get("value")}

    def set_profile(self, key: str, value: str, *, source: str = "dialog",
                    confidence: float = 0.7, pinned: bool | None = None) -> bool:
        """画像 upsert，返回是否写入。闭集外键 / 空值丢弃；规则：
        - pinned 且新来源不是 user → 拒绝（用户手改锁定）
        - 现存 user 来源不被更低 rank 来源在不低于其置信时覆写（同旧表语义）
        - source="user"（设置页手填/亲口）总可写；pinned=None 沿用旧锁定态
        """
        if key not in PROFILE_KEYS:
            return False
        if value in (None, "", "null"):
            if source != "user":
                return False  # 清空只认用户侧操作（dialog 提取端本来就滤空值）
            doc = self._ensure()
            if doc["profile"].pop(key, None) is not None:
                self._dirty = True
                self._save()
            return True
        doc = self._ensure()
        cur = doc["profile"].get(key) or {}
        if pinned is None:
            pinned = bool(cur.get("pinned"))
        if pinned and source != "user":
            return False
        if (cur.get("source") == "user" and source != "user"
                and float(cur.get("confidence") or 0) >= confidence):
            return False
        doc["profile"][key] = {
            "value": str(value)[:200], "source": source,
            "confidence": float(confidence),
            "pinned": bool(cur.get("pinned")) if pinned is None else pinned,
            "updated_at": _now(),
        }
        self._dirty = True
        self._save()
        return True

    def set_pinned(self, key: str, pinned: bool) -> None:
        if key not in PROFILE_KEYS:
            return
        doc = self._ensure()
        entry = doc["profile"].setdefault(
            key, {"value": "", "source": "user", "confidence": 1.0,
                  "updated_at": _now()})
        entry["pinned"] = bool(pinned)
        self._dirty = True
        self._save()

    # ---------- portraits（角色写，用户只读） ----------

    def get_portrait(self, role_id: str) -> str:
        v = self._ensure()["portraits"].get(role_id) or {}
        return str(v.get("text") or "")

    def all_portraits(self) -> dict[str, dict]:
        return {k: dict(v) for k, v in self._ensure()["portraits"].items()
                if v.get("text")}

    def set_portrait(self, role_id: str, text: str) -> None:
        if not role_id:
            return
        doc = self._ensure()
        if text.strip():
            doc["portraits"][role_id] = {"text": str(text)[:300],
                                         "updated_at": _now()}
        else:
            doc["portraits"].pop(role_id, None)
        self._dirty = True
        self._save()
