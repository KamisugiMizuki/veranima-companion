# -*- coding: utf-8 -*-
"""Enable -DFLASHATTENTION_DISABLE_BACKWARD in flash-attn setup.py.

flash-attn bundles backward kernels (flash_bwd_*.cu) whose template
instantiation needs 5-8GB RAM each during nvcc; building them OOMs on 32GB
machines (catastrophic error: out of memory in cute/layout.hpp).
veranima only runs inference (qwen-tts forward pass), so backward is never
called. This macro skips bwd template instantiation entirely; flash_api.cpp
provides stub implementations (raises if ever called).

Run from the unpacked sdist root:
    python patch_flash_attn_no_bwd.py setup.py
"""
import pathlib
import sys

p = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "setup.py")
s = p.read_text(encoding="utf-8")

OLD = '    # "-DFLASHATTENTION_DISABLE_BACKWARD",'
NEW = '    "-DFLASHATTENTION_DISABLE_BACKWARD",'
if OLD in s:
    p.write_text(s.replace(OLD, NEW, 1), encoding="utf-8")
    print(f"[OK] DISABLE_BACKWARD enabled in {p}")
elif NEW in s:
    print(f"[OK] already enabled in {p}")
else:
    raise SystemExit(f"[ERROR] pattern not found in {p}")
