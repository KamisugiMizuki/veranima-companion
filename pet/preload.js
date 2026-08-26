// 预加载：暴露最小桥接（contextIsolation 安全模式）
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pet', {
  // main → renderer
  onCoreState: (cb) => ipcRenderer.on('core-state', (e, m) => cb(m)),
  onSpeak: (cb) => ipcRenderer.on('speak', (e, m) => cb(m)),
  onSpeakChunk: (cb) => ipcRenderer.on('speak-chunk', (e, m) => cb(m)),
  onSpeakDone: (cb) => ipcRenderer.on('speak-done', () => cb()),
  onBubble: (cb) => ipcRenderer.on('bubble', (e, m) => cb(m)),
  onStopSpeak: (cb) => ipcRenderer.on('stop-speak', () => cb()),
  onMenuPoke: (cb) => ipcRenderer.on('menu-poke', () => cb()),
  onAvatarMap: (cb) => ipcRenderer.on('avatar-map', (e, map) => cb(map)),
  onAvatarHeight: (cb) => ipcRenderer.on('avatar-height', (e, h) => cb(h)),
  // 日志窗口通道
  onLogLine: (cb) => ipcRenderer.on('log-line', (e, m) => cb(m)),
  onLogHistory: (cb) => ipcRenderer.on('log-history', (e, m) => cb(m)),
  // 聊天窗口通道
  onChatHistory: (cb) => ipcRenderer.on('chat-history', (e, m) => cb(m)),
  onChatLine: (cb) => ipcRenderer.on('chat-line', (e, m) => cb(m)),
  onChatEvent: (cb) => ipcRenderer.on('chat-event', (e, m) => cb(m)),
  onChatProfile: (cb) => ipcRenderer.on('chat-profile', (e, m) => cb(m)),
  onChatHidden: (cb) => ipcRenderer.on('chat-hidden', () => cb()),
  onHistorySearchResults: (cb) => ipcRenderer.on('history-search-results', (e, m) => cb(m)),
  onSelfModel: (cb) => ipcRenderer.on('self-model', (e, m) => cb(m)),
  sendChat: (text, images = []) => ipcRenderer.invoke('chat-send', { text, images }),
  transcribeAudio: (audio, filename = 'voice.webm') => ipcRenderer.invoke('stt-transcribe', { audio, filename }),
  getSttInputDevice: () => ipcRenderer.invoke('stt-input-device'),
  clearChat: () => ipcRenderer.invoke('chat-clear'),
  retryChat: (messageId) => ipcRenderer.invoke('chat-retry', messageId),
  getChatState: () => ipcRenderer.invoke('chat-get-state'),
  searchHistory: (query) => ipcRenderer.invoke('search-history', query),
  getSelfModel: () => ipcRenderer.invoke('get-self-model'),
  reconnect: () => ipcRenderer.send('pet-reconnect'),
  stopReply: () => ipcRenderer.send('chat-stop'),  // GUI_SPEC 9：停止说话/取消回复
  stopSpeaking: () => ipcRenderer.send('speech-stop'),
  setSpeechMuted: (muted) => ipcRenderer.send('speech-mute', !!muted),
  resizePet: (dim) => ipcRenderer.send('pet-resize', dim),  // GUI_SPEC 4.2：窗口高度随气泡
  setPetHitShape: (data) => ipcRenderer.send('pet-hit-shape', data),
  // 设置窗口通道：请求 → main 转发 WS → 核心响应回传
  getConfig: () => ipcRenderer.invoke('settings-get-config'),
  saveConfig: (data) => ipcRenderer.invoke('settings-save-config', data),
  profileConfig: (action, profile = {}) => ipcRenderer.invoke('settings-profile-config', action, profile),
  // 设置页扩展：LLM 连接测试（返回模型列表）+ 本地路径浏览框
  testLlm: (payload) => ipcRenderer.invoke('settings-test-llm', payload),
  pickPath: (payload) => ipcRenderer.invoke('settings-pick-path', payload),
  restartCore: () => ipcRenderer.send('core-restart'),
  // renderer → main
  sendEvent: (payload) => ipcRenderer.send('pet-event', payload),
});
