"""伴侣备份行为测试：导出→换端导入→向量按当前模型重铸→召回可用；全量覆盖留 .old。"""
import json
import sqlite3
from pathlib import Path

import pytest

sqlite_vec = pytest.importorskip("sqlite_vec")

from veranima.core.backup import export_backup, import_backup
from veranima.memory.schema import init_db


class StubProvider:
    dim = 8

    def __init__(self, model="model-a"):
        self.model = model
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for i, t in enumerate(texts):
            h = sum(ord(c) for c in t)
            out.append([((h >> (j * 2)) % 7) / 7.0 + 0.1 for j in range(8)])
        return out


def _make_root(tmp: Path, n_mem: int = 5) -> Path:
    root = tmp / "project"
    (root / "characters" / "r1" / "portraits").mkdir(parents=True)
    (root / "characters" / "r1" / "character.json").write_text('{"name":"R1"}', encoding="utf-8")
    (root / "characters" / "r1" / "portraits" / "p1.png").write_bytes(b"\x89PNG")
    (root / "data").mkdir()
    (root / "data" / "mirror.json").write_text('{"top":1}', encoding="utf-8")
    con = init_db(root / "data" / "veranima.db", dim=8, provider=StubProvider())
    for i in range(n_mem):
        con.execute(
            "INSERT INTO memories(layer,content,created_at,updated_at) VALUES('semantic',?,?,?)",
            (f"记忆条目 {i} 内容", "2026-08-01T00:00:00", "2026-08-01T00:00:00"))
    con.commit()
    rows = con.execute("SELECT id, content FROM memories").fetchall()
    prov = StubProvider()
    for r in rows:
        con.execute("INSERT INTO memory_vec(memory_id, embedding) VALUES (?,?)",
                    (r[0], json.dumps(prov.embed([r[1]])[0])))
    con.commit()
    con.close()
    return root


def test_export_import_same_model_keeps_vectors(tmp_path):
    root = _make_root(tmp_path)
    out = export_backup(root, root / "data" / "veranima.db",
                        embedding_spec="openai:m-a", out_path=tmp_path / "bk.zip")
    assert out.exists()

    dest = tmp_path / "phone" / "project"
    (dest / "data").mkdir(parents=True)
    (dest / "data" / "veranima.db").write_bytes(b"OLDDB")  # 覆盖对象
    prov = StubProvider()
    res = import_backup(dest, out, embedding_spec="openai:m-a", embedding_dim=8,
                        embed_fn=prov.embed)
    assert res["memories"] == 5
    assert res["reembedded"] == 0          # 同模型同维度 → 不重嵌
    assert prov.calls == 0
    assert (dest / "data" / "veranima.db.old").read_bytes() == b"OLDDB"
    assert json.loads((dest / "data" / "mirror.json").read_text()) == {"top": 1}


def test_import_new_model_reembeds_and_recall_works(tmp_path):
    root = _make_root(tmp_path)
    export_backup(root, root / "data" / "veranima.db",
                  embedding_spec="openai:old-bge", out_path=tmp_path / "bk.zip")
    dest = tmp_path / "phone2" / "project"
    (dest / "data").mkdir(parents=True)

    class NewProv(StubProvider):  # 不同"模型"的向量分布（对内容敏感）
        def embed(self, texts):
            self.calls += 1
            return [[(sum(ord(c) for c in t) % 11 + j * 3) % 13 / 13.0 + 0.02 for j in range(8)]
                    for t in texts]

    prov = NewProv()
    res = import_backup(dest, tmp_path / "bk.zip", embedding_spec="openai:new-qwen",
                        embedding_dim=8, embed_fn=prov.embed)
    assert res["reembedded"] == 5          # 模型变了 → 全量重铸
    con = sqlite3.connect(dest / "data" / "veranima.db")
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    assert con.execute("SELECT count(*) FROM memory_vec").fetchone()[0] == 5
    q = json.dumps(prov.embed(["记忆条目 3 内容"])[0])
    hit = con.execute(
        "SELECT memory_id FROM memory_vec WHERE embedding MATCH ? AND k=1 ORDER BY distance",
        (q,)).fetchone()
    content = con.execute("SELECT content FROM memories WHERE id=?", (hit[0],)).fetchone()[0]
    assert content == "记忆条目 3 内容"     # 召回命中重嵌后的正确原文
    con.close()


def test_import_without_embed_fn_raises(tmp_path):
    root = _make_root(tmp_path)
    export_backup(root, root / "data" / "veranima.db",
                  embedding_spec="openai:x", out_path=tmp_path / "bk.zip")
    dest = tmp_path / "p3"
    (dest / "data").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="embedding"):
        import_backup(dest, tmp_path / "bk.zip", embedding_spec="openai:y",
                      embedding_dim=8, embed_fn=None)
