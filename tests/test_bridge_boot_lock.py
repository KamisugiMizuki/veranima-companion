"""bridge.boot 并发串行化回归（09-08 实锤：MainActivity/CompanionService/drive 三处
并发调 boot → 双 store → sqlite database is locked）。"""
import importlib.util
import json
import threading
import time
from pathlib import Path

_BRIDGE = (Path(__file__).resolve().parents[1] / "android" / "fuyuno" / "app"
           / "src" / "main" / "python" / "bridge.py")


def _load():
    spec = importlib.util.spec_from_file_location("fuyuno_bridge", _BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_boot_concurrent_calls_run_impl_once():
    m = _load()
    calls = []

    def fake_impl(files_dir):
        calls.append(files_dir)
        time.sleep(0.2)          # 模拟 create_agent 耗时窗口
        m.boot._done = True
        return json.dumps({"ok": True, "already": False})

    m._boot_impl = fake_impl
    out = []
    threads = [threading.Thread(target=lambda: out.append(m.boot("/x"))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)

    assert len(calls) == 1, f"boot 初始化跑了 {len(calls)} 次（并发未串行化）"
    assert len(out) == 4
    assert sum('"already": true' in r for r in out) == 3
