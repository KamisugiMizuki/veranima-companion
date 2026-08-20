// Veranima 桌宠壳 main 进程（R3_SPEC 1.进程与协议）
// 职责：窗口管理（主窗口/日志窗口）、spawn 核心、WS 连核心、日志汇聚、health 自愈
const { app, BrowserWindow, Tray, Menu, ipcMain, powerSaveBlocker, screen, nativeImage } = require('electron');

// 主动对话（L0 衔接语等）无用户手势：Chromium autoplay 策略默认拒绝
// audio.play() → 音频不播 + renderer catch 立即清气泡（实测「消失过快」）。
// 桌宠是常驻陪伴 UI，放行无手势播放。
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
const { spawn, spawnSync } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');

const CORE_WS = process.env.VERANIMA_PET_WS || 'ws://127.0.0.1:8765';
const MEMORY_LIMIT_MB = 400;      // 渲染进程 RSS 阈值（R3_SPEC 1.进程与协议）
const HEALTH_INTERVAL_MS = 5 * 60 * 1000; // 5min 采样
const LOG_RING_MAX = 500;         // 内存环形缓冲行数
const MAX_CHAT_IMAGE_BYTES = 10 * 1024 * 1024;
const MAX_CHAT_IMAGE_PIXELS = 40 * 1000 * 1000;
const startupAt = Date.now();

let win = null;
let logWin = null;
let chatWin = null;
let isQuitting = false;
let activePetStreaming = false;  // 聊天窗口：桌宠回复流式进行中（chunk 合并/去重）
let activeReply = null;            // 当前 R3 reply 草稿，只在 reply_end 落盘
let pendingUserMessages = new Map();
let latestReplyTurn = 0;
let coreSessionId = '';
let speechMuted = false;
const stoppedSpeechTurns = new Set();
const cancelledReplyTurns = new Set();
const seenCoreEvents = new Set();
// 聊天记录：会话内数组 + userData/chat.json 持久化（跨重启保留）
let chatHistory = [];
let chatHistoryLoaded = false;
const chatLogPath = () => path.join(app.getPath('userData'), 'chat.json');
function loadChatHistory() {
  try { chatHistory = JSON.parse(fs.readFileSync(chatLogPath(), 'utf-8')) || []; }
  catch { chatHistory = []; }
  if (!Array.isArray(chatHistory)) chatHistory = [];
  chatHistory.forEach(restoreChatImageRefs);
  let changed = false;
  for (const message of chatHistory) {
    if (message.status === 'pending' || message.status === 'sent') {
      message.status = 'failed';
      changed = true;
    }
  }
  if (changed) saveChatHistory();
  chatHistoryLoaded = true;
}
function saveChatHistory() {
  try {
    const removed = chatHistory.length > 500 ? chatHistory.splice(0, chatHistory.length - 500) : [];
    removeUnreferencedChatImages(removed);
    const persisted = chatHistory.map((message) => {
      const copy = { ...message };
      delete copy.image_data;
      return copy;
    });
    fs.writeFileSync(chatLogPath(), JSON.stringify(persisted), 'utf-8');
  } catch { /* 写失败不阻塞聊天 */ }
}
function pushChat(role, text, opts = {}) {
  if (!chatHistoryLoaded) loadChatHistory();
  const message = {
    message_id: opts.messageId || crypto.randomUUID(),
    role,
    text: String(text || ''),
    ts: opts.ts || Date.now(),
    status: opts.status || 'complete',
    turn_id: opts.turnId || '',
    request_id: opts.requestId || '',
    images: Array.isArray(opts.images) ? opts.images.filter((x) => typeof x === 'string') : [],
    image_data: Array.isArray(opts.imageData) ? opts.imageData.filter((x) => typeof x === 'string') : [],
  };
  const last = chatHistory[chatHistory.length - 1];
  if (last && last.role === message.role && last.text === message.text &&
      message.status === 'complete' && opts.dedupe !== false) {
    last.ts = message.ts;
    if (opts.persist !== false) saveChatHistory();
    return last;
  }
  chatHistory.push(message);
  if (opts.persist !== false) saveChatHistory();
  if (opts.broadcast !== false && chatWin && !chatWin.isDestroyed()) {
    chatWin.webContents.send('chat-line', message);
  }
  return message;
}

function sendChatEvent(type, payload = {}) {
  if (chatWin && !chatWin.isDestroyed()) {
    chatWin.webContents.send('chat-event', { type, ...payload });
  }
}

function updateChatMessage(messageId, patch) {
  const message = chatHistory.find((item) => item.message_id === messageId);
  if (!message) return null;
  Object.assign(message, patch);
  saveChatHistory();
  sendChatEvent('message-update', { message });
  return message;
}

function rememberCoreEvent(msg) {
  const id = msg && msg.event_id;
  if (!id) return true;
  if (seenCoreEvents.has(id)) return false;
  seenCoreEvents.add(id);
  if (seenCoreEvents.size > 2048) {
    const first = seenCoreEvents.values().next().value;
    seenCoreEvents.delete(first);
  }
  return true;
}

function replyMatches(payload) {
  if (!activeReply) return false;
  const turn = Number(payload && payload.turn_id || 0);
  const request = String(payload && payload.request_id || '');
  if (turn && turn !== activeReply.turn_id) return false;
  if (request && activeReply.request_id && request !== activeReply.request_id) return false;
  return true;
}
let tray = null;
let ws = null;
let coreProc = null;
let ttsProc = null;  // 本地 TTS 服务子进程（GPT-SoVITS api_v2）
let sttProc = null;  // 本地 SenseVoice STT 服务子进程
let suppressSTTRestart = false;
let sttRestartTimer = null;
let sttRestartDelay = 3000;
let sttProbeInFlight = false;
let suppressTTSRestart = false;
let ttsRestartTimer = null;
let reconnectDelay = 1000;
let reconnectTimer = null;
let suppressCoreRestart = false;  // 预期停止标志：stopCore/退出时不自动重启
let coreRestartTimer = null;      // restartCore 定时器（防重入：多次保存只重启一次）
let coreStartTimer = null;
let ttsStartTimer = null;
let logRing = [];                 // 内存环形缓冲（转发给日志窗口）
let moduleLogStreams = {};        // 模块日志流：core.log / shell.log / stt.log（tts.log 走原始字节）

// ---------- 日志（汇聚 + 按模块落盘到 logs/） ----------
function openLogFile() {
  try {
    const dir = path.join(__dirname, '..', 'logs');
    fs.mkdirSync(dir, { recursive: true });
    // 按模块分开：core.log / shell.log / stt.log（tts.log 在 startTTS 里用原始字节写）
    for (const name of ['core.log', 'shell.log', 'stt.log']) {
      moduleLogStreams[name] = fs.createWriteStream(path.join(dir, name), { flags: 'a' });
    }
  } catch (e) { console.error('log file open failed:', e.message); }
}

function pushLog(tag, line) {
  const ts = new Date().toISOString().slice(11, 23);
  const entry = `[${ts}] [${tag}] ${line}`;
  logRing.push(entry);
  if (logRing.length > LOG_RING_MAX) logRing.shift();
  // 按模块分文件写本地；TTS 已走 tts.log 原始字节。
  const tagFile = tag === 'core-err' || tag === 'core' ? 'core.log' : tag === 'stt' ? 'stt.log' : 'shell.log';
  const stream = moduleLogStreams[tagFile];
  if (stream) stream.write(entry + '\n');
  if (logWin && !logWin.isDestroyed()) {
    logWin.webContents.send('log-line', entry);
  }
  // 壳自身日志也走这里（health/ws 诊断）
  console.log(entry);
}

function startupMark(label) {
  pushLog('startup', `${label} +${Date.now() - startupAt}ms`);
}

// ---------- spawn 核心（R3_SPEC 1.进程与协议） ----------
// 端口孤儿清理由 scripts/run_pet.py 统一负责；Electron 不再同步执行
// netstat/PowerShell，避免重复扫描阻塞首屏并产生控制台闪窗。
function startCore() {
  if (isQuitting) return;
  suppressCoreRestart = false;  // 新进程：崩溃仍走自动重启（restartCore 的定时器会先置 true）
  const py = process.env.VERANIMA_PY || path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe');
  const srcDir = path.join(__dirname, '..', 'src');
  pushLog('shell', `spawning core: ${py} -m veranima.pet_server`);
  startupMark('core-spawn');
  try {
    coreProc = spawn(py, ['-m', 'veranima.pet_server', '--port', '8765'], {
      cwd: path.join(__dirname, '..'),
      windowsHide: true,   // 不弹控制台窗口（用户要求单窗口启动）
      env: { ...process.env, PYTHONPATH: srcDir, PYTHONIOENCODING: 'utf-8' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } catch (e) {
    pushLog('shell', `core spawn failed: ${e.message}`);
    scheduleCoreRestart();
    return;
  }
  coreProc.stdout.on('data', (d) => pushLog('core', d.toString().trimEnd()));
  coreProc.stderr.on('data', (d) => pushLog('core-err', d.toString().trimEnd()));
  coreProc.on('error', (err) => {
    pushLog('shell', `core process error: ${err.message}`);
    coreProc = null;
    if (!isQuitting) scheduleCoreRestart();
  });
  coreProc.on('exit', (code, signal) => {
    pushLog('shell', `core exited (code=${code}, signal=${signal}); ${suppressCoreRestart ? '预期停止，不自动重启' : `restarting in ${reconnectDelay}ms`}`);
    coreProc = null;
    if (suppressCoreRestart) return;  // stopCore/退出路径：等 restartCore 定时器或退出
    scheduleCoreRestart();
  });
}

function startSTT() {
  if (isQuitting || sttProc || sttProbeInFlight) return;
  if (!localFeatureEnabled('stt', true)) { pushLog('shell', 'stt disabled by config'); return; }
  const configuredBase = String(localFeatureValue('stt', 'base_url', 'http://127.0.0.1:9890/v1'));
  if (!/^https?:\/\/(?:127\.0\.0\.1|localhost):9890(?:\/|$)/i.test(configuredBase)) {
    pushLog('shell', `remote STT configured; local service not started: ${configuredBase}`);
    return;
  }
  suppressSTTRestart = false;
  const healthUrl = configuredBase.replace(/\/(?:v1(?:\/audio\/transcriptions)?)?\/?$/, '') + '/health';
  sttProbeInFlight = true;
  fetch(healthUrl, { signal: AbortSignal.timeout(1500) })
    .then(async (response) => {
      let health = null;
      if (response.ok) {
        try { health = await response.json(); } catch (_) { /* not our JSON health endpoint */ }
      }
      if (health?.ok === true && health?.provider === 'sensevoice') {
        sttRestartDelay = 3000;
        pushLog('shell', 'stt health ready; reusing existing service');
      } else {
        suppressSTTRestart = true;
        pushLog('shell', 'stt port 9890 conflict: health endpoint is not Veranima SenseVoice; restart suppressed');
      }
    })
    .catch(() => spawnLocalSTT())
    .finally(() => { sttProbeInFlight = false; });
}
function spawnLocalSTT() {
  if (isQuitting || sttProc || suppressSTTRestart) return;
  if (!localFeatureEnabled('stt', true)) { pushLog('shell', 'stt disabled by config'); return; }
  const configuredBase = String(localFeatureValue('stt', 'base_url', 'http://127.0.0.1:9890/v1'));
  if (!/^https?:\/\/(?:127\.0\.0\.1|localhost):9890(?:\/|$)/i.test(configuredBase)) {
    pushLog('shell', `remote STT configured; local service not started: ${configuredBase}`);
    return;
  }
  const root = path.join(__dirname, '..');
  const py = process.env.VERANIMA_STT_PY || path.join(root, 'tts', 'gpt-sovits', 'runtime', 'python.exe');
  const script = path.join(root, 'scripts', 'run_stt_server.py');
  const overlay = path.join(root, 'data', 'stt-runtime', 'site');
  const configuredModel = String(localFeatureValue('stt', 'model_path', 'data/models/sensevoice-small'));
  const modelPath = path.isAbsolute(configuredModel) ? configuredModel : path.join(root, configuredModel);
  const configuredVad = String(localFeatureValue('stt', 'vad_model_path', 'tts/gpt-sovits/tools/asr/models/speech_fsmn_vad_zh-cn-16k-common-pytorch'));
  const vadModelPath = path.isAbsolute(configuredVad) ? configuredVad : path.join(root, configuredVad);
  if (!fs.existsSync(script) || !fs.existsSync(py) || !fs.existsSync(overlay) || !fs.existsSync(modelPath) || !fs.existsSync(vadModelPath)) {
    pushLog('shell', `stt disabled: missing runtime/script/overlay/model/vad (${modelPath}; ${vadModelPath})`); return;
  }
  pushLog('shell', 'spawning STT SenseVoice server (port from config, default 9890)');
  const env = { ...process.env, PYTHONIOENCODING: 'utf-8' };
  delete env.PYTHONUTF8;
  let child;
  try {
    child = spawn(py, [script], {
      cwd: root, windowsHide: true, env: { ...env, PYTHONPATH: '' }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    sttProc = child;
  } catch (error) {
    pushLog('shell', `stt spawn failed: ${error.message}`);
    scheduleSTTRestart();
    return;
  }
  child.stdout.on('data', (d) => pushLog('stt', d.toString().trimEnd()));
  child.stderr.on('data', (d) => pushLog('stt', d.toString().trimEnd()));
  child.on('error', (err) => {
    pushLog('shell', `stt process error: ${err.message}`);
    if (sttProc === child) sttProc = null;
    scheduleSTTRestart();
  });
  child.on('exit', (code, signal) => {
    pushLog('shell', `stt exited (code=${code}, signal=${signal})`);
    if (sttProc === child) sttProc = null;
    scheduleSTTRestart();
  });
}
function scheduleSTTRestart() {
  if (suppressSTTRestart || isQuitting || sttRestartTimer) return;
  const delay = sttRestartDelay;
  pushLog('shell', `restarting STT in ${delay}ms`);
  sttRestartTimer = setTimeout(() => { sttRestartTimer = null; startSTT(); }, delay);
  sttRestartDelay = Math.min(sttRestartDelay * 2, 60000);
}
function stopSTT() {
  suppressSTTRestart = true;
  if (sttRestartTimer) { clearTimeout(sttRestartTimer); sttRestartTimer = null; }
  if (sttProc) { terminateProcessTree(sttProc); sttProc = null; }
}

// ---------- spawn 本地 TTS 服务（GPT-SoVITS api_v2.py，端口 9880） ----------
// 2026-08-19：Qwen3-TTS 1.7B → GPT-SoVITS（实时率 ~0.5x，快 3 倍）
// 编码：参考 sakura tts_service.py 的做法——不用 -I（隔离模式忽略
// PYTHONIOENCODING → 输出编码随系统环境漂移）；显式移除 PYTHONUTF8（用户
// 环境若设了 1 会让 Python 3.7+ 强制 UTF-8 输出，与 PYTHONIOENCODING 混用
// 时 stdout/stderr 编码不一致）+ PYTHONIOENCODING=utf-8 → 输出固定 UTF-8。
// 日志：窗口显示跨 chunk 解码易乱码（实测多轮），改为原始字节直接写本地
// 文件 logs/tts.log——编辑器自动检测编码，永远正确。窗口只保留 shell 日志。
const ttsLogPath = path.join(__dirname, '..', 'logs', 'tts.log');
let ttsLogStream = null;
let ttsLineBuf = Buffer.alloc(0);  // 跨 chunk 的行缓冲（按 \n 切行，多字节安全）
function appendTtsLog(buf) {
  try {
    if (!ttsLogStream) {
      fs.mkdirSync(path.dirname(ttsLogPath), { recursive: true });
      ttsLogStream = fs.createWriteStream(ttsLogPath, { flags: 'a', encoding: null });
    }
    // 每行首加 [HH:MM:SS.mmm] 时间戳（ASCII，不破坏编码；跨 chunk 切断的
    // 多字节留在缓冲里等下个 chunk，按 \n 切行不会切断字节序列）
    ttsLineBuf = Buffer.concat([ttsLineBuf, buf]);
    let nl;
    while ((nl = ttsLineBuf.indexOf(10)) !== -1) {  // \n
      const line = ttsLineBuf.subarray(0, nl + 1);
      ttsLineBuf = ttsLineBuf.subarray(nl + 1);
      if (line.toString('utf8').includes('Uvicorn running')) startupMark('tts-api-ready');
      const ts = new Date().toISOString().slice(11, 23);
      ttsLogStream.write(`[${ts}] `);
      ttsLogStream.write(line);
    }
  } catch (e) { /* 日志写入失败不阻塞 TTS */ }
}
function startTTS() {
  if (isQuitting) return;
  suppressTTSRestart = false;
  const gptDir = path.join(__dirname, '..', 'tts', 'gpt-sovits');
  const gptPy = path.join(gptDir, 'runtime', 'python.exe');
  pushLog('shell', 'spawning tts server (GPT-SoVITS, port 9880)');
  startupMark('tts-spawn');
  try {
    const ttsEnv = { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONPATH: '' };
    delete ttsEnv.PYTHONUTF8;  // 强制 stdout/stderr 统一 UTF-8（sakura 同款）
    ttsProc = spawn(gptPy, ['api_v2.py', '-a', '127.0.0.1', '-p', '9880'], {
      cwd: gptDir,
      windowsHide: true,   // 不弹控制台窗口
      env: ttsEnv,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } catch (e) {
    pushLog('shell', `tts spawn failed: ${e.message}`);
    scheduleTTSRestart();
    return;
  }
  ttsProc.stdout.on('data', (d) => appendTtsLog(d));   // 原始字节写 logs/tts.log
  ttsProc.stderr.on('data', (d) => appendTtsLog(d));   // 同上（含 tqdm 进度）
  ttsProc.on('error', (err) => {
    pushLog('shell', `tts process error: ${err.message}`);
    ttsProc = null;
    if (!isQuitting) scheduleTTSRestart();
  });
  ttsProc.on('exit', (code, signal) => {
    pushLog('shell', `tts exited (code=${code}, signal=${signal}); ${suppressTTSRestart ? '预期停止，不自动重启' : `restarting in ${reconnectDelay}ms`}`);
    ttsProc = null;
    if (suppressTTSRestart) return;
    scheduleTTSRestart();
  });
}

function scheduleTTSRestart() {
  if (isQuitting || suppressTTSRestart || ttsRestartTimer) return;
  ttsRestartTimer = setTimeout(() => { ttsRestartTimer = null; startTTS(); }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 30000);
}

function stopTTS() {
  suppressTTSRestart = true;
  if (ttsRestartTimer) { clearTimeout(ttsRestartTimer); ttsRestartTimer = null; }
  if (ttsProc) { terminateProcessTree(ttsProc); ttsProc = null; }
}

function terminateProcessTree(proc) {
  if (!proc || proc.killed) return;
  if (process.platform === 'win32') {
    // uv 的 python.exe 会再派生真实解释器；退出路径同步递归清理，不能让 app.quit 抢在 taskkill 前完成。
    try {
      spawnSync('taskkill', ['/F', '/T', '/PID', String(proc.pid)], {
        windowsHide: true, stdio: 'ignore', timeout: 10000,
      });
    } catch (e) { /* fallback below */ }
    try { if (!proc.killed) proc.kill(); } catch (e) { /* already gone */ }
  } else {
    try { proc.kill(); } catch (e) { /* already gone */ }
  }
}

function scheduleCoreRestart() {
  if (isQuitting || coreRestartTimer) return;
  coreRestartTimer = setTimeout(() => {
    coreRestartTimer = null;
    startCore();
  }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 30000); // 1s→2s→4s…上限30s
}

function stopCore() {
  suppressCoreRestart = true;  // 预期停止：exit 回调不再自动重启（防双 spawn）
  if (coreProc) { terminateProcessTree(coreProc); coreProc = null; }
}

function prepareQuit() {
  if (isQuitting) return;
  isQuitting = true;
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  if (coreRestartTimer) { clearTimeout(coreRestartTimer); coreRestartTimer = null; }
  if (coreStartTimer) { clearTimeout(coreStartTimer); coreStartTimer = null; }
  if (ttsStartTimer) { clearTimeout(ttsStartTimer); ttsStartTimer = null; }
  stopDragPoll();
  if (ws) {
    try { ws.close(); } catch (e) { /* already closed */ }
    ws = null;
  }
  if (tray) {
    try { tray.destroy(); } catch (e) { /* already destroyed */ }
    tray = null;
  }
  stopCore();
  stopTTS();
  stopSTT();
}

function quitApplication() {
  prepareQuit();
  app.quit();
}

// 重启核心（设置保存后）：kill 旧进程 → 等端口释放 → spawn 新的。
// 防重入：多次保存只保留最后一个定时器（否则多个 startCore 排队 → 双核心抢 8765）。
// 防双 spawn：stopCore 置 suppress 后，exit 事件不再触发崩溃重启路径；
// 只有本定时器 spawn 一次。
function restartCore() {
  stopCore();
  saveWindowPos();  // 重启前落盘窗口位置/尺寸（用户要求）
  if (coreRestartTimer) clearTimeout(coreRestartTimer);
  const waitMs = 3000;  // 旧 python 进程退出 + 端口释放通常 <3s
  coreRestartTimer = setTimeout(() => {
    coreRestartTimer = null;
    startCore();        // startCore 内部 suppressCoreRestart = false
  }, waitMs);
}

// ---------- 位置持久化（airi config.json 同款，R3_SPEC 3.6） ----------
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
  // 角色立绘映射：renderer 加载完成后推一次（connect 的 ws open 可能早于
  // renderer 监听注册——事件竞态导致 avatar-map 丢失，立绘显示 zima 默认图）
  win.webContents.on('did-finish-load', () => {
    startupMark('ui-ready');
    pushAvatarMap();
  });
  // 位置持久化：move/resize → 存 userData/win-pos.json
  win.on('move', saveWindowPos);
  win.on('resize', saveWindowPos);

  win.webContents.on('render-process-gone', (e, details) => {
    console.error('[health] renderer gone:', details.reason);
    setTimeout(() => { win && win.reload(); }, 2000); // 自愈重建（R3_SPEC 1.进程与协议）
  });
  // 普通点叉=隐藏；托盘退出时 isQuitting=true，允许窗口真正销毁。
  win.on('close', (e) => {
    if (isQuitting) return;
    e.preventDefault();
    win.hide();
  });
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
  logWin.on('close', (e) => {
    if (isQuitting) return;
    e.preventDefault();
    logWin.hide();
  }); // 复用模式
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
    if (!ws || ws.readyState !== 1 || isQuitting) { resolve(null); return; }
    const id = ++wsRequestId;
    const timer = setTimeout(() => {
      const entry = wsPending.get(id);
      if (entry) { wsPending.delete(id); entry.resolve(null); }
    }, timeoutMs);
    wsPending.set(id, { resolve, timer });
    try {
      ws.send(JSON.stringify({ type, data, id }));
    } catch (e) {
      clearTimeout(timer); wsPending.delete(id); resolve(null);
    }
  });
}

function rejectWsPending() {
  for (const [id, entry] of wsPending) {
    clearTimeout(entry.timer);
    entry.resolve(null);
    wsPending.delete(id);
  }
}

function failPendingChats() {
  for (const message of pendingUserMessages.values()) {
    updateChatMessage(message.message_id, { status: 'failed' });
  }
  pendingUserMessages.clear();
  if (activeReply) finishReply(activeReply, 'failed');
}

function disconnectSocket(socket) {
  if (ws !== socket) return false;
  ws = null;
  rejectWsPending();
  failPendingChats();
  setPetStatus('offline');
  if (!isQuitting) scheduleReconnect();
  return true;
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
  settingsWin.on('close', (e) => {
    if (isQuitting) return;
    e.preventDefault();
    settingsWin.hide();
  }); // 复用
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
          const payload = cj.spec === 'chara_card_v3' ? (cj.data || {}) : cj;
          const name = payload.name || payload.display_name || d.name;
          return { id: d.name, name };
        } catch { return { id: d.name, name: d.name }; }
      });
  } catch { /* characters/ 不存在 → 空列表 */ }
  return { ...resp.data, roles };
});
ipcMain.handle('settings-save-config', async (e, data) => {
  const resp = await wsRequest('save_config', data);
  const ok = !!(resp && resp.type === 'config_saved' && resp.ok);
  if (ok) {
    pushAvatarMap();  // 角色可能变了 → 刷新立绘映射
    const h = data && data.pet && data.pet.avatar_height;
    if (h) win && win.webContents.send('avatar-height', Number(h));  // 立绘尺寸
  }
  return ok;
});
ipcMain.on('core-restart', () => { restartCore(); });
ipcMain.on('pet-reconnect', () => {
  // R3_SPEC 4：聊天窗 offline/failed → 重试连接（重置退避立即重连）
  reconnectDelay = 1000;
  if (ws) { rejectWsPending(); try { ws.close(); } catch (e) {} ws = null; }
  connect();
});
ipcMain.on('pet-hit-shape', (e, payload) => applyPetHitShape(payload || {}));
ipcMain.on('chat-stop', () => {
  // 取消整轮生成（与 speech-stop 的本地音频停止分离）。
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'cancel_reply' }));
});
ipcMain.on('pet-resize', (e, dim) => {
  // GUI_SPEC 4.2：气泡增长 → 窗口向上扩（顶边随高度变化，锚点=形象底边稳定）。
  // width/height 同时接受，避免 renderer 的布局宽度与原生窗口宽度分叉。
  const requestedWidth = Math.max(120, Math.round(Number(dim && dim.width) || win.getBounds().width));
  const h = Math.max(120, Math.round(Number(dim && dim.height) || 206));
  const b = win.getBounds();
  if (b.height === h && b.width === requestedWidth) return;
  const center = b.x + b.width / 2;
  win.setBounds({
    x: Math.round(center - requestedWidth / 2),
    y: b.y - (h - b.height),
    width: requestedWidth,
    height: h,
  });
});

// 角色立绘映射：读当前角色卡 avatar.expressions → {标签: 绝对路径} → renderer
// （R2_SPEC 2：表情标签驱动；角色切换后立绘跟着换，不再写死 assets/）
function pushAvatarMap() {
  try {
    // 优先本地读 config.yaml（不等核心启动——启动 ~7s 内立绘应是 yuki 而非 zima 默认图）
    const cardPath = localCharacterCard() || '';
    if (cardPath) { pushAvatarMapFrom(cardPath); return; }
    wsRequest('get_config').then((resp) => {
      const cp = resp && resp.data && resp.data.character_card;
      if (cp) pushAvatarMapFrom(cp);
    }).catch((e) => console.warn('[shell] avatar map failed:', e.message));
  } catch (e) { console.warn('[shell] avatar map failed:', e.message); }
}

// 从 config.yaml 直接读 character_card（顶层字符串键）
function localCharacterCard() {
  try {
    const cfg = fs.readFileSync(path.join(__dirname, '..', 'config', 'config.yaml'), 'utf-8');
    const m = cfg.match(/^character_card:\s*["']?([^"'\s#]+)/m);
    return m ? m[1] : '';
  } catch { return ''; }
}

function localFeatureEnabled(section, fallback) {
  const value = localFeatureValue(section, 'enabled', fallback);
  return typeof value === 'boolean' ? value : fallback;
}

function localFeatureValue(section, key, fallback) {
  try {
    const cfg = fs.readFileSync(path.join(__dirname, '..', 'config', 'config.yaml'), 'utf-8');
    const block = cfg.match(new RegExp(`^${section}:\\s*\\r?\\n((?:[ \\t]+.*(?:\\r?\\n|$))*)`, 'm'));
    if (!block) return fallback;
    const match = block[1].match(new RegExp(`^\\s+${key}:\\s*(?:"([^"]*)"|'([^']*)'|([^#\\r\\n]*))`, 'im'));
    if (!match) return fallback;
    const raw = String(match[1] ?? match[2] ?? match[3] ?? '').trim();
    if (/^(true|false)$/i.test(raw)) return raw.toLowerCase() === 'true';
    if (/^-?\d+(?:\.\d+)?$/.test(raw)) return Number(raw);
    return raw;
  } catch { return fallback; }
}

function pushAvatarMapFrom(cardPath) {
  try {
    const full = path.join(__dirname, '..', cardPath);
    const cj = JSON.parse(fs.readFileSync(full, 'utf-8'));
    const data = cj.spec === 'chara_card_v3' ? (cj.data || {}) : cj;
    const ext = (data.extensions || {}).veranima || data.veranima || {};
    const exprs = (ext.avatar || {}).expressions || {};
    const map = {};
    for (const [label, rel] of Object.entries(exprs)) {
      map[label] = require('url').pathToFileURL(path.join(path.dirname(full), rel)).href;
    }
    // 主窗 + 聊天窗都收（聊天窗桌宠头像用角色卡立绘，不再写死 zima idle）
    const profile = { name: data.name || data.display_name || 'Veranima' };
    win && win.webContents.send('avatar-map', map);
    win && win.webContents.send('character-profile', profile);
    if (chatWin && !chatWin.isDestroyed()) {
      chatWin.webContents.send('avatar-map', map);
      chatWin.webContents.send('chat-profile', profile);
    }
    // 启动时同步立绘尺寸（设置页 avatar_height；默认 200）
    const ah = localAvatarHeight();
    if (ah > 0) win && win.webContents.send('avatar-height', Number(ah));
    console.log('[shell] avatar map updated:', Object.keys(map).length, 'expressions');
  } catch (e) { console.warn('[shell] avatar map failed:', e.message); }
}

function localAvatarHeight() {
  try {
    const cfg = fs.readFileSync(path.join(__dirname, '..', 'config', 'config.yaml'), 'utf-8');
    const m = cfg.match(/avatar_height:\s*(\d+)/);
    return m ? parseInt(m[1], 10) : 0;
  } catch { return 0; }
}

function applyPetHitShape(payload = {}) {
  if (!win || win.isDestroyed() || typeof win.setShape !== 'function') return;
  if (process.platform !== 'win32' && process.platform !== 'linux') return;
  try {
    const src = String(payload.src || '');
    if (!src) return;
    const filePath = src.startsWith('file:')
      ? require('url').fileURLToPath(src)
      : path.resolve(src);
    const projectRoot = path.resolve(__dirname, '..');
    const resolved = path.resolve(filePath);
    if (!resolved.toLowerCase().startsWith(projectRoot.toLowerCase() + path.sep)) {
      pushLog('shell', `hit-shape rejected outside project: ${resolved}`);
      return;
    }
    const image = nativeImage.createFromPath(resolved);
    if (image.isEmpty()) return;
    const sourceSize = image.getSize();
    const bitmap = image.toBitmap();
    const left = Math.max(0, Math.round(Number(payload.x) || 0));
    const top = Math.max(0, Math.round(Number(payload.y) || 0));
    const width = Math.max(1, Math.round(Number(payload.width) || sourceSize.width));
    const height = Math.max(1, Math.round(Number(payload.height) || sourceSize.height));
    // 形状裁剪本身也会影响可见轮廓；按显示像素采样，避免 128 网格造成 2px 台阶。
    // 半透明抗锯齿边缘纳入命中区，但不改变图像本身的 alpha。
    const threshold = Math.max(1, Math.min(255, Number(payload.threshold ?? 4)));
    const cols = Math.min(width, 512);
    const rows = Math.min(height, 512);
    const spansByRow = [];
    for (let gy = 0; gy < rows; gy += 1) {
      const y0 = Math.floor(gy * height / rows);
      const y1 = Math.max(y0 + 1, Math.floor((gy + 1) * height / rows));
      const sy = Math.min(sourceSize.height - 1,
        Math.floor((gy + 0.5) * sourceSize.height / rows));
      const spans = [];
      let runStart = -1;
      for (let gx = 0; gx < cols; gx += 1) {
        const x0 = Math.floor(gx * width / cols);
        const x1 = Math.max(x0 + 1, Math.floor((gx + 1) * width / cols));
        const sx = Math.min(sourceSize.width - 1,
          Math.floor((gx + 0.5) * sourceSize.width / cols));
        const alpha = bitmap[(sy * sourceSize.width + sx) * 4 + 3];
        const opaque = alpha >= threshold;
        if (opaque && runStart < 0) runStart = gx;
        if ((!opaque || gx === cols - 1) && runStart >= 0) {
          const end = opaque && gx === cols - 1 ? gx + 1 : gx;
          // 原生 shape 是二值边界；向外扩 1 DIP，把硬裁剪放到完全透明区，
          // 保留 Chromium 已计算出的半透明抗锯齿边缘。
          const startPx = Math.max(0, Math.floor(runStart * width / cols) - 1);
          const endPx = Math.min(width, Math.floor(end * width / cols) + 1);
          const expandedY0 = Math.max(0, y0 - 1);
          const expandedY1 = Math.min(height, y1 + 1);
          spans.push({ x: left + startPx, y: top + expandedY0,
            width: Math.max(1, endPx - startPx), height: expandedY1 - expandedY0 });
          runStart = -1;
        }
      }
      spansByRow.push(spans);
    }
    const rects = [];
    for (const spans of spansByRow) {
      for (const span of spans) {
        const previous = rects[rects.length - 1];
        if (previous && previous.x === span.x && previous.width === span.width &&
            previous.y + previous.height === span.y) {
          previous.height += span.height;
        } else {
          rects.push({ ...span });
        }
      }
    }
    // 气泡是 DOM 交互区，不属于 PNG alpha；由 renderer 显式补充其窗口内矩形。
    const extraRects = Array.isArray(payload.rects) ? payload.rects.map((r) => ({
      x: Math.max(0, Math.round(Number(r.x) || 0)),
      y: Math.max(0, Math.round(Number(r.y) || 0)),
      width: Math.max(1, Math.round(Number(r.width) || 0)),
      height: Math.max(1, Math.round(Number(r.height) || 0)),
    })).filter((r) => r.width > 0 && r.height > 0) : [];
    const allRects = rects.concat(extraRects);
    // 轮廓复杂时保留 alpha 轮廓并优先保留显式 UI 矩形；失败时退回立绘盒。
    const bounded = allRects.length > 4000
      ? rects.slice(0, Math.max(0, 4000 - extraRects.length)).concat(extraRects.slice(-4000))
      : allRects;
    win.setShape(bounded.length ? bounded : [{ x: left, y: top, width, height }]);
  } catch (e) {
    pushLog('shell', `hit-shape failed: ${e.message}`);
  }
}

// ---------- 托盘 ----------
// 右键菜单模板（形象右键 + 托盘共用；sakura 同款：设置/日志/重启/退出）
function buildContextMenu() {
  return Menu.buildFromTemplate([
    { label: '戳一下', click: () => {
      if (ws && ws.readyState === 1) {
        ws.send(JSON.stringify({ type: 'poke', request_id: crypto.randomUUID() }));
      }
    } },
    { label: '打开聊天', click: () => openChatWindow() },
    { label: '清空聊天记录', click: () => clearChatHistory() },
    { label: '显示/隐藏桌宠', click: () => { win ? (win.isVisible() ? win.hide() : win.show()) : createWindow(); } },
    { type: 'separator' },
    { label: '打开设置', click: () => openSettingsWindow() },
    { label: '打开日志', click: () => openLogWindow() },
    { label: '重启核心', click: () => { restartCore(); } },
    { type: 'separator' },
    { label: '退出（全部一起停）', click: () => quitApplication() },
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
  let socket;
  try {
    socket = new WebSocket(CORE_WS);
    ws = socket;
  } catch (e) {
    scheduleReconnect();
    return;
  }
  socket.on('open', () => {
    if (ws !== socket) return;
    console.log('[ws] connected to core');
    startupMark('core-ws-connected');
    reconnectDelay = 1000;
    setPetStatus('online');
    pushAvatarMap();  // 启动时加载当前角色立绘映射
  });
  socket.on('message', (data) => {
    if (ws !== socket) return;
    try {
      const msg = JSON.parse(data.toString());
      handleCoreMsg(msg);
    } catch (e) { console.error('[ws] bad msg:', e.message); }
  });
  socket.on('close', () => {
    if (!disconnectSocket(socket)) return;
    console.log('[ws] closed');
  });
  socket.on('error', (e) => {
    if (ws !== socket) return;
    console.error('[ws] error:', e.message);
    disconnectSocket(socket);
    socket.close();
  });
}

function scheduleReconnect() {
  if (isQuitting || reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 30000); // 1s→2s→4s…上限30s
}

// ---------- 核心消息 → renderer（R3 协议，R3_SPEC 1/2） ----------
// 状态机：connecting → online → generating → speaking → online
//        ↘ offline ↔ reconnecting；任意生成态 → failed/cancelled → online
let petStatus = 'connecting';

function broadcastToWindows(channel, payload) {
  if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
  if (chatWin && !chatWin.isDestroyed()) chatWin.webContents.send(channel, payload);
}

function setPetStatus(status, extra = {}) {
  petStatus = status;
  broadcastToWindows('core-state', { connected: status !== 'offline', status, ...extra });
}

function updateChatMessage(messageId, patch) {
  const message = chatHistory.find((item) => item.message_id === messageId);
  if (!message) return null;
  Object.assign(message, patch);
  saveChatHistory();
  sendChatEvent('message-update', { message });
  return message;
}

function rememberCoreEvent(msg) {
  const id = msg && msg.event_id;
  if (!id) return true;
  if (seenCoreEvents.has(id)) return false;
  seenCoreEvents.add(id);
  if (seenCoreEvents.size > 2048) {
    seenCoreEvents.delete(seenCoreEvents.values().next().value);
  }
  return true;
}

function replyMatches(payload) {
  if (!activeReply) return false;
  const turn = Number(payload && payload.turn_id || 0);
  const request = String(payload && payload.request_id || '');
  if (turn && turn !== activeReply.turn_id) return false;
  if (request && activeReply.request_id && request !== activeReply.request_id) return false;
  return true;
}

function beginReply(payload) {
  const turn = Number(payload.turn_id || 0);
  const request = String(payload.request_id || '');
  if (turn && (turn < latestReplyTurn || cancelledReplyTurns.has(turn))) return false;
  if (activeReply && turn && turn < activeReply.turn_id) return false;
  activeReply = {
    message_id: crypto.randomUUID(),
    turn_id: turn,
    request_id: request,
    text: '',
    segments: [],
    started_at: Date.now(),
  };
  latestReplyTurn = Math.max(latestReplyTurn, turn);
  const pending = pendingUserMessages.get(request);
  if (pending) {
    updateChatMessage(pending.message_id, { status: 'sent', turn_id: turn });
  }
  sendChatEvent('reply-start', {
    turn_id: turn, request_id: request, message_id: activeReply.message_id,
  });
  setPetStatus('generating', { turn_id: turn, request_id: request });
  return true;
}

function finishReply(payload, status = 'complete') {
  if (!replyMatches(payload)) return false;
  const reply = activeReply;
  const finalStatus = reply.errorCode ? 'failed' : status;
  const message = reply.text
    ? pushChat('pet', reply.text, {
        status: finalStatus, turnId: reply.turn_id, requestId: reply.request_id,
        messageId: reply.message_id, broadcast: false, dedupe: false,
      })
    : null;
  sendChatEvent('reply-end', {
    message, status: finalStatus, turn_id: reply.turn_id, request_id: reply.request_id,
  });
  pendingUserMessages.delete(reply.request_id);
  activeReply = null;
  setPetStatus('online');
  return true;
}

function handleCoreMsg(msg) {
  if (msg.session_id && msg.session_id !== coreSessionId) {
    coreSessionId = msg.session_id;
    latestReplyTurn = 0;
    cancelledReplyTurns.clear();
    stoppedSpeechTurns.clear();
    seenCoreEvents.clear();
    if (activeReply) {
      const pending = pendingUserMessages.get(activeReply.request_id);
      if (pending) updateChatMessage(pending.message_id, { status: 'failed' });
      activeReply = null;
    }
    win && win.webContents.send('stop-speak', {});
  }
  // 带 id 的配置/搜索响应先交给 pending promise。
  if (msg.id && wsPending.has(msg.id)) {
    const entry = wsPending.get(msg.id);
    wsPending.delete(msg.id);
    clearTimeout(entry.timer);
    entry.resolve(msg);
    return;
  }
  if (!rememberCoreEvent(msg)) return;
  const payload = msg.payload || {};
  switch (msg.type) {
    case 'state':
      setPetStatus(payload.status || 'online', {
        character: payload.character || '', turn_id: payload.turn_id || '',
      });
      break;
    case 'reply_start':
      beginReply(payload);
      break;
    case 'reply_segment': {
      if (!activeReply && payload.turn_id !== undefined && !beginReply(payload)) break;
      if (!replyMatches(payload)) break; // 丢弃迟到/旧回合
      const display = payload.text_zh || payload.text || '';
      activeReply.text += display;
      activeReply.segments.push({
        text: payload.text || '', text_zh: payload.text_zh || '',
        tone: payload.tone || '', portrait: payload.portrait || '',
        audio_b64: payload.audio_b64 || '',
      });
      win && win.webContents.send('speak', {
        text: payload.text, text_zh: payload.text_zh || '', tags: [],
        portrait: payload.portrait || '',
        audioB64: speechMuted || stoppedSpeechTurns.has(activeReply.turn_id)
          ? '' : (payload.audio_b64 || ''),
        turn_id: activeReply.turn_id, request_id: activeReply.request_id,
      });
      sendChatEvent('reply-segment', {
        message_id: activeReply.message_id, text: display,
        segment: activeReply.segments[activeReply.segments.length - 1],
        turn_id: activeReply.turn_id, request_id: activeReply.request_id,
      });
      setPetStatus('speaking', {
        turn_id: activeReply.turn_id, request_id: activeReply.request_id,
      });
      break;
    }
    case 'reply_end':
      win && win.webContents.send('speak-done', {});
      finishReply(payload);
      break;
    case 'reply_error': {
      if (!replyMatches(payload)) break;
      if (payload.code === 'tts_failed') {
        sendChatEvent('speech-error', {
          code: payload.code, turn_id: activeReply.turn_id,
          request_id: activeReply.request_id,
        });
        break;
      }
      activeReply.errorCode = payload.code || 'reply_failed';
      const pending = pendingUserMessages.get(activeReply.request_id);
      if (pending) updateChatMessage(pending.message_id, { status: 'failed' });
      sendChatEvent('reply-error', {
        code: payload.code || 'reply_failed', recoverable: !!payload.recoverable,
        turn_id: activeReply.turn_id, request_id: activeReply.request_id,
      });
      setPetStatus('failed', { reason: payload.code || 'reply_failed' });
      break;
    }
    case 'reply_cancelled': {
      if (!activeReply || (payload.turn_id && !replyMatches(payload))) break;
      win && win.webContents.send('stop-speak', {});
      const request = activeReply && activeReply.request_id;
      const pending = pendingUserMessages.get(request);
      if (pending) updateChatMessage(pending.message_id, { status: 'cancelled' });
      cancelledReplyTurns.add(Number(payload.turn_id || 0));
      if (cancelledReplyTurns.size > 128) {
        cancelledReplyTurns.delete(cancelledReplyTurns.values().next().value);
      }
      if (activeReply.text) {
        finishReply(payload, 'cancelled');
      } else {
        sendChatEvent('reply-cancelled', {
          turn_id: payload.turn_id, request_id: payload.request_id,
        });
        activeReply = null;
      }
      pendingUserMessages.delete(request);
      setPetStatus('online');
      break;
    }
    case 'speech_stopped':
      win && win.webContents.send('stop-speak', {});
      sendChatEvent('speech-stopped', payload);
      break;
    case 'bubble':
      win && win.webContents.send('bubble', { text: msg.text });
      break;
    default:
      console.warn('[ws] unknown msg type:', msg.type);
  }
}

// ---------- 聊天窗口（QQ 风格对话框，独立窗口，复用模式 hide 不销毁） ----------
function openChatWindow() {
  loadChatHistory();
  if (chatWin && !chatWin.isDestroyed()) { chatWin.show(); chatWin.focus(); return; }
  chatWin = new BrowserWindow({
    width: 420, height: 640, minWidth: 360, minHeight: 480,
    title: 'Veranima 聊天',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  });
  chatWin.loadFile('chat.html');
  chatWin.webContents.on('did-finish-load', () => {
    pushAvatarMap();
    chatWin.webContents.send('chat-history', chatHistory);
  });
  chatWin.on('close', (e) => {
    if (isQuitting) return;
    e.preventDefault();
    chatWin.webContents.send('chat-hidden');
    chatWin.hide();
  }); // 复用模式
  chatWin.on('ready-to-show', () => {
    chatWin.show();
  });
  chatWin.on('closed', () => { chatWin = null; });
}
function clearChatHistory() {
  // 只清 Electron 窗口历史；SQLite 原始消息/长期记忆证据不删除。
  const { dialog } = require('electron');
  const parent = chatWin && !chatWin.isDestroyed() ? chatWin
    : (win && !win.isDestroyed() ? win : null);
  const options = {
    type: 'question', buttons: ['取消', '清空'], defaultId: 0, cancelId: 0,
    title: '清空窗口记录', message: '确定清空这个聊天窗口的记录吗？',
    detail: '长期记忆和可审计的原始消息不会被删除。',
  };
  const btn = parent ? dialog.showMessageBoxSync(parent, options)
    : dialog.showMessageBoxSync(options);
  if (btn !== 1) return false;
  chatHistory = [];
  fs.rmSync(path.join(app.getPath('userData'), 'chat-images'), { recursive: true, force: true });
  pendingUserMessages.clear();
  activeReply = null;
  saveChatHistory();
  if (chatWin && !chatWin.isDestroyed()) {
    chatWin.webContents.send('chat-history', []);
  }
  pushLog('shell', 'chat history cleared');
  return true;
}
function validateChatImages(images) {
  if (!Array.isArray(images) || images.length > 4) return { ok: false, reason: '图片数量超限' };
  const decoded = [];
  const dir = path.join(app.getPath('userData'), 'chat-images');
  for (const value of images) {
    const match = /^data:(image\/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\r\n]+)$/.exec(String(value || ''));
    if (!match) return { ok: false, reason: '图片格式无效' };
    const encoded = match[2].replace(/[\r\n]/g, '');
    if (encoded.length > Math.ceil(MAX_CHAT_IMAGE_BYTES / 3) * 4) return { ok: false, reason: '图片大小无效' };
    let raw;
    try {
      raw = Buffer.from(encoded, 'base64');
      if (raw.toString('base64') !== encoded) return { ok: false, reason: '图片编码无效' };
    } catch { return { ok: false, reason: '图片编码无效' }; }
    if (!raw.length || raw.length > MAX_CHAT_IMAGE_BYTES) return { ok: false, reason: '图片大小无效' };
    const magic = (match[1] === 'image/png' && raw.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])))
      || (match[1] === 'image/jpeg' && raw.subarray(0, 3).equals(Buffer.from([255,216,255])))
      || (match[1] === 'image/gif' && (raw.subarray(0, 6).toString() === 'GIF87a' || raw.subarray(0, 6).toString() === 'GIF89a'))
      || (match[1] === 'image/webp' && raw.subarray(0, 4).toString() === 'RIFF' && raw.subarray(8, 12).toString() === 'WEBP');
    if (!magic) return { ok: false, reason: '图片内容与类型不匹配' };
    let size;
    try { size = nativeImage.createFromBuffer(raw).getSize(); } catch { return { ok: false, reason: '图片内容损坏' }; }
    if (!size.width || !size.height || size.width * size.height > MAX_CHAT_IMAGE_PIXELS) {
      return { ok: false, reason: '图片像素尺寸过大' };
    }
    const ext = match[1].split('/')[1].replace('jpeg', 'jpg');
    decoded.push({ value, raw, name: `${crypto.randomUUID()}.${ext}` });
  }
  const written = [];
  try {
    fs.mkdirSync(dir, { recursive: true });
    for (const item of decoded) {
      const file = path.join(dir, item.name);
      fs.writeFileSync(file, item.raw, { flag: 'wx' });
      written.push(file);
    }
  } catch (error) {
    written.forEach((file) => fs.rmSync(file, { force: true }));
    return { ok: false, reason: `图片保存失败: ${error.message}` };
  }
  const refs = decoded.map((item) => `chat-images/${item.name}`);
  return { ok: true, values: decoded.map((item) => item.value), refs, previews: chatImageRefs(refs, false) };
}
function chatImageFile(ref) {
  const dir = path.join(app.getPath('userData'));
  const rel = String(ref || '');
  if (!rel.startsWith('chat-images/') || rel.includes('..')) return null;
  const file = path.resolve(dir, rel);
  if (!file.toLowerCase().startsWith(dir.toLowerCase() + path.sep) || !fs.existsSync(file)) return null;
  const ext = path.extname(file).toLowerCase();
  const type = ext === '.png' ? 'image/png' : ext === '.jpg' || ext === '.jpeg' ? 'image/jpeg'
    : ext === '.gif' ? 'image/gif' : ext === '.webp' ? 'image/webp' : '';
  return type ? { file, type } : null;
}
function chatImageRefs(refs, asDataUrl) {
  return (Array.isArray(refs) ? refs : []).map((ref) => {
    const resolved = chatImageFile(ref);
    if (!resolved) return '';
    return asDataUrl
      ? `data:${resolved.type};base64,${fs.readFileSync(resolved.file).toString('base64')}`
      : pathToFileURL(resolved.file).href;
  }).filter(Boolean);
}
function removeUnreferencedChatImages(messages) {
  const retained = new Set(chatHistory.flatMap((message) => message.images || []));
  for (const message of messages || []) {
    for (const ref of message.images || []) {
      if (retained.has(ref)) continue;
      const resolved = chatImageFile(ref);
      if (resolved) fs.rmSync(resolved.file, { force: true });
    }
  }
}
function restoreChatImageRefs(message) {
  if (!message || !Array.isArray(message.images)) return message;
  message.image_data = chatImageRefs(message.images, false);
  return message;
}
function dispatchChat(text, images = [], messageId = '', existingRefs = []) {
  const t = String(text || '').trim();
  let message = messageId && chatHistory.find((item) => item.message_id === messageId);
  const imageResult = existingRefs.length
    ? { ok: true, values: images, refs: existingRefs, previews: chatImageRefs(existingRefs, false) }
    : validateChatImages(images);
  if (!imageResult.ok) return imageResult;
  if (imageResult.values.length !== imageResult.refs.length) return { ok: false, reason: '历史图片缺失' };
  if (!t && !imageResult.values.length) return { ok: false, reason: 'empty' };
  const requestId = crypto.randomUUID();
  if (message) {
    message.text = t;
    message.status = 'pending';
    message.request_id = requestId;
    message.ts = Date.now();
    message.images = imageResult.refs;
    message.image_data = imageResult.previews;
    saveChatHistory();
    sendChatEvent('message-update', { message });
  } else {
    message = pushChat('user', t, {
      status: 'pending', requestId, dedupe: false,
      images: imageResult.refs,
      imageData: imageResult.previews,
    });
  }
  pendingUserMessages.set(requestId, message);
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: 'stream_talk', text: t, images: imageResult.values, request_id: requestId }));
  } else {
    updateChatMessage(message.message_id, { status: 'failed' });
    pendingUserMessages.delete(requestId);
    return { ok: false, reason: 'offline', message };
  }
  return { ok: true, request_id: requestId, message };
}

// 聊天窗口 IPC：发送/重试 → 带 request_id 的 WS 请求。
ipcMain.handle('chat-send', (e, payload) => {
  if (typeof payload === 'string') return dispatchChat(payload);
  return dispatchChat(payload && payload.text, payload && payload.images);
});
ipcMain.handle('stt-transcribe', async (e, payload) => {
  const raw = Buffer.from(payload && payload.audio || []);
  if (!raw.length || raw.length > 20 * 1024 * 1024) return '';
  try {
    if (!localFeatureEnabled('stt', true)) return '';
    const base = String(localFeatureValue('stt', 'base_url', 'http://127.0.0.1:9890/v1')).replace(/\/$/, '');
    const endpoint = base.endsWith('/audio/transcriptions') ? base
      : `${base}${base.endsWith('/v1') ? '' : '/v1'}/audio/transcriptions`;
    const model = String(localFeatureValue('stt', 'model', 'sensevoice-small'));
    const language = String(localFeatureValue('stt', 'language', 'auto'));
    const timeoutMs = Math.max(1000, Number(localFeatureValue('stt', 'timeout', 120)) * 1000);
    const apiKey = String(localFeatureValue('stt', 'api_key', ''));
    const form = new FormData();
    form.append('file', new Blob([raw], { type: 'audio/webm' }), String(payload.filename || 'voice.webm'));
    form.append('model', model); form.append('language', language);
    const response = await fetch(endpoint, {
      method: 'POST', body: form, signal: AbortSignal.timeout(timeoutMs),
      headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
    });
    if (!response.ok) throw new Error(`STT HTTP ${response.status}`);
    sttRestartDelay = 3000;
    return String((await response.json()).text || '').trim();
  } catch (error) {
    pushLog('shell', `stt transcribe failed: ${error.message}`);
    startSTT();
    return '';
  }
});
ipcMain.handle('chat-clear', async () => clearChatHistory());
ipcMain.handle('chat-retry', (e, messageId) => {
  const old = chatHistory.find((item) => item.message_id === messageId && item.role === 'user');
  return old
    ? dispatchChat(old.text, chatImageRefs(old.images, true), old.message_id, old.images)
    : { ok: false, reason: 'missing' };
});
ipcMain.handle('search-history', async (e, query) => wsRequest('search_history', { query: String(query || '') }));
ipcMain.handle('get-self-model', async () => wsRequest('get_self_model'));
ipcMain.handle('chat-get-state', async () => ({
  status: petStatus, muted: speechMuted, history: chatHistory,
  active_reply: activeReply && {
    message_id: activeReply.message_id, turn_id: activeReply.turn_id,
    request_id: activeReply.request_id, text: activeReply.text,
  },
}));
ipcMain.on('speech-stop', () => {
  if (activeReply && activeReply.turn_id) {
    stoppedSpeechTurns.add(activeReply.turn_id);
    if (stoppedSpeechTurns.size > 128) {
      stoppedSpeechTurns.delete(stoppedSpeechTurns.values().next().value);
    }
  }
  win && win.webContents.send('stop-speak', {});
  sendChatEvent('speech-stopped', {});
});
ipcMain.on('speech-mute', (e, muted) => {
  speechMuted = !!muted;
  if (speechMuted) win && win.webContents.send('stop-speak', {});
  sendChatEvent('speech-muted', { muted: speechMuted });
});

// ---------- renderer → 核心 ----------
ipcMain.on('pet-event', (e, payload) => {
  if (payload && payload.type === 'open-chat') { openChatWindow(); return; }
  if (payload && payload.type === 'stream_talk') {
    // 主窗口发消息：记录聊天 + 转发（聊天窗口 UI 已接管主入口，保留兼容）
    const t = String(payload.text || '').trim();
    if (t) {
      dispatchChat(t);
    }
    return;
  }
  if (payload && (payload.type === 'drag-start' || payload.type === 'drag-end')) {
    pushLog('shell', `[pet-event] ${payload.type}`);  // 【诊断】拖拽信号
    // 拖拽：main 进程轮询全局鼠标位置移动窗口（renderer mousemove 在透明置顶窗不可靠；
    // sakura 同款方案——跨屏、跨窗口层级稳定）
    if (payload.type === 'drag-start') {
      startDragPoll();
    } else {
      stopDragPoll();
    }
    return;
  }
  if (payload && payload.type === 'pet-hit-shape') {
    applyPetHitShape(payload);
    return;
  }
  if (payload && payload.type === 'fit-window') {
    // 立绘按比例显示：窗口尺寸跟随（高度不变，宽度按立绘比例自适应）
    if (win && !win.isDestroyed() && payload.width > 0 && payload.height > 0) {
      const b = win.getBounds();
      win.setBounds({ x: b.x, y: b.y, width: payload.width, height: payload.height });
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

// ---------- 拖拽轮询（main 进程全局鼠标） ----------
let dragTimer = null;
let lastDragPoint = null;

function startDragPoll() {
  if (dragTimer) return;
  const p = screen.getCursorScreenPoint();
  lastDragPoint = { x: p.x, y: p.y };
  dragTimer = setInterval(() => {
    if (!win || win.isDestroyed()) { stopDragPoll(); return; }
    const p2 = screen.getCursorScreenPoint();
    const dx = p2.x - lastDragPoint.x;
    const dy = p2.y - lastDragPoint.y;
    lastDragPoint = { x: p2.x, y: p2.y };
    if (dx || dy) {
      const b = win.getBounds();
      win.setBounds({ x: b.x + dx, y: b.y + dy, width: b.width, height: b.height });
    }
  }, 16); // ~60Hz
}

function stopDragPoll() {
  if (dragTimer) { clearInterval(dragTimer); dragTimer = null; }
  lastDragPoint = null;
}
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
const gotLock = app.requestSingleInstanceLock(); // R3_SPEC 1 缺陷6
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    win && win.isMinimized() && win.restore();
    win && win.show();
  });
  app.whenReady().then(() => {
    powerSaveBlocker.start('prevent-app-suspension'); // R3_SPEC 1 缺陷4
    openLogFile();
    startupMark('electron-ready');
    createWindow();             // 先显示桌宠，后台服务不阻塞首屏
    createTray();
    setTimeout(() => startCore(), 0);
    setTimeout(() => startTTS(), 0);
    setTimeout(() => startSTT(), 0);
    connect();
    setInterval(healthCheck, HEALTH_INTERVAL_MS);
  });
  app.on('before-quit', () => { prepareQuit(); });
  app.on('window-all-closed', (e) => {
    // 桌宠壳关窗不退出（托盘常驻）；只有托盘菜单「退出」才 quit
  });
}
