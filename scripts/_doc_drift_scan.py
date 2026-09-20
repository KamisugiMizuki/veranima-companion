"""文档漂移扫描（R16，2026-09-20）：把 docs/ 与 README 里可机械核验的引用与代码对一遍。

只管三类能客观判定的事实，不做语义判断：
  1. 引用的代码路径（`xxx.py` / `a/b/c.js` …）在仓库里是否存在（按 basename 或相对路径解析）；
  2. 文档写的测试基线（"NNN passed"）是否与当前实测一致（需 --baseline 传入）。

反引号符号不做机械核验：规范文档本就大量使用设计词汇（CurrentScene / LifeTheme…），
它们对应字段或概念而非标识符，机械比对只会产噪（试过，182 条几乎全是假阳）。

用法：
    python scripts/_doc_drift_scan.py                 # 列问题
    python scripts/_doc_drift_scan.py --baseline 1303 # 校验测试基线数字
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "characters", "exports", "data", "logs"}

# 注意：扩展名按「长的在前」排（json 在 js 前），否则 `.json` 会被 `.js` 抢走前半截
_EXT = r"(?:jsonl|json|yaml|yml|py|kt|kts|js|md|sh|bat|vbs|txt)"

# 运行期产物 / 别的系统 / 未动工设计的路径：文档写它们是对的，只是不在仓库里。
# （本盘扫描只回答「文档点的仓库内文件在不在」；这些按定义不在 → 白名单，注释留证）
KNOWN_EXTERNAL = (
    "userData/", "data/", "char_card_design/",
    "chat.json", "onboarding.json", "style.json", "profile.json", "manifest.json",
    "checksums.json", "active.json", "index.json", "index.v1.backup.json",
    "release_audit.json", "mirror.json", "usermodel.json",
    "SOUL.md", "MEMORY.md", "USER.md", "AGENTS.md",   # Hermes 侧文件
    "virtual_world.json",                              # D2-B 世界文件（W-0~W-6 未动工）
    "api_v2.py",                                       # GPT-SoVITS 运行时（gitignore 的 tts/gpt-sovits/）
    "dialogue.jsonl", "seed.jsonl", "deletions.jsonl",  # charpkg/语料包内部文件（打包产物里）
    "docs/proactive/PROACTIVE_DESIGN_REVIEW.md",        # 已按裁决删除（DOC_DRIFT 的历史记录里提到）
)

# 它本身就是漂移账本：正文要引用「改之前的旧路径/旧数字」，扫它必然产假阳
SKIP_DOCS = {"docs/DOC_DRIFT.md"}

# 「这句在说计划/历史，不是在说现状」的行内标记：不参与机械核验
PLAN_LINE = ("建议", "计划", "拟新增", "目标文件", "最小新增", "仅在", "暂缓", "待补", "尚未",
             "已删", "已退役", "已移除", "替代", "历史", "迁移前", "新增测试", "可选",
             "未拆", "未建")
DATE_RE = re.compile(r"20\d\d-\d\d")   # 带日期的说法=历史记录（如「08-31 实测 1024 passed」）
PATH_RE = re.compile(rf"`([A-Za-z0-9_][\w/\-\.]*\.{_EXT})`")
PATH_RE2 = re.compile(rf"\b((?:src|tests|docs|pet|scripts|android|characters|config)/[\w/\-\.]+\.{_EXT})")
SYMBOL_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_\.]{2,})(?:\(\))?`")
BASELINE_RE = re.compile(r"(\d{3,5})\s+passed")
NOISE_SYMBOLS = {
    "true", "false", "none", "null", "json", "yaml", "sqlite", "utf-8", "iso", "utc", "cst",
    "GET", "POST", "HTTP", "HTTPS", "WS", "CT", "LLM", "TTS", "STT", "PAD", "TV", "ID",
}


def _corpus() -> str:
    parts = []
    for base in ("src", "tests", "pet", "scripts", "android", "config", "characters"):
        for p in (ROOT / base).rglob("*"):
            if p.is_file() and p.suffix in {".py", ".js", ".kt", ".kts", ".json", ".yaml", ".yml", ".md"}:
                if any(s in p.parts for s in SKIP_DIRS):
                    continue
                try:
                    parts.append(p.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    pass
    return "\n".join(parts)


def _names_by_suffix() -> dict[str, set[str]]:
    """文件名索引：跳过 node_modules/.venv 等，但角色目录也要进（文档常写相对路径
    `virtual_schedule.json`，实际在 characters/<role>/ 下）。"""
    out: dict[str, set[str]] = {}
    skip = {".git", "node_modules", "__pycache__", ".venv"}
    for base in ("src", "tests", "pet", "scripts", "android", "config", "characters", "docs"):
        for p in (ROOT / base).rglob("*"):
            if p.is_file() and not any(s in p.parts for s in skip):
                out.setdefault(p.name, set()).add(p.relative_to(ROOT).as_posix())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", type=int, default=0, help="当前实测 passed 数（校验文档里的基线）")
    ap.add_argument("--symbols", action="store_true", help="额外核对反引号符号（噪声大，默认关）")
    args = ap.parse_args()

    corpus = _corpus()
    names = _names_by_suffix()
    docs = [ROOT / "README.md"] + sorted((ROOT / "docs").rglob("*.md"))
    problems: list[str] = []
    for doc in docs:
        rel = doc.relative_to(ROOT).as_posix()
        if rel in SKIP_DOCS:
            continue
        text = doc.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        for m in list(PATH_RE.finditer(text)) + list(PATH_RE2.finditer(text)):
            ref = m.group(1)
            if any(k in ref for k in KNOWN_EXTERNAL):
                continue
            line = lines[text[:m.start()].count("\n")] if lines else ""
            if any(k in line for k in PLAN_LINE) or DATE_RE.search(line):
                continue          # 计划/历史语境：不是在声称现状
            if ref in names or Path(ref).name in {k for k in names}:
                continue
            if (ROOT / ref).exists():
                continue
            problems.append(f"{rel}: 引用了不存在的文件 `{ref}`")
        for m in ([] if not args.symbols else SYMBOL_RE.finditer(text)):
            sym = m.group(1)
            if sym in NOISE_SYMBOLS or len(sym) < 4 or sym.startswith(("http", "www")):
                continue
            short = sym.split(".")[-1]
            if len(short) < 4 or short.upper() == short:
                continue
            if short in corpus:
                continue
            if "." in sym and sym.split(".")[0] in corpus:
                continue
            problems.append(f"{rel}: 引用了找不到的符号 `{sym}`")
        if args.baseline and "/audits/" not in rel:
            for m in BASELINE_RE.finditer(text):
                n = int(m.group(1))
                if abs(n - args.baseline) <= 5:
                    continue
                line = lines[text[:m.start()].count("\n")] if lines else ""
                if DATE_RE.search(line) or any(k in line for k in PLAN_LINE):
                    continue      # 带日期/历史语境的基线数字 = 记录，不是当前声称
                problems.append(f"{rel}: 测试基线写的是 {n} passed（当前实测 {args.baseline}）")
    seen = set()
    for line in problems:
        if line in seen:
            continue
        seen.add(line)
        print(line)
    print(f"\n共 {len(seen)} 条待核（扫描 {len(docs)} 份文档）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
