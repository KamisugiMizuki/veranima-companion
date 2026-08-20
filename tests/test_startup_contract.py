"""桌宠启动链契约：无控制台闪窗、先显示 UI、重服务后台启动。"""
from __future__ import annotations

import json
from pathlib import Path

from veranima.memory.embedding import SentenceTransformersProvider

ROOT = Path(__file__).resolve().parents[1]


def test_local_embedding_provider_is_lazy(tmp_path):
    pooling = tmp_path / "1_Pooling"
    pooling.mkdir()
    (pooling / "config.json").write_text(json.dumps({"word_embedding_dimension": 123}), encoding="utf-8")
    provider = SentenceTransformersProvider(str(tmp_path))
    assert provider.dim == 123
    assert provider._model is None


def test_launcher_hides_all_windows_and_owns_preflight():
    launcher = (ROOT / "scripts/run_pet.py").read_text(encoding="utf-8")
    main = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    assert "def _run_hidden" in launcher
    assert "creationflags=CREATE_NO_WINDOW" in launcher
    assert "preflight_ports()" in launcher
    assert "Local\\\\VeranimaPetLauncher" in launcher
    assert "preflightPorts()" not in main


def test_electron_creates_window_before_background_services():
    main = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    block = main[main.index("app.whenReady().then"):]
    assert block.index("createWindow()") < block.index("startCore()")
    assert block.index("createWindow()") < block.index("startTTS()")


def test_tts_expected_stop_does_not_restart():
    main = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    assert "suppressTTSRestart" in main
    assert "if (suppressTTSRestart) return" in main
    assert "clearTimeout(ttsRestartTimer)" in main
    assert "taskkill', ['/F', '/T', '/PID'" in main


def test_zero_console_launcher_exists():
    text = (ROOT / "run_pet.vbs").read_text(encoding="ascii")
    assert "pythonw.exe" in text
    assert ", 0, False" in text


def test_launcher_does_not_spawn_second_qq_agent():
    launcher = (ROOT / "scripts/run_pet.py").read_text(encoding="utf-8")
    core = (ROOT / "src/veranima/pet_server.py").read_text(encoding="utf-8")
    assert "veranima.qq" not in launcher
    assert "connect_qq" in core
    assert "build_adapter" in core
