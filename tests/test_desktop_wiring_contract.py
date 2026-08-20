"""聊天、WS 和透明命中区域的生产接线契约。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_chat_ipc_and_reply_contract():
    main = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    preload = (ROOT / "pet/preload.js").read_text(encoding="utf-8")
    renderer = (ROOT / "pet/chat-renderer.js").read_text(encoding="utf-8")

    for channel in ("chat-send", "chat-retry", "chat-clear", "chat-get-state"):
        assert f"ipcMain.handle('{channel}'" in main
    for api in ("sendChat", "retryChat", "clearChat", "getChatState", "onChatEvent"):
        assert f"{api}:" in preload
    assert "turn < latestReplyTurn" in main
    assert "cancelledReplyTurns.has(turn)" in main
    assert "coreSessionId" in main
    assert "msg.session_id" in main
    assert "request_id: requestId" in main
    assert "renderMessageActions(row, m)" in renderer
    assert "onChatProfile" in renderer
    assert "state.active_reply" in renderer


def test_ws_and_sidecar_failure_guards():
    main = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    core = (ROOT / "src/veranima/pet_server.py").read_text(encoding="utf-8")

    assert "coreProc.on('error'" in main
    assert "ttsProc.on('error'" in main
    assert "if (ws !== socket) return" in main
    assert "rejectWsPending();" in main
    assert "if self._client is ws:" in core
    assert "self._turn_clients[turn_id] = ws" in core
    assert "self._reply_deliverable(turn_id)" in core
    assert 'msg.setdefault("session_id", self.session_id)' in core


def test_native_alpha_hit_shape_wiring():
    main = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    preload = (ROOT / "pet/preload.js").read_text(encoding="utf-8")
    renderer = (ROOT / "pet/renderer.js").read_text(encoding="utf-8")

    assert "function applyPetHitShape" in main
    assert "nativeImage.createFromPath" in main
    assert "win.setShape" in main
    assert "setPetHitShape:" in preload
    assert "function reportHitShape" in renderer
    assert "requestAnimationFrame(reportHitShape)" in renderer
    assert "x: Math.round(box.left - stage.left)" in renderer
    assert "rects: uiRects" in renderer
    assert ".setIgnoreMouseEvents(" not in main + renderer
