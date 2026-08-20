"""托盘退出链契约：真正退出必须放行窗口并清理 sidecar。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tray_quit_uses_single_shutdown_path():
    text = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    assert "let isQuitting = false" in text
    assert "function prepareQuit()" in text
    assert "function quitApplication()" in text
    assert "click: () => quitApplication()" in text
    assert "app.on('before-quit', () => { prepareQuit(); })" in text


def test_all_reusable_windows_allow_real_close_during_quit():
    text = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    assert text.count("if (isQuitting) return;") >= 4
    assert "if (isQuitting) return;\n    e.preventDefault();\n    win.hide();" in text
    assert "if (isQuitting) return;\n    e.preventDefault();\n    chatWin.hide();" in text


def test_shutdown_kills_process_tree_before_app_quit():
    text = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    assert "spawnSync('taskkill', ['/F', '/T', '/PID'" in text
    assert "function prepareQuit()" in text
    assert "stopCore();\n  stopTTS();" in text


def test_launcher_reaps_sidecars_after_shell_exit():
    text = (ROOT / "scripts/run_pet.py").read_text(encoding="utf-8")
    finally_block = text[text.index("    finally:"):]
    assert "preflight_ports()" in finally_block
