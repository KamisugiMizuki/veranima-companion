"""动态素材失败账本：ref 兜底 + kind 保险丝（09-08 实机 D01 重试风暴）。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from veranima.memory.store import MemoryStore  # noqa: E402


def _fail(s, kind, role="lin", verdict="failed", ref=""):
    s.log_decision(role, f"moment:{kind}", verdict, reason="test", object_ref=ref, digest="d")


def test_kind_fuse_needs_two_failures():
    s = MemoryStore(":memory:")
    _fail(s, "D01")
    assert s.moment_failed_kinds_today("lin") == set(), "单次失败不该封杀（LLM 偶发）"
    _fail(s, "D01")
    assert s.moment_failed_kinds_today("lin") == {"D01"}, "连续两次失败应封杀该 kind"
    assert "D02" not in s.moment_failed_kinds_today("lin"), "不该误伤其他 kind"


def test_kind_fuse_is_role_isolated():
    s = MemoryStore(":memory:")
    _fail(s, "D01")
    _fail(s, "D01")
    assert s.moment_failed_kinds_today("xumian") == set()


def test_ref_ledger_counts_deduped():
    s = MemoryStore(":memory:")
    _fail(s, "D02", verdict="deduped", ref="ref:w:lin:2026-09-08")
    assert s.moment_failed_refs_today("lin") == {"w:lin:2026-09-08"}, "deduped 也该进账本"


def test_kind_fuse_ignores_empty_ref():
    """实机 object_ref 落库为空时，ref 账本空转但 kind 保险丝仍生效。"""
    s = MemoryStore(":memory:")
    _fail(s, "D01", ref="")
    _fail(s, "D01", ref="")
    assert s.moment_failed_refs_today("lin") == set(), "无 ref 时 ref 账本必然空"
    assert s.moment_failed_kinds_today("lin") == {"D01"}, "kind 保险丝不依赖 ref"
