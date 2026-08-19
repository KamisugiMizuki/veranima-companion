// Veranima 桌宠壳 main 进程（R3_SPEC 1.进程与协议）
// 职责：窗口管理（主窗口/日志窗口）、spawn 核心、WS 连核心、日志汇聚、health 自愈
const { app, BrowserWindow, Tray, Menu, ipcMain, powerSaveBlocker, screen } = require('electron');

// 主动对话（L0 衔接语等）无用户手势：Chromium autoplay 策略默认拒绝
// audio.play() → 音频不播 + renderer catch 立即清气泡（实测「消失过快」）。
// 桌宠是常驻陪伴 UI，放行无手势播放。
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const CORE_WS = process.env.VERANIMA_PET_WS || 'ws://127.0.0.1:8765';
const MEMORY_LIMIT_MB = 400;      // 渲染进程 RSS 阈值（R3_SPEC 1.进程与协议）
const HEALTH_INTERVAL_MS = 5 * 60 * 1000; // 5min 采样
const LOG_RING_MAX = 500;         // 内存环形缓冲行数

let win = null;
let logWin = null;
let chatWin = null;
let activePetStreaming = false;  // 聊天窗口：桌宠回复流式进行中（chunk 合并/去重）
// 聊天记录：会话内数组 + userData/chat.json 持久化（跨重启保留）
let chatHistory = [];
const chatLogPath = () => path.join(app.getPath('userData'), 'chat.json');
function loadChatHistory() {
  try { chatHistory = JSON.parse(fs.readFileSync(chatLogPath(), 'utf-8')) || []; }
  catch { chatHistory = []; }
  if (!Array.isArray(chatHistory)) chatHistory = [];
}
function saveChatHistory() {
  try { fs.writeFileSync(chatLogPath(), JSON.stringify(chatHistory.slice(-500)), 'utf-8'); }
  catch { /* 写失败不阻塞聊天 */ }
}
function pushChat(role, text, opts = {}) {
  // 主动对话 speak 逐句推送 N 条（每条 text=整段，音频不同）→ 聊天记录
  // 去重：同角色同文本的连续消息合并（只更新时间戳），避免「重复三遍」（实测）
  const last = chatHistory[chatHistory.length - 1];
  if (last && last.role === role && last.text === text && !opts.streaming) {
    last.ts = Date.now();
    saveChatHistory();
    return last;
  }
  const m = { role, text, ts: Date.now() };
  chatHistory.push(m);
  saveChatHistory();
  // 广播给聊天窗口：历史消息直接 append；流式走 streaming/finish 增量
  if (chatWin && !chatWin.isDestroyed()) {
    chatWin.webContents.send('chat-line', { ...m, streaming: !!opts.streaming, finish: !!opts.finish });
  }
  return m;
}
let tray = null;
let ws = null;
let coreProc = null;
let ttsProc = null;  // 本地 TTS 服务子进程（Qwen3-TTS 1.7B，OpenAI 兼容）
let reconnectDelay = 1000;
let suppressCoreRestart = false;  // 预期停止标志：stopCore/退出时不自动重启
let coreRestartTimer = null;      // restartCore 定时器（防重入：多次保存只重启一次）
let logRing = [];                 // 内存环形缓冲（转发给日志窗口）
let moduleLogStreams = {};        // 模块日志流：core.log / shell.log（tts.log 走原始字节）

// ---------- 日志（汇聚 + 按模块落盘到 logs/） ----------
function openLogFile() {
  try {
    const dir = path.join(__dirname, '..', 'logs');
    fs.mkdirSync(dir, { recursive: true });
    // 按模块分开：core.log / shell.log（tts.log 在 startTTS 里用原始字节写）
    for (const name of ['core.log', 'shell.log']) {
      moduleLogStreams[name] = fs.createWriteStream(path.join(dir, name), { flags: 'a' });
    }
  } catch (e) { console.error('log file open failed:', e.message); }
}

function pushLog(tag, line) {
  const ts = new Date().toISOString().slice(11, 23);
  const entry = `[${ts}] [${tag}] ${line}`;
  logRing.push(entry);
  if (logRing.length > LOG_RING_MAX) logRing.shift();
  // 按模块分文件写本地：core-err→core.log，其余→shell.log（TTS 已走 tts.log 原始字节）
  const tagFile = tag === 'core-err' || tag === 'core' ? 'core.log' : 'shell.log';
  const stream = moduleLogStreams[tagFile];
  if (stream) stream.write(entry + '\n');
  if (logWin && !logWin.isDestroyed()) {
    logWin.webContents.send('log-line', entry);
  }
  // 壳自身日志也走这里（health/ws 诊断）
  console.log(entry);
}

// ---------- spawn 核心（R3_SPEC 1.进程与协议） ----------
// 启动核心前清理孤儿进程：占 8765/9880 且命令行含 pet_server/tts.server/api_v2.py 才杀。
// 根因：壳被强杀/双实例时 Windows 不回收 spawn 的子进程，孤儿占端口 → 新核心
// bind 失败 → 崩溃重启死循环（Errno 10048 实测）。
// 关键：只杀「父进程不是本壳」的进程——自己 spawn 的核心/TTS 父进程 = 本
// electron 主进程，跳过（否则 restartCore 的 preflight 会误杀正在跑的 TTS，
// 实测 `killed orphan pid xxx (tts)` 导致 TTS 崩溃重启）。
function preflightPorts() {
  try {
    const { execSync } = require('child_process');
    const myPid = String(process.pid);
    const out = execSync('netstat -ano', { encoding: 'buffer' }).toString('latin1');
    const lines = out.split(/\r?\n/).filter((l) => /:8765\s|:9880\s/.test(l) && /LISTENING/.test(l));
    const pids = [...new Set(lines.map((l) => l.trim().split(/\s+/).pop()).filter(Boolean))];
    for (const pid of pids) {
      if (pid === myPid) continue;
      if (isOurDescendant(pid, execSync, myPid)) continue;  // 自己 spawn 的进程树（含 uv launcher 两层）
      const info = execSync(
        `powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \\\"ProcessId=${pid}\\\").CommandLine"`,
        { encoding: 'buffer' }).toString('latin1') || '';
      if (info.includes('pet_server') || info.includes('tts.server') || info.includes('api_v2.py')) {
        execSync(`taskkill /F /PID ${pid}`, { stdio: 'ignore' });
        console.log(`[shell] killed orphan pid ${pid} (${info.includes('pet_server') ? 'core' : 'tts'})`);
      }
    }
  } catch (e) { /* netstat/powershell 失败不阻塞启动 */ }
}

// 沿父进程链向上找（最多 10 层）：祖先链含本壳主进程 → 是自己的子进程树
// （uv 的 venv python.exe 是 launcher，会再 spawn 真实解释器——监听端口的
//  是第二层，父进程是 launcher 不是壳，直接比 ParentProcessId 会漏判）
function isOurDescendant(pid, execSync, myPid) {
  let cur = pid;
  for (let i = 0; i < 10; i++) {
    if (String(cur) === myPid) return true;
    let info = '';
    try {
      info = execSync(
        `powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \\\"ProcessId=${cur}\\\").ParentProcessId"`,
        { encoding: 'buffer' }).toString('latin1') || '';
    } catch (e) { return false; }
    const m = info.match(/\d+/);
    if (!m) return false;
    cur = m[0].trim();
  }
  return false;
}

function startCore() {
  suppressCoreRestart = false;  // 新进程：崩溃仍走自动重启（restartCore 的定时器会先置 true）
  preflightPorts();  // 清孤儿（父进程非本壳的残留；自己的子进程自动跳过）
  const py = process.env.VERANIMA_PY || path.join(__dirname, '..', '.venv', 'Scripts', 'python.exe');
  const srcDir = path.join(__dirname, '..', 'src');
  pushLog('shell', `spawning core: ${py} -m veranima.pet_server`);
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
  coreProc.on('exit', (code, signal) => {
    pushLog('shell', `core exited (code=${code}, signal=${signal}); ${suppressCoreRestart ? '预期停止，不自动重启' : `restarting in ${reconnectDelay}ms`}`);
    coreProc = null;
    if (suppressCoreRestart) return;  // stopCore/退出路径：等 restartCore 定时器或退出
    scheduleCoreRestart();
  });
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
      const ts = new Date().toISOString().slice(11, 23);
      ttsLogStream.write(`[${ts}] `);
      ttsLogStream.write(line);
    }
  } catch (e) { /* 日志写入失败不阻塞 TTS */ }
}
function startTTS() {
  const gptDir = path.join(__dirname, '..', 'tts', 'gpt-sovits');
  const gptPy = path.join(gptDir, 'runtime', 'python.exe');
  pushLog('shell', 'spawning tts server (GPT-SoVITS, port 9880)');
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
  suppressCoreRestart = true;  // 预期停止：exit 回调不再自动重启（防双 spawn）
  if (coreProc) { coreProc.kill(); coreProc = null; }
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
    preflightPorts();   // 清真孤儿（父进程非本壳的残留；自己的子进程自动跳过）
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
  win.webContents.on('did-finish-load', () => { pushAvatarMap(); });
  // 位置持久化：move/resize → 存 userData/win-pos.json
  win.on('move', saveWindowPos);
  win.on('resize', saveWindowPos);

  win.webContents.on('render-process-gone', (e, details) => {
    console.error('[health] renderer gone:', details.reason);
    setTimeout(() => { win && win.reload(); }, 2000); // 自愈重建（R3_SPEC 1.进程与协议）
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
  if (ws) { try { ws.close(); } catch (e) {} ws = null; }
  connect();
});
ipcMain.on('chat-stop', () => {
  // GUI_SPEC 9：generating/speaking → 停止（转发核心 stop_speak → reply_cancelled）
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'stop_speak' }));
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
    win && win.webContents.send('avatar-map', map);
    if (chatWin && !chatWin.isDestroyed()) chatWin.webContents.send('avatar-map', map);
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

// ---------- 托盘 ----------
// 右键菜单模板（形象右键 + 托盘共用；sakura 同款：设置/日志/重启/退出）
function buildContextMenu() {
  return Menu.buildFromTemplate([
    { label: '戳一下', click: () => { win && win.webContents.send('menu-poke'); } },
    { label: '打开聊天', click: () => openChatWindow() },
    { label: '清空聊天记录', click: () => clearChatHistory() },
    { label: '显示/隐藏桌宠', click: () => { win ? (win.isVisible() ? win.hide() : win.show()) : createWindow(); } },
    { type: 'separator' },
    { label: '打开设置', click: () => openSettingsWindow() },
    { label: '打开日志', click: () => openLogWindow() },
    { label: '重启核心', click: () => { restartCore(); } },
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
    setPetStatus('online');
    pushAvatarMap();  // 启动时加载当前角色立绘映射
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
    setPetStatus('offline');
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

function handleCoreMsg(msg) {
  // 带 id 的响应 → 回传 wsRequest pending
  if (msg.id && wsPending.has(msg.id)) {
    const resolve = wsPending.get(msg.id);
    wsPending.delete(msg.id);
    resolve(msg);
    return;
  }
  const payload = msg.payload || {};
  switch (msg.type) {
    case 'state': {
      const st = payload.status || 'online';
      setPetStatus(st, { character: payload.character || '', turn_id: payload.turn_id || '' });
      break;
    }
    case 'reply_start':
      setPetStatus('generating', { character: payload.character || '' });
      break;
    case 'reply_segment': {
      // 主窗：气泡 + 音频（沿用旧 IPC 通道，renderer 无需改协议）
      win && win.webContents.send('speak', {
        text: payload.text, text_zh: payload.text_zh || '',
        tags: [], portrait: payload.portrait || '', audioB64: payload.audio_b64 || '',
      });
      // 聊天窗口：主动对话直接成条；stream_talk 后无重复（speak 只在 reply_segment 发一次）
      const display = payload.text_zh || payload.text || '';
      if (display) {
        if (!activePetStreaming) {
          activePetStreaming = true;
          chatHistory.push({ role: 'pet', text: display, ts: Date.now() });
        } else {
          const lastPet = chatHistory[chatHistory.length - 1];
          if (lastPet && lastPet.role === 'pet') lastPet.text = display;
        }
        saveChatHistory();
        if (chatWin && !chatWin.isDestroyed()) {
          chatWin.webContents.send('chat-line', { role: 'pet', text: display, streaming: true });
        }
      }
      break;
    }
    case 'reply_end':
      win && win.webContents.send('speak-done', {});
      if (activePetStreaming) {
        activePetStreaming = false;
        if (chatWin && !chatWin.isDestroyed()) {
          chatWin.webContents.send('chat-line', { role: 'pet', text: '', finish: true, ts: Date.now() });
        }
        saveChatHistory();
      }
      setPetStatus('online');
      break;
    case 'reply_error':
      // R3_SPEC 5：文字保留；错误状态在聊天窗显示
      setPetStatus('failed', { reason: payload.code || 'reply_failed' });
      if (chatWin && !chatWin.isDestroyed()) {
        chatWin.webContents.send('chat-line', { role: 'error', text: payload.code === 'tts_failed' ? '语音没有播放，文字仍可阅读' : '这条回复没有完成', retry: !!payload.recoverable });
      }
      break;
    case 'reply_cancelled':
      win && win.webContents.send('stop-speak', {});
      if (activePetStreaming) {
        activePetStreaming = false;
        saveChatHistory();
      }
      setPetStatus('online');
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
    width: 380, height: 560,
    title: 'Veranima 聊天',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  });
  chatWin.loadFile('chat.html');
  chatWin.on('close', (e) => { e.preventDefault(); chatWin.hide(); }); // 复用模式
  chatWin.on('ready-to-show', () => {
    chatWin.show();
    chatWin.webContents.send('chat-history', chatHistory);  // 补发历史
  });
  chatWin.on('closed', () => { chatWin = null; });
}
function clearChatHistory() {
  // GUI_SPEC 6：清空二次确认（防误删 500 条历史）
  if (!chatWin || chatWin.isDestroyed()) return;
  const { dialog } = require('electron');
  const btn = dialog.showMessageBoxSync(chatWin, {
    type: 'question', buttons: ['取消', '清空'], defaultId: 0, cancelId: 0,
    title: '清空聊天记录', message: '确定清空全部聊天记录吗？', detail: '此操作不可撤销。',
  });
  if (btn !== 1) return;
  chatHistory = [];
  saveChatHistory();
  if (chatWin && !chatWin.isDestroyed()) {
    chatWin.webContents.send('chat-history', []);  // 清空已打开窗口的显示
  }
  pushLog('shell', 'chat history cleared');
}
// 聊天窗口 IPC：发送 → WS stream_talk + 本地记录用户消息
ipcMain.on('chat-send', (e, text) => {
  const t = String(text || '').trim();
  if (!t) return;
  pushChat('user', t);
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: 'stream_talk', text: t }));
  } else {
    pushChat('pet', '（核心未连接）');
  }
});
ipcMain.handle('search-history', async (e, query) => wsRequest('search_history', { query: String(query || '') }));
ipcMain.handle('get-self-model', async () => wsRequest('get_self_model'));

// ---------- renderer → 核心 ----------
ipcMain.on('pet-event', (e, payload) => {
  if (payload && payload.type === 'open-chat') { openChatWindow(); return; }
  if (payload && payload.type === 'stream_talk') {
    // 主窗口发消息：记录聊天 + 转发（聊天窗口 UI 已接管主入口，保留兼容）
    const t = String(payload.text || '').trim();
    if (t) {
      pushChat('user', t);
      if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'stream_talk', text: t }));
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
    startCore();                // 壳 spawn 核心（R3_SPEC 1.进程与协议）
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
