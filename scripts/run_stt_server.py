"""Start Veranima STT with the isolated FunASR overlay.

The GPT-SoVITS runtime supplies Python 3.9 and torch.  FunASR 1.4.x and
its newer dependencies live in data/stt-runtime/site so the TTS environment
is not modified.
"""
from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--site-path", default=str(ROOT / "data" / "stt-runtime" / "site"))
    args, remaining = parser.parse_known_args()
    site = Path(args.site_path).resolve()
    if not site.is_dir():
        raise SystemExit(f"STT runtime overlay not found: {site}")
    sys.path.insert(0, str(site))
    sys.path.insert(0, str(ROOT / "src"))
    # server.py owns the actual CLI; do not leak this wrapper-only option into it.
    sys.argv = [sys.argv[0], *remaining]
    runpy.run_module("veranima.stt.server", run_name="__main__")


if __name__ == "__main__":
    main()
