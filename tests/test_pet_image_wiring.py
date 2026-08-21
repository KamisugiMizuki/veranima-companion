"""Electron 图片/STT 通道的静态行为契约。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_chat_images_are_runtime_data_not_persisted_base64():
    main = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    renderer = (ROOT / "pet/chat-renderer.js").read_text(encoding="utf-8")
    assert "delete copy.image_data" in main
    assert "message.image_data = chatImageRefs(message.images, false)" in main
    assert "pathToFileURL(resolved.file).href" in main
    assert "chatImageRefs(old.images, true)" in main
    assert "images === message.image_data" not in main
    assert "images: imageResult.values" in main
    assert "const decoded = []" in main and "const written = []" in main
    assert "written.forEach((file) => fs.rmSync(file" in main
    assert "chatHistory.splice(0, chatHistory.length - 500)" in main
    assert "removeUnreferencedChatImages(removed)" in main
    assert "fs.rmSync(path.join(app.getPath('userData'), 'chat-images')" in main
    assert "renderMessageBubble(bubble, m)" in renderer
    assert "!src.startsWith('file://')" in renderer
    assert "encoded.length > Math.ceil(MAX_CHAT_IMAGE_BYTES / 3) * 4" in main
    assert "raw.toString('base64') !== encoded" in main
    assert "nativeImage.createFromBuffer(raw)" in main
    assert "size.width * size.height > MAX_CHAT_IMAGE_PIXELS" in main
    assert "retryMessage(failedId)" in renderer


def test_clipboard_and_stt_are_connected():
    renderer = (ROOT / "pet/chat-renderer.js").read_text(encoding="utf-8")
    preload = (ROOT / "pet/preload.js").read_text(encoding="utf-8")
    main = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    assert "event.clipboardData" in renderer
    assert "FileReader" in renderer
    assert "getSttInputDevice" in preload
    assert "deviceId: { deviceId: deviceId }" not in renderer
    assert "deviceId: { exact: deviceId }" in renderer
    assert "transcribeAudio" in renderer
    assert "ipcRenderer.invoke('stt-transcribe'" in preload
    assert "localFeatureValue('stt', 'base_url'" in main
    assert "localFeatureValue('stt', 'model'" in main
    assert "localFeatureValue('stt', 'language'" in main
    assert "AbortSignal.timeout(timeoutMs)" in main
    assert "new Blob([raw], { type: 'audio/webm' })" in main
    assert "Authorization: 'Bearer ' + apiKey" in main
    assert "transcription complete: chars=" in main
    assert "MAX_RECORDING_BYTES = 20 * 1024 * 1024" in renderer
    assert "MAX_RECORDING_MS = 120 * 1000" in renderer
    assert "recorder.start(1000)" in renderer
    assert "getSttInputDevice" in preload
    assert "deviceId: { exact: deviceId }" in renderer
    assert "没有识别到清晰语音" in renderer
    assert "function cleanupRecording(discard = true)" in renderer
    assert "recorder.onerror" in renderer
    assert "stream.getTracks().forEach((track) => track.stop())" in renderer
    assert "onChatHidden(() => cleanupRecording(true))" in renderer
    assert "addEventListener('pagehide', () => cleanupRecording(true))" in renderer
    assert "chatWin.webContents.send('chat-hidden')" in main
    assert "onChatHidden: (cb) => ipcRenderer.on('chat-hidden'" in preload


def test_ws_disconnect_fails_pending_chat_and_partial_reply():
    main = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    helper = main[main.index("function failPendingChats()"):main.index("function disconnectSocket(socket)")]
    assert "updateChatMessage(message.message_id, { status: 'failed' })" in helper
    assert "pendingUserMessages.clear()" in helper
    assert "finishReply(activeReply, 'failed')" in helper
    disconnect = main[main.index("function disconnectSocket(socket)"):main.index("function openSettingsWindow()")]
    assert "rejectWsPending()" in disconnect and "failPendingChats()" in disconnect
    assert "setPetStatus('offline')" in disconnect and "scheduleReconnect()" in disconnect
    handlers = main[main.index("socket.on('close'"):main.index("function scheduleReconnect()")]
    assert handlers.count("disconnectSocket(socket)") == 2
    assert "pendingUserMessages.delete(requestId)" in main[main.index("function dispatchChat"):]


def test_stt_process_lifecycle_is_owned_by_shell():
    main = (ROOT / "pet/main.js").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts/run_pet.py").read_text(encoding="utf-8")
    assert "startSTT()" in main and "stopSTT()" in main
    assert "localFeatureEnabled('stt', true)" in main
    assert "spawn(py, [script]" in main
    assert "'data', 'stt-runtime', 'site'" in main
    assert "localFeatureValue('stt', 'model_path'" in main
    assert "function scheduleSTTRestart()" in main
    assert "sttRestartDelay = Math.min(sttRestartDelay * 2, 60000)" in main
    assert "stt process error" in main and "scheduleSTTRestart();" in main
    assert "sttRestartDelay = 3000" in main
    assert "AbortSignal.timeout(1500)" in main
    assert "stt health ready; reusing existing service" in main
    assert "health?.provider === 'sensevoice'" in main
    assert "stt port 9890 conflict" in main
    assert "function spawnLocalSTT()" in main
    assert "vad_model_path" in main
    assert "tag === 'stt' ? 'stt.log'" in main
    assert "9890" in launcher
    assert "stt.server" in launcher or "run_stt_server" in launcher
