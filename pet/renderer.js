// Veranima 桌宠 renderer（M3_SPEC 3.5）
// 职责：四态切换、气泡显示、形象区域交互（拖拽/戳一下）、穿透切换
const avatar = document.getElementById('avatar');
const bubble = document.getElementById('bubble');
const connDot = document.getElementById('conn');

let currentState = 'idle';
let bubbleTimer = null;
let isDragging = false;
let dragStart = null;

const STATES = ['idle', 'speaking', 'thinking', 'sleeping'];
// M4 表情标签 → 立绘文件（与角色卡 avatar.expressions 同步；M4_SPEC 2.2）
const EXPRESSION_FILES = {
  '站立待机': 'stand', '开心脸红': 'happy', '疑惑': 'puzzled', '难过': 'sad', '惊讶': 'surprised',
};

// ---------- 四态切换 ----------
function setState(s) {
  if (!STATES.includes(s)) s = 'idle';
  currentState = s;
  avatar.src = `assets/${s}.png`;
}

// ---------- 气泡 ----------
function showBubble(text, ms = 3000) {
  bubble.textContent = text;
  bubble.classList.add('show');
  clearTimeout(bubbleTimer);
  bubbleTimer = setTimeout(() => bubble.classList.remove('show'), ms);
}

// ---------- 核心消息 ----------
window.pet.onCoreState((m) => {
  if (m.connected === true) connDot.classList.add('ok');
  else if (m.connected === false) connDot.classList.remove('ok');
  // 核心推送的完整状态可扩展（情绪/依恋度），MVP 只关心连接
});

window.pet.onSpeak((m) => {
  setState('speaking');
  showBubble(m.text || '…');
  // M4 表情标签驱动：tags 携带 portrait 标签 → 映射表情图（M4_SPEC 2.4）
  if (m.tags && m.tags.length > 0) {
    const file = EXPRESSION_FILES[m.tags[0]];
    if (file) avatar.src = `assets/${file}.png`;
  }
  // TTS 播放（M3_SPEC 3.2）：有 audioB64 播放真实语音，否则模拟 2.5s
  if (m.audioB64) {
    if (currentAudio) currentAudio.pause();
    currentAudio = new Audio(`data:audio/wav;base64,${m.audioB64}`);
    currentAudio.onended = () => { currentAudio = null; if (currentState === 'speaking') setState('idle'); };
    currentAudio.play().catch(() => { /* 播放失败（无音频设备）→ 按文本时长回 idle */ });
  } else {
    // 模拟播放时长后回 idle（MVP 无真实音频；TTS 接入后由 audio.onended 控制）
    setTimeout(() => { if (currentState === 'speaking') setState('idle'); }, 2500);
  }
});

// TTS 打断：停止当前播放（M3 3.2）
let currentAudio = null;
window.pet.onStopSpeak(() => {
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  setState('idle');
});

window.pet.onBubble((m) => showBubble(m.text || ''));

// 流式打字机（DESIGN 4.13）：逐句追加，speak_done 定稿
let streamText = '';
window.pet.onSpeakChunk((m) => {
  setState('speaking');
  streamText += (m.text || '');
  showBubble(streamText, 10000); // 长气泡暂不消失，done 时重置
});
window.pet.onSpeakDone(() => {
  if (streamText) {
    showBubble(streamText, 5000);
    streamText = '';
    setTimeout(() => setState('idle'), 3000);
  }
});

// ---------- 形象区域交互（整窗捕获：拖拽/戳一下/右键；sakura 同款） ----------
const pet = document.getElementById('pet');

// 拖拽移动窗口（透明窗不能用 -webkit-app-region；renderer 上报**增量** → main setBounds）
avatar.addEventListener('mousedown', (e) => {
  isDragging = true;
  dragStart = { x: e.screenX, y: e.screenY };  // 同时作为 last 位置
});
window.addEventListener('mousemove', (e) => {
  if (isDragging && dragStart) {
    // 增量（相对上一次事件位置）——main 在 setBounds 里逐帧累加，不能发累计位移
    const dx = e.screenX - dragStart.x;
    const dy = e.screenY - dragStart.y;
    dragStart = { x: e.screenX, y: e.screenY };
    window.pet.sendEvent({ type: 'drag', dx, dy });
  }
});
window.addEventListener('mouseup', () => {
  isDragging = false;
  dragStart = null;
});

// 左键单击 = 戳一下（触发核心互动；M3_SPEC 3.5 交互）
function poke() {
  setState('thinking');
  showBubble('……', 1500); // thinking 气泡
  window.pet.sendEvent({ type: 'poke' });
  setTimeout(() => setState('idle'), 1500);
}
avatar.addEventListener('click', () => {
  if (isDragging) return;
  poke();
});
// 右键菜单「戳一下」同逻辑
window.pet.onMenuPoke(() => poke());

// 右键菜单（sakura 同款：形象上右键弹原生菜单；参考 sakura 托盘菜单）
window.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  window.pet.sendEvent({ type: 'menu' });
});
