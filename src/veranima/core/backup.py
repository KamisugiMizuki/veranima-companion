"""记忆备份：共享记忆本体（SQLite 一致性快照）+ 学习状态文件。

设计（2026-08-29 拍板）：
- 记忆是**所有角色共用**的，所以这里只管 veranima.db + mirror/style 状态；
  角色是**角色间互相独立**的，走 roles export/import（.char 包）另一条通道。
- 备份的是原文与状态。DB 快照虽连带向量，但向量只在**模型+维度一致**时才可信复用；
  否则导入端丢弃并按当前 embedding 模型全量重铸（memory_vec 是派生数据）。
- 导入 = **全量覆盖**：DB 覆盖前移为 veranima.db.old、状态文件移为 *.old（各留一份，
  防手滑）。
- Windows 与安卓跑同一份代码，格式通用：一个 zip。

包结构::

    manifest.json            {version, created_at, embedding_model, embedding_dim, counts}
    db/veranima.db           sqlite3.backup() 一致性快照
    state/mirror.json        风格镜像状态（存在才收）
    state/style.json         风格学习参数（存在才收）
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

MANIFEST_VERSION = 1
BACKUP_DB_NAME = "veranima.db"


def _db_tables(con: sqlite3.Connection) -> dict[str, int]:
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%' AND name != 'memory_vec'")]
    out = {}
    for n in names:
        try:
            out[n] = con.execute(f"SELECT count(*) FROM \"{n}\"").fetchone()[0]
        except sqlite3.Error:
            out[n] = -1
    return out


def export_backup(root: Path, db_path: Path, *, embedding_spec: str,
                  out_path: Path) -> Path:
    """生成记忆备份包。root=项目根（找状态文件），db_path=veranima.db。"""
    root = Path(root)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        snap = Path(out_path).with_suffix(".snap.tmp")
        snap.unlink(missing_ok=True)
        dst = sqlite3.connect(snap)
        src.backup(dst)  # WAL 安全的在线一致性快照
        dst.close()
        manifest = {
            "version": MANIFEST_VERSION,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "embedding_model": embedding_spec,
            "embedding_dim": _vec_dim(src) or 0,
            "counts": _db_tables(src),
        }
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snap, "db/" + BACKUP_DB_NAME)
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            for name in ("mirror.json", "style.json"):
                f = root / "data" / name
                if f.exists():
                    zf.write(f, f"state/{name}")
        snap.unlink(missing_ok=True)
    finally:
        src.close()
    return out_path


def _vec_dim(con: sqlite3.Connection) -> int | None:
    row = con.execute("SELECT sql FROM sqlite_master WHERE name='memory_vec'").fetchone()
    if not row or not row[0]:
        return None
    import re
    m = re.search(r"float\[(\d+)\]", row[0])
    return int(m.group(1)) if m else None


def _stow(path: Path) -> None:
    """把将被覆盖的旧文件移为 .old（覆盖上一份 .old）。"""
    old = path.with_name(path.name + ".old")
    if old.is_dir():
        shutil.rmtree(old)
    elif old.exists():
        old.unlink()
    if path.exists():
        path.rename(old)


def import_backup(root: Path, zip_path: Path, *, embedding_spec: str, embedding_dim: int,
                  embed_fn=None) -> dict:
    """全量覆盖导入记忆。embed_fn: Callable[[list[str]], list[list[float]]]，重嵌时调。

    返回 manifest + 摘要 {reembedded: int, stowed: [paths]}。
    调用前提：核心已停止（Windows 端先关桌宠/QQ bot）。
    """
    root = Path(root)
    with zipfile.ZipFile(zip_path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        if manifest.get("version") != MANIFEST_VERSION:
            raise ValueError(f"备份包版本不兼容: {manifest.get('version')}")
        data = zf.read("db/" + BACKUP_DB_NAME)

    db_file = root / "data" / BACKUP_DB_NAME
    db_file.parent.mkdir(parents=True, exist_ok=True)
    stowed: list[str] = []
    _stow(db_file)
    stowed.append(str(db_file) + ".old")
    for suffix in ("-wal", "-shm"):  # 陈旧 WAL 配新库 = 损坏，必须清
        (root / "data" / (BACKUP_DB_NAME + suffix)).unlink(missing_ok=True)
    db_file.write_bytes(data)

    # 学习状态文件
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.filename.startswith("state/"):
                target = root / "data" / Path(info.filename).name
                _stow(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))

    # 打开导入库，重嵌判定：模型变 / 维度变 / 向量数不齐 → 全量重铸
    con = sqlite3.connect(db_file)
    con.enable_load_extension(True)
    try:
        import sqlite_vec
        sqlite_vec.load(con)
    except Exception:
        con.close()
        return {**manifest, "reembedded": -1, "stowed": stowed, "note": "sqlite-vec 不可用，向量未校验"}
    mem_n = con.execute("SELECT count(*) FROM memories WHERE content != ''").fetchone()[0]
    try:
        vec_n = con.execute("SELECT count(*) FROM memory_vec").fetchone()[0]
    except sqlite3.Error:
        vec_n = -1
    old_dim = _vec_dim(con)
    need = (manifest.get("embedding_model") != embedding_spec) or (old_dim != embedding_dim) or (vec_n != mem_n)
    reembedded = 0
    if need:
        if not embed_fn:
            con.close()
            raise RuntimeError("需要重嵌但没有可用 embedding provider（检查 config memory.embedding_model）")
        if old_dim != embedding_dim:
            con.execute("DROP TABLE IF EXISTS memory_vec")
            con.execute(
                "CREATE VIRTUAL TABLE memory_vec USING vec0("
                f"memory_id INTEGER PRIMARY KEY, embedding float[{embedding_dim}] distance_metric=cosine)")
        con.execute("DELETE FROM memory_vec")
        rows = con.execute("SELECT id, content FROM memories WHERE content != ''").fetchall()
        B = 8  # ponytail: dashscope 批上限 10，全端统一 8；换站点再调
        for i in range(0, len(rows), B):
            batch = rows[i:i + B]
            try:
                vecs = embed_fn([r[1][:2000] for r in batch])
            except Exception:
                vecs = [embed_fn([r[1][:2000]])[0] for r in batch]  # 批失败退单条
            for (mid, _), v in zip(batch, vecs):
                if len(v) != embedding_dim:
                    raise RuntimeError(f"embedding 维度不符: 返回 {len(v)}, 期望 {embedding_dim}")
                con.execute("INSERT INTO memory_vec(memory_id, embedding) VALUES (?,?)",
                            (mid, json.dumps(v)))
            con.commit()
        reembedded = len(rows)
    con.close()
    return {**manifest, "reembedded": reembedded, "stowed": stowed, "memories": mem_n}
