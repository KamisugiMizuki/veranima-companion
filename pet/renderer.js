// Veranima 桌宠 renderer（M3_SPEC 3.5）
// 职责：四态切换、气泡显示、形象区域交互（拖拽/点击弹输入框/右键菜单）
const avatar = document.getElementById('avatar');
const bubble = document.getElementById('bubble');
const connDot = document.getElementById('conn');

let currentState = 'idle';
let bubbleTimer = null;
let isDragging = false;
let dragStart = null;

const STATES = ['idle', 'speaking', 'thinking', 'sleeping'];
// M4 表情标签 → 立绘文件（默认 zima assets/；角色卡 avatar.expressions 到达后优先）
const EXPRESSION_FILES = {
  '站立待机': 'stand', '开心脸红': 'happy', '疑惑': 'puzzled', '难过': 'sad', '惊讶': 'surprised',
};
// 角色立绘映射：{表情标签: 绝对路径}（main 读角色卡 avatar.expressions 推来）
let avatarMap = null;
window.pet.onAvatarMap((map) => {
  avatarMap = map || null;
  if (avatarMap) applyExpression(Object.keys(avatarMap)[0] || '');
});
// 表情 → 立绘路径：角色卡优先，fallback 默认 assets/
function applyExpression(label) {
  if (avatarMap && avatarMap[label]) { avatar.src = avatarMap[label]; return true; }
  const f = EXPRESSION_FILES[label];
  if (f) { avatar.src = `assets/${f}.png`; return true; }
  return false;
}

// ---------- 立绘尺寸（按原图比例；高度优先固定，宽度自适应） ----------
// 用户可调：设置页 avatar_height（px）→ main 下发；默认 200
let avatarHeight = 200;
window.pet.onAvatarHeight((h) => {
  avatarHeight = h > 0 ? h : 200;
  fitAvatar();
});
// 立绘加载完成 → 按原图比例缩放：高度固定、宽度按比例，窗口宽度自适应（高度不变）
function fitAvatar() {
  const img = avatar;
  if (!img.complete || !img.naturalWidth) { img.onload = fitAvatar; return; }
  const h = avatarHeight;
  const w = Math.round(h * img.naturalWidth / img.naturalHeight);
  img.style.width = w + 'px';
  img.style.height = h + 'px';
  document.getElementById('pet').style.width = (w + 20) + 'px';
  document.getElementById('pet').style.height = (h + 60) + 'px';
  // 通知 main：窗口高度保持 h+60（切换立绘时高度不变，宽度跟随比例）
  window.pet.sendEvent({ type: 'fit-window', width: w + 20, height: h + 60 });
}
avatar.addEventListener('load', fitAvatar);

// ---------- 四态切换 ----------
function setState(s) {
  if (!STATES.includes(s)) s = 'idle';
  currentState = s;
  // 立绘：角色卡表情映射优先（闲置/微笑…），fallback assets/ 状态图（zima 默认）
  if (s === 'idle') { applyExpression('闲置') || (avatar.src = `assets/idle.png`); return; }
  if (s === 'speaking') { applyExpression('微笑') || (avatar.src = `assets/speaking.png`); return; }
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
  // 双语（M5_SPEC 由岐日语）：ja 播 TTS，zh 显示气泡
  const displayText = m.text_zh || m.text || '…';
  showBubble(displayText);
  // M4 表情标签驱动：tags 携带 portrait 标签 → 映射表情图（M4_SPEC 2.4）
  if (m.tags && m.tags.length > 0) {
    applyExpression(m.tags[0]);
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

// ---------- 形象区域交互（整窗捕获：拖拽/点击/右键；sakura 同款） ----------
// 注意：不能声明 const pet——preload 已 exposeInMainWorld('pet')，同名全局声明
// 会 SyntaxError 导致整个 renderer 不执行（立绘/点击/拖拽全挂的根因）

// 拖拽：mousedown/mouseup 只发 start/end 信号，main 进程用 setInterval 轮询
// 全局鼠标位置移动窗口（Windows 透明置顶窗的 renderer mousemove 投递不可靠——
// 实测 mousedown 到达但 mousemove 丢失，窗口不动；轮询方案 sakura 同款，跨屏可靠）
let downPos = null;
avatar.addEventListener('mousedown', (e) => {
  isDragging = true;
  downPos = { x: e.screenX, y: e.screenY };
  window.pet.sendEvent({ type: 'drag-start' });
});
window.addEventListener('mouseup', (e) => {
  if (isDragging) {
    isDragging = false;
    window.pet.sendEvent({ type: 'drag-end' });
    // 点击判定：按下/松开距离 < 5px = 点击（不用 click 事件——拖动窗口后
    // 按下/松开位置不同，click 会丢失；实测 drag-start 轮询哪怕移动 1px 就没 click）
    if (downPos && Math.abs(e.screenX - downPos.x) < 5 && Math.abs(e.screenY - downPos.y) < 5) {
      openChatInput();
    }
    downPos = null;
  }
});

// 右键菜单（sakura 同款：形象上右键弹原生菜单；参考 sakura 托盘菜单）
window.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  window.pet.sendEvent({ type: 'menu' });
});

// ---------- 聊天输入框（用户指定交互：点击形象 → 弹输入框 → LLM 回复气泡+TTS） ----------
let chatInput = null;
function openChatInput() {
  if (chatInput) { chatInput.focus(); return; }
  chatInput = document.createElement('input');
  chatInput.id = 'chat-input';
  chatInput.placeholder = '对她说点什么…';
  chatInput.style.cssText = [
    'position:absolute; bottom:36px; left:8px; width:204px; padding:6px 10px;',
    'border:1px solid #d8dce3; border-radius:8px; font-size:13px;',
    'background:rgba(255,255,255,.97); outline:none; z-index:10;',
  ].join('');
  document.getElementById('pet').appendChild(chatInput);
  chatInput.focus();
  const send = () => {
    const text = chatInput.value.trim();
    if (!text) return;
    window.pet.sendEvent({ type: 'stream_talk', text });  // 核心流式回复（speak_chunk→done）
    setState('thinking');
    showBubble('……', 2000);
    chatInput.remove();
    chatInput = null;
  };
  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') send();
    if (e.key === 'Escape') { chatInput.remove(); chatInput = null; }
  });
}
