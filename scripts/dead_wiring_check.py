#!/usr/bin/env python
"""扫「写好没接线」：定义了但生产侧零引用的函数/方法（只读静态分析）。

用法：
    .venv/Scripts/python scripts/dead_wiring_check.py           # 只报可疑（跳过私有）
    .venv/Scripts/python scripts/dead_wiring_check.py --all     # 连私有一起报

引用面 = 全仓 tracked 的 .py（排除 tests/、exports/、build 产物）+ Kotlin/JS 字面量。
tests/ 单独统计：
    TEST-ONLY  仅测试引用 —— 「写好+测好，生产零调用」（apply_emotion_event 就是这型）
    ZERO       生产/测试全零引用 —— 死代码或纯外部入口（需人工确认）

已知假阳性：字典/闭包分发（如 `{"meal": _collect_meal}`）已算接线；
getattr(动态字符串) 分发仍会漏 → 报出后 grep 确认。
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = ("tests/", "exports/", "build/", "node_modules/", ".venv/", "third_party/")


def tracked() -> list[str]:
    out = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    return [p for p in out.splitlines() if not p.startswith(SKIP_DIRS)]


class DefCollector(ast.NodeVisitor):
    """收集函数/方法定义；只把类体直接子节点算作方法，嵌套函数按普通函数算。"""

    def __init__(self):
        self.out: list[tuple[str, str | None, int]] = []
        self._class: str | None = None

    def visit_ClassDef(self, node):
        outer = self._class
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._class = node.name
                self._record(child)
                self._class = None
                for g in child.body:  # 方法体内的嵌套函数不算方法
                    self.visit(g)
            else:
                self._class = None
                self.visit(child)
        self._class = outer

    def visit_FunctionDef(self, node):
        self._record(node)
        for child in node.body:
            self.visit(child)

    visit_AsyncFunctionDef = visit_FunctionDef

    def _record(self, node):
        n = node.name
        if (n.startswith("__") and n.endswith("__")) or n.startswith("test_"):
            return
        self.out.append((n, self._class, node.lineno))


def refs(files: list[str]) -> tuple[set[str], set[str]]:
    names, attrs = set(), set()
    for rel in files:
        if not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                names.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                attrs.add(node.attr)
    return names, attrs


def main() -> int:
    files = tracked()
    prod = [f for f in files if f.endswith(".py")
            and (f.startswith("src/") or ("/python/" in f and f.startswith("android/"))
                 or "/" not in f)]
    test = [p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").rglob("*.py")
            if "__pycache__" not in p.parts]
    cross_text = "\n".join((ROOT / f).read_text(encoding="utf-8", errors="ignore")
                           for f in files if f.endswith((".kt", ".js", ".html")))
    pn, pa = refs(prod)
    tn, ta = refs(test)

    rows = []
    for rel in prod:
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        dc = DefCollector()
        dc.visit(tree)
        for name, cls, lineno in dc.out:
            pool, tpool = (pa, ta) if cls else (pn, tn)
            if name in pool or name in cross_text:
                continue
            rows.append(("TEST-ONLY" if name in tpool else "ZERO", rel, cls or "-", name, lineno))

    rows.sort(key=lambda r: (r[0] != "TEST-ONLY", r[1], r[4]))
    show_all = "--all" in sys.argv
    print(f"# 候选 {len(rows)} 个（生产侧无按名引用）\n")
    for tag, rel, cls, name, lineno in rows:
        if tag == "ZERO" and name.startswith("_") and not show_all:
            continue
        where = f"{cls}.{name}" if cls != "-" else name
        print(f"{tag:9} {rel}:{lineno}  {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
