// Veranima 桌宠壳 main 进程（M3_SPEC 3.5）
// 职责：窗口管理（透明/置顶/穿透）、WS 连核心、状态广播、health 自愈
const { app, BrowserWindow, Tray, Menu, ipcMain, powerSaveBlocker } = require('electron');
const path = require('path');

const CORE_WS = process.env.VERANIMA_PET_WS || 'ws://127.0.0.1:8765';
const MEMORY_LIMIT_MB = 400;      // 渲染进程 RSS 阈值（M3_SPEC 3.4 缺陷1）
const HEALTH_INTERVAL_MS = 5 * 60 * 1000; // 5min 采样

let win = null;
let tray = null;
let ws = null;
let reconnectDelay = 1000;
let speaking = false;

// ---------- 窗口 ----------
function createWindow() {
  win = new BrowserWindow({
    width: 220, height: 260,
    transparent: true,            // 透明背景
    frame: false,                 // 无边框
    alwaysOnTop: true,            // 置顶
    resizable: false,
    skipTaskbar: true,            // 不进任务栏（托盘常驻）
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  });
  win.setAlwaysOnTop(true, 'screen-saver'); // 尽量压在普通窗口上
  win.loadFile('index.html');
  // 点击穿透：默认整个窗口穿透（形象区域由 renderer 通过 setIgnoreMouseEvents(false) 局部恢复）
  win.setIgnoreMouseEvents(true, { forward: true });

  win.webContents.on('render-process-gone', (e, details) => {
    console.error('[health] renderer gone:', details.reason);
    setTimeout(() => { win && win.reload(); }, 2000); // 自愈重建（M3_SPEC 3.4 缺陷2）
  });
  win.on('closed', () => { win = null; });
}

// ---------- 托盘 ----------
function createTray() {
  tray = new Tray(path.join(__dirname, 'assets', 'idle.png'));
  tray.setToolTip('Veranima 桌宠');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '显示/隐藏', click: () => { win ? win.show() : createWindow(); } },
    { label: '退出桌宠（核心继续跑）', click: () => app.quit() },
  ]));
  tray.on('click', () => { win && win.isVisible() ? win.hide() : (win ? win.show() : createWindow()); });
}

// ---------- WS 连接核心（指数退避重连） ----------
function connect() {
  if (ws) return;
  const WebSocket = require('ws');
  try {
    ws = new WebSocket(CORE_WS);
  } catch (e) {
    scheduleReconnect();
    return;
  }
  ws.on('open', () => {
    console.log('[ws] connected to core');
    reconnectDelay = 1000;
    win && win.webContents.send('core-state', { connected: true });
  });
  ws.on('message', (data) => {
    try {
      const msg = JSON.parse(data.toString());
      handleCoreMsg(msg);
    } catch (e) { console.error('[ws] bad msg:', e.message); }
  });
  ws.on('close', () => {
    console.log('[ws] closed');
    ws = null;
    win && win.webContents.send('core-state', { connected: false });
    scheduleReconnect();
  });
  ws.on('error', (e) => {
    console.error('[ws] error:', e.message);
    ws && ws.close();
  });
}

function scheduleReconnect() {
  setTimeout(connect, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 30000); // 1s→2s→4s…上限30s
}

// ---------- 核心消息 → renderer ----------
function handleCoreMsg(msg) {
  switch (msg.type) {
    case 'state':
      win && win.webContents.send('core-state', msg);
      break;
    case 'speak':
      win && win.webContents.send('speak', { text: msg.text, tags: msg.tags || [] });
      break;
    case 'bubble':
      win && win.webContents.send('bubble', { text: msg.text });
      break;
    case 'stop_speak':
      win && win.webContents.send('stop-speak', {});
      break;
    default:
      console.warn('[ws] unknown msg type:', msg.type);
  }
}

// ---------- renderer → 核心 ----------
ipcMain.on('pet-event', (e, payload) => {
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify(payload));
  }
});
// 形象区域鼠标交互：穿透↔捕获切换（renderer 拖动/点击时恢复）
ipcMain.on('set-ignore-mouse', (e, ignore) => {
  win && win.setIgnoreMouseEvents(ignore, { forward: true });
});

// ---------- health：渲染进程内存监控 + 自愈 ----------
function healthCheck() {
  if (!win || win.isDestroyed()) return;
  win.webContents.getProcessMemoryInfo().then((info) => {
    const rssMB = Math.round(info.workingSetSize / 1024 / 1024);
    console.log(`[health] renderer RSS: ${rssMB}MB`);
    if (rssMB > MEMORY_LIMIT_MB) {
      console.warn(`[health] RSS ${rssMB}MB > ${MEMORY_LIMIT_MB}MB, reloading renderer`);
      win.reload(); // 壳无状态，重启无损
    }
  }).catch(() => {});
}

// ---------- 生命周期 ----------
const gotLock = app.requestSingleInstanceLock(); // M3_SPEC 3.4 缺陷6
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    win && win.isMinimized() && win.restore();
    win && win.show();
  });
  app.whenReady().then(() => {
    powerSaveBlocker.start('prevent-app-suspension'); // M3_SPEC 3.4 缺陷4
    createWindow();
    createTray();
    connect();
    setInterval(healthCheck, HEALTH_INTERVAL_MS);
  });
  app.on('window-all-closed', (e) => {
    // 桌宠壳关窗不退出（托盘常驻）；只有托盘菜单「退出」才 quit
  });
}
