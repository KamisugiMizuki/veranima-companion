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
  // 模拟播放时长后回 idle（MVP 无真实音频；M3b TTS 接入后由 stop_speak 控制）
  setTimeout(() => { if (currentState === 'speaking') setState('idle'); }, 2500);
});

window.pet.onStopSpeak(() => setState('idle'));

window.pet.onBubble((m) => showBubble(m.text || ''));

// ---------- 形象区域交互（穿透 ↔ 捕获） ----------
// 默认整个窗口点击穿透；鼠标移入形象区域时恢复捕获（可拖拽/点击）
const pet = document.getElementById('pet');
pet.addEventListener('mouseenter', () => window.pet.setIgnoreMouse(false));
pet.addEventListener('mouseleave', () => {
  if (!isDragging) window.pet.setIgnoreMouse(true);
});

// 拖拽移动窗口（MVP：利用 electron 的 -webkit-app-region 不可用于透明窗的局部；
// 简单方案：拖拽时让 main 移动窗口——renderer 发事件，main 处理）
avatar.addEventListener('mousedown', (e) => {
  isDragging = true;
  dragStart = { x: e.screenX, y: e.screenY };
});
window.addEventListener('mousemove', (e) => {
  if (isDragging && dragStart) {
    const dx = e.screenX - dragStart.x;
    const dy = e.screenY - dragStart.y;
    window.pet.sendEvent({ type: 'drag', dx, dy });
  }
});
window.addEventListener('mouseup', () => {
  isDragging = false;
  dragStart = null;
  if (!pet.matches(':hover')) window.pet.setIgnoreMouse(true);
});

// 左键单击 = 戳一下（触发核心互动；M3_SPEC 3.5 交互）
avatar.addEventListener('click', () => {
  if (isDragging) return;
  setState('thinking');
  window.pet.sendEvent({ type: 'poke' });
  setTimeout(() => setState('idle'), 1500);
});

// 右键菜单交给 main 的托盘；窗口内右键 = 恢复穿透
window.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  window.pet.setIgnoreMouse(true);
});
