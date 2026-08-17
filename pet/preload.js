// 预加载：暴露最小桥接（contextIsolation 安全模式）
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pet', {
  // main → renderer
  onCoreState: (cb) => ipcRenderer.on('core-state', (e, m) => cb(m)),
  onSpeak: (cb) => ipcRenderer.on('speak', (e, m) => cb(m)),
  onBubble: (cb) => ipcRenderer.on('bubble', (e, m) => cb(m)),
  onStopSpeak: (cb) => ipcRenderer.on('stop-speak', () => cb()),
  // 日志窗口通道
  onLogLine: (cb) => ipcRenderer.on('log-line', (e, m) => cb(m)),
  onLogHistory: (cb) => ipcRenderer.on('log-history', (e, m) => cb(m)),
  // renderer → main
  sendEvent: (payload) => ipcRenderer.send('pet-event', payload),
  setIgnoreMouse: (ignore) => ipcRenderer.send('set-ignore-mouse', ignore),
});
