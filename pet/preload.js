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
  sendChat: (text) => ipcRenderer.send('chat-send', text),
  reconnect: () => ipcRenderer.send('pet-reconnect'),
  stopReply: () => ipcRenderer.send('chat-stop'),  // GUI_SPEC 9：停止说话/取消回复
  resizePet: (dim) => ipcRenderer.send('pet-resize', dim),  // GUI_SPEC 4.2：窗口高度随气泡
  // 设置窗口通道：请求 → main 转发 WS → 核心响应回传
  getConfig: () => ipcRenderer.invoke('settings-get-config'),
  saveConfig: (data) => ipcRenderer.invoke('settings-save-config', data),
  restartCore: () => ipcRenderer.send('core-restart'),
  // renderer → main
  sendEvent: (payload) => ipcRenderer.send('pet-event', payload),
});
