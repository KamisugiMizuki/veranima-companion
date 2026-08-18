# -*- coding: utf-8 -*-
"""Patch torch's CUDA version mismatch check (raise -> warning).

torch cu128 requires nvcc 12.8 exactly; this machine has 12.6/12.9/13.2.
We compile with 12.9 (minor-version compatible). Idempotent: safe to run
multiple times. Run with the project venv python:

    .venv\\Scripts\\python.exe scripts\\patch_torch_cuda_check.py
"""
import pathlib

import torch.utils.cpp_extension as m

ORIG = "raise RuntimeError(CUDA_MISMATCH_MESSAGE"
PATCHED = (
    'print("[warn] torch CUDA mismatch tolerated (patched by '
    "veranima scripts\\patch_torch_cuda_check.py)\"); "
    "print(CUDA_MISMATCH_MESSAGE"
)

p = pathlib.Path(m.__file__)
s = p.read_text(encoding="utf-8")
if ORIG in s:
    p.write_text(s.replace(ORIG, PATCHED, 1), encoding="utf-8")
    print(f"[OK] patched {p}")
else:
    print(f"[OK] already patched ({p})")
