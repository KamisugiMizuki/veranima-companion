// Veranima 桌宠壳 main 进程（M3_SPEC 3.5/3.6）
// 职责：窗口管理（主窗口/日志窗口）、spawn 核心、WS 连核心、日志汇聚、health 自愈
const { app, BrowserWindow, Tray, Menu, ipcMain, powerSaveBlocker } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const CORE_WS = process.env.VERANIMA_PET_WS || 'ws://127.0.0.1:8765';
const MEMORY_LIMIT_MB = 400;      // 渲染进程 RSS 阈值（M3_SPEC 3.4 缺陷1）
const HEALTH_INTERVAL_MS = 5 * 60 * 1000; // 5min 采样
const LOG_RING_MAX = 500;         // 内存环形缓冲行数

let win = null;
let logWin = null;
let tray = null;
let ws = null;
let coreProc = null;
let ttsProc = null;  // 本地 TTS 服务子进程（Qwen3-TTS 1.7B，OpenAI 兼容）
let reconnectDelay = 1000;
let logRing = [];                 // 内存环形缓冲（转发给日志窗口）
let logFile = null;               // 文件日志句柄

// ---------- 日志（汇聚 + 落盘） ----------
function openLogFile() {
  try {
    const dir = app.getPath('userData');
    fs.mkdirSync(dir, { recursive: true });
    const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    logFile = fs.createWriteStream(path.join(dir, `veranima-${ts}.log`), { flags: 'a' });
  } catch (e) { console.error('log file open failed:', e.message); }
}

function pushLog(tag, line) {
  const ts = new Date().toISOString().slice(11, 23);
  const entry = `[${ts}] [${tag}] ${line}`;
  logRing.push(entry);
  if (logRing.length > LOG_RING_MAX) logRing.shift();
  if (logFile) logFile.write(entry + '\n');
  if (logWin && !logWin.isDestroyed()) {
    logWin.webContents.send('log-line', entry);
  }
  // 壳自身日志也走这里（health/ws 诊断）
  console.log(entry);
}

// ---------- spawn 核心（M3_SPEC 3.6 进程模型） ----------
function startCore() {
  const py = process.env.VERANIMA_PY || path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe');
  const srcDir = path.join(__dirname, '..', 'src');
  pushLog('shell', `spawning core: ${py} -m veranima.pet_server`);
  try {
    coreProc = spawn(py, ['-m', 'veranima.pet_server', '--port', '8765'], {
      cwd: path.join(__dirname, '..'),
      windowsHide: true,   // 不弹控制台窗口（用户要求单窗口启动）
      env: { ...process.env, PYTHONPATH: srcDir },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } catch (e) {
    pushLog('shell', `core spawn failed: ${e.message}`);
    scheduleCoreRestart();
    return;
  }
  coreProc.stdout.on('data', (d) => pushLog('core', d.toString().trimEnd()));
  coreProc.stderr.on('data', (d) => pushLog('core-err', d.toString().trimEnd()));
  coreProc.on('exit', (code, signal) => {
    pushLog('shell', `core exited (code=${code}, signal=${signal}); restarting in ${reconnectDelay}ms`);
    coreProc = null;
    scheduleCoreRestart();
  });
}

// ---------- spawn 本地 TTS 服务（OpenAI 兼容 /v1/audio/speech，Qwen3-TTS 1.7B） ----------
function startTTS() {
  const py = process.env.VERANIMA_PY || path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe');
  const srcDir = path.join(__dirname, '..', 'src');
  pushLog('shell', 'spawning tts server (Qwen3-TTS 1.7B, port 9880)');
  try {
    ttsProc = spawn(py, ['-m', 'veranima.tts.server', '--port', '9880'], {
      cwd: path.join(__dirname, '..'),
      windowsHide: true,   // 不弹控制台窗口
      env: { ...process.env, PYTHONPATH: srcDir },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } catch (e) {
    pushLog('shell', `tts spawn failed: ${e.message}`);
    scheduleTTSRestart();
    return;
  }
  ttsProc.stdout.on('data', (d) => pushLog('tts', d.toString().trimEnd()));
  ttsProc.stderr.on('data', (d) => pushLog('tts-err', d.toString().trimEnd()));
  ttsProc.on('exit', (code, signal) => {
    pushLog('shell', `tts exited (code=${code}, signal=${signal}); restarting in ${reconnectDelay}ms`);
    ttsProc = null;
    scheduleTTSRestart();
  });
}

function scheduleTTSRestart() {
  setTimeout(startTTS, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 30000);
}

function stopTTS() {
  if (ttsProc) { ttsProc.kill(); ttsProc = null; }
}

function scheduleCoreRestart() {
  setTimeout(startCore, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 30000); // 1s→2s→4s…上限30s
}

function stopCore() {
  if (coreProc) { coreProc.kill(); coreProc = null; }
}

// ---------- 位置持久化（airi config.json 同款，M3_SPEC 3.6） ----------
function loadWindowPos() {
  try {
    const p = path.join(app.getPath('userData'), 'win-pos.json');
    if (fs.existsSync(p)) {
      const pos = JSON.parse(fs.readFileSync(p, 'utf-8'));
      // 校验屏幕内（防止显示器变更后窗口跑到屏幕外）
      const { screen } = require('electron');
      const display = screen.getDisplayNearestPoint({ x: pos.x || 0, y: pos.y || 0 });
      const wa = display.workArea;
      if (pos.x >= wa.x - 50 && pos.x < wa.x + wa.width - 50 && pos.y >= wa.y - 50 && pos.y < wa.y + wa.height - 50) {
        return pos;
      }
    }
  } catch (e) { console.error('win-pos load failed:', e.message); }
  // 默认右下角（sakura/airi 同款：初次启动不居中）
  const { screen } = require('electron');
  const wa = screen.getPrimaryDisplay().workArea;
  return { x: wa.x + wa.width - 260, y: wa.y + wa.height - 300, width: 220, height: 260 };
}

function saveWindowPos() {
  if (!win || win.isDestroyed()) return;
  try {
    const b = win.getBounds();
    fs.writeFileSync(path.join(app.getPath('userData'), 'win-pos.json'),
      JSON.stringify({ x: b.x, y: b.y, width: b.width, height: b.height }));
  } catch (e) { /* 非关键路径，失败忽略 */ }
}

// ---------- 窗口 ----------
function createWindow() {
  const pos = loadWindowPos();
  win = new BrowserWindow({
    width: pos?.width || 220, height: pos?.height || 260,
    x: pos?.x, y: pos?.y,
    transparent: true,            // 透明背景
    frame: false,                 // 无边框
    alwaysOnTop: true,            // 置顶
    type: 'panel',                // airi 同款：置顶层级更高、不抢焦点
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
  // 鼠标：整窗捕获（不穿透）——形象可拖拽/右键/点击；sakura 同款
  // （注：Electron 的 setIgnoreMouseEvents forward 仅在 macOS 有效，Windows 穿透会锁死交互）
  // 位置持久化：move/resize → 存 userData/win-pos.json
  win.on('move', saveWindowPos);
  win.on('resize', saveWindowPos);

  win.webContents.on('render-process-gone', (e, details) => {
    console.error('[health] renderer gone:', details.reason);
    setTimeout(() => { win && win.reload(); }, 2000); // 自愈重建（M3_SPEC 3.4 缺陷2）
  });
  // airi allowClose 模式：关窗=隐藏（托盘常驻），托盘「退出」才真关
  win.on('close', (e) => { e.preventDefault(); win.hide(); });
  win.on('closed', () => { win = null; });
}

// ---------- 日志窗口（airi createReusableWindow 简化：关闭隐藏复用） ----------
function openLogWindow() {
  if (logWin && !logWin.isDestroyed()) {
    logWin.show();
    logWin.focus();
    return;
  }
  logWin = new BrowserWindow({
    width: 560, height: 600,
    title: 'Veranima 日志',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  });
  logWin.loadFile('log.html');
  logWin.on('close', (e) => { e.preventDefault(); logWin.hide(); }); // 复用模式
  logWin.on('ready-to-show', () => {
    logWin.show();
    logWin.webContents.send('log-history', logRing); // 补发历史
  });
  logWin.on('closed', () => { logWin = null; });
}

// ---------- 设置窗口（airi reusable 模式） ----------
let settingsWin = null;
let wsRequestId = 0;
const wsPending = new Map(); // id -> resolve

function wsRequest(type, data, timeoutMs = 5000) {
  return new Promise((resolve) => {
    if (!ws || ws.readyState !== 1) { resolve(null); return; }
    const id = ++wsRequestId;
    wsPending.set(id, resolve);
    ws.send(JSON.stringify({ type, data, id }));
    setTimeout(() => { if (wsPending.has(id)) { wsPending.delete(id); resolve(null); } }, timeoutMs);
  });
}

function openSettingsWindow() {
  if (settingsWin && !settingsWin.isDestroyed()) {
    settingsWin.show();
    settingsWin.focus();
    return;
  }
  settingsWin = new BrowserWindow({
    width: 600, height: 800,
    title: 'Veranima 设置',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  });
  settingsWin.loadFile('settings.html');
  settingsWin.on('close', (e) => { e.preventDefault(); settingsWin.hide(); }); // 复用
  settingsWin.on('ready-to-show', () => settingsWin.show());
  settingsWin.on('closed', () => { settingsWin = null; });
}

// 设置窗口 IPC：请求 → WS → 核心 → 响应回传
ipcMain.handle('settings-get-config', async () => {
  const resp = await wsRequest('get_config');
  if (!resp || resp.type !== 'config') return null;
  // 附角色列表（扫描 characters/ 目录；settings 角色下拉用）
  const charsDir = path.join(__dirname, '..', 'characters');
  let roles = [];
  try {
    roles = fs.readdirSync(charsDir, { withFileTypes: true })
      .filter((d) => d.isDirectory() && fs.existsSync(path.join(charsDir, d.name, 'character.json')))
      .map((d) => {
        try {
          const cj = JSON.parse(fs.readFileSync(path.join(charsDir, d.name, 'character.json'), 'utf-8'));
          const name = cj.name || cj.display_name || d.name;
          return { id: d.name, name };
        } catch { return { id: d.name, name: d.name }; }
      });
  } catch { /* characters/ 不存在 → 空列表 */ }
  return { ...resp.data, roles };
});
ipcMain.handle('settings-save-config', async (e, data) => {
  const resp = await wsRequest('save_config', data);
  return !!(resp && resp.type === 'config_saved' && resp.ok);
});
ipcMain.on('core-restart', () => { stopCore(); startCore(); });

// ---------- 托盘 ----------
// 右键菜单模板（形象右键 + 托盘共用；sakura 同款：设置/日志/重启/退出）
function buildContextMenu() {
  return Menu.buildFromTemplate([
    { label: '戳一下', click: () => { win && win.webContents.send('menu-poke'); } },
    { label: '显示/隐藏桌宠', click: () => { win ? (win.isVisible() ? win.hide() : win.show()) : createWindow(); } },
    { type: 'separator' },
    { label: '打开设置', click: () => openSettingsWindow() },
    { label: '打开日志', click: () => openLogWindow() },
    { label: '重启核心', click: () => { stopCore(); startCore(); } },
    { type: 'separator' },
    { label: '退出（全部一起停）', click: () => { stopCore(); stopTTS(); app.quit(); } },
  ]);
}

function createTray() {
  tray = new Tray(path.join(__dirname, 'assets', 'idle.png'));
  tray.setToolTip('Veranima 桌宠');
  tray.setContextMenu(buildContextMenu());
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
  // 带 id 的响应 → 回传 wsRequest pending
  if (msg.id && wsPending.has(msg.id)) {
    const resolve = wsPending.get(msg.id);
    wsPending.delete(msg.id);
    resolve(msg);
    return;
  }
  switch (msg.type) {
    case 'state':
      win && win.webContents.send('core-state', msg);
      break;
    case 'speak':
      win && win.webContents.send('speak', { text: msg.text, tags: msg.tags || [], audioB64: msg.audio_b64 || '' });
      break;
    case 'speak_chunk':
      win && win.webContents.send('speak-chunk', { text: msg.text });
      break;
    case 'speak_done':
      win && win.webContents.send('speak-done', {});
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
  if (payload && payload.type === 'drag') {
    // 拖拽：main 直接移动窗口（透明窗不能靠 -webkit-app-region）
    if (win && !win.isDestroyed()) {
      const b = win.getBounds();
      win.setBounds({ x: b.x + (payload.dx || 0), y: b.y + (payload.dy || 0), width: b.width, height: b.height });
    }
    return;
  }
  if (payload && payload.type === 'menu') {
    // 右键菜单：形象区域右键 → 弹原生菜单（sakura 同款）
    if (win && !win.isDestroyed()) {
      buildContextMenu().popup({ window: win });
    }
    return;
  }
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify(payload));
  }
});
// ---------- health：渲染进程内存监控 + 自愈 ----------
function healthCheck() {
  if (!win || win.isDestroyed()) return;
  // Electron 34+ 移除了 webContents.getProcessMemoryInfo，改用 app.getAppMetrics()
  const pid = win.webContents.getOSProcessId();
  const metric = app.getAppMetrics().find((m) => m.pid === pid);
  const rssMB = metric ? Math.round(metric.memory.workingSetSize / 1024 / 1024) : 0;
  console.log(`[health] renderer RSS: ${rssMB}MB`);
  if (rssMB > MEMORY_LIMIT_MB) {
    console.warn(`[health] RSS ${rssMB}MB > ${MEMORY_LIMIT_MB}MB, reloading renderer`);
    win.reload(); // 壳无状态，重启无损
  }
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
    openLogFile();
    startCore();                // 壳 spawn 核心（M3_SPEC 3.6）
    startTTS();                 // 壳 spawn 本地 TTS（Qwen3-TTS 1.7B）
    createWindow();
    createTray();
    connect();
    setInterval(healthCheck, HEALTH_INTERVAL_MS);
  });
  app.on('before-quit', () => { stopCore(); stopTTS(); });
  app.on('window-all-closed', (e) => {
    // 桌宠壳关窗不退出（托盘常驻）；只有托盘菜单「退出」才 quit
  });
}
