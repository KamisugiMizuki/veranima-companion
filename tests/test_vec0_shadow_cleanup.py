"""vec0 影子表残留清理（实机 177 行 rowids + 1 行 chunks 单独残留）。"""
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from veranima.memory.schema import migrate_vec0  # noqa: E402


def test_leftover_shadows_dropped_when_main_absent():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE memory_vec_rowids(a)")
    con.execute("CREATE TABLE memory_vec_chunks(a)")
    assert migrate_vec0(con) == 0
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not ({"memory_vec_rowids", "memory_vec_chunks"} & names), "影子表该清掉"


def test_no_vec0_at_all_is_noop():
    con = sqlite3.connect(":memory:")
    assert migrate_vec0(con) == 0
