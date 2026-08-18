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

// TTS 播放队列：逐句合成快（GPT-SoVITS ~1s/句）时新音频不能打断当前播放
// （实测第一句没放完第二句就 pause 掉 → 跳句）。串行播放：队列为空立即播，
// 否则排队等 onended。气泡跟随音频：轮到播放才显示对应文本（显示时长=音频
// 时长），避免前几句气泡在排队时 3s 就消失、只有最后一句停留。
let audioQueue = [];
let currentAudio = null;
function playAudio(b64, text) {
  const a = new Audio(`data:audio/wav;base64,${b64}`);
  a.onended = () => {
    currentAudio = null;
    hideBubble();
    const next = audioQueue.shift();
    if (next) {
      playNext(next);
    } else if (currentState === 'speaking') {
      setState('idle');
    }
  };
  const item = { audio: a, text };
  if (currentAudio) {
    audioQueue.push(item);
  } else {
    playNext(item);
  }
}
function playNext(item) {
  currentAudio = item.audio;
  // 气泡跟随播放：显示本句文本，音频时长=气泡时长
  if (item.text) {
    bubble.textContent = item.text;
    bubble.classList.add('show');
  }
  item.audio.play().catch(() => {
    currentAudio = null; audioQueue = [];
    hideBubble();
    /* 播放失败（无音频设备）→ 按文本时长回 idle */
  });
}
function hideBubble() {
  bubble.classList.remove('show');
  clearTimeout(bubbleTimer);
}

window.pet.onSpeak((m) => {
  setState('speaking');
  // 双语（M5_SPEC 由岐日语）：ja 播 TTS，zh 显示气泡
  const displayText = m.text_zh || m.text || '…';
  // M4 表情标签驱动：tags 携带 portrait 标签 → 映射表情图（M4_SPEC 2.4）
  if (m.tags && m.tags.length > 0) {
    applyExpression(m.tags[0]);
  }
  // TTS 播放（M3_SPEC 3.2）：有 audioB64 播放真实语音（串行队列，气泡跟随
  // 音频显示），否则模拟 2.5s 显示
  if (m.audioB64) {
    playAudio(m.audioB64, displayText);
  } else {
    showBubble(displayText);
  }
});

// TTS 打断：停止当前播放并清空队列（M3 3.2）
window.pet.onStopSpeak(() => {
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  audioQueue = [];
  hideBubble();
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
      // 2026-08-19：点击形象 → 打开 QQ 风格独立聊天窗口（替代旧内嵌输入框）
      window.pet.sendEvent({ type: 'open-chat' });
    }
    downPos = null;
  }
});

// 右键菜单（sakura 同款：形象上右键弹原生菜单；参考 sakura 托盘菜单）
window.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  window.pet.sendEvent({ type: 'menu' });
});

// ---------- 聊天入口（2026-08-19：QQ 风格独立聊天窗口；点击形象 → 打开） ----------
// 旧的内嵌输入框（openChatInput）已废弃：交互统一走 chat.html 独立窗口
// （显示聊天记录 + 流式回复，main 进程持有历史持久化）。点击判定在 mouseup
// 拖动逻辑里（L183），此处不再重复注册 click。空块保留结构注释。
