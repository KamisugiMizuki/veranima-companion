// 聊天窗口 renderer：QQ 风格消息列表 + 输入框（独立窗口，聊天记录持久化在 main）
const msgsEl = document.getElementById('msgs');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const statusText = document.getElementById('statusText');
const charName = document.getElementById('charName');
let activePetMsg = null;   // 流式进行中的桌宠气泡元素（speak_chunk 追加）
let petAvatarSrc = 'assets/idle.png';  // 角色卡立绘（main 推来）；默认 zima
window.pet.onAvatarMap((map) => {
  if (map && Object.keys(map).length) {
    petAvatarSrc = map[Object.keys(map)[0]] || petAvatarSrc;  // 首表情作头像
  }
});

function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function tsStr(ts) {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
function appendMsg(m) {
  const wrap = document.createElement('div');
  wrap.className = `msg ${m.role}`;
  if (m.role === 'error') wrap.setAttribute('role', 'alert');  // GUI_SPEC 11
  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  if (m.role === 'pet') {
    const img = document.createElement('img');
    img.src = petAvatarSrc;
    img.onerror = () => { avatar.textContent = '由'; };
    avatar.appendChild(img);
  } else {
    avatar.textContent = '我';
  }
  const body = document.createElement('div');
  body.className = 'body';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = m.text;
  body.appendChild(bubble);
  if (m.ts) {
    const t = document.createElement('div');
    t.className = 'ts';
    t.textContent = tsStr(m.ts);
    body.appendChild(t);
  }
  wrap.appendChild(avatar);
  wrap.appendChild(body);
  msgsEl.appendChild(wrap);
  msgsEl.scrollTop = msgsEl.scrollHeight;
  return bubble;
}
// 流式：chunk 追加到进行中的桌宠气泡（无则新建）
function appendChunk(text) {
  if (!activePetMsg) {
    const bubble = appendMsg({ role: 'pet', text: '' });
    activePetMsg = { wrap: bubble.parentElement.parentElement, bubble };
  }
  const cur = activePetMsg.bubble.textContent;
  activePetMsg.bubble.textContent = cur ? cur + text : text;
  msgsEl.scrollTop = msgsEl.scrollHeight;
}
function finishPetMsg(ts) {
  if (!activePetMsg) return;
  // 补时间戳
  const t = document.createElement('div');
  t.className = 'ts';
  t.textContent = tsStr(ts || Date.now());
  activePetMsg.wrap.appendChild(t);
  activePetMsg = null;
}

// main → 本窗口
// 历史分批渲染（GUI_SPEC 6：数据一次读取，UI 每批 40 条让出事件循环；
// 刷新/清空时 renderGeneration 使旧批次失效）
const RENDER_BATCH = 40;
let renderGeneration = 0;
function renderHistory(list, generation) {
  if (!list || !list.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = '还没有聊天记录，戳戳她说句话吧';
    msgsEl.appendChild(empty);
    return;
  }
  let i = 0;
  const step = () => {
    if (generation !== renderGeneration) return; // 旧批次失效
    const end = Math.min(i + RENDER_BATCH, list.length);
    for (; i < end; i++) appendMsg(list[i]);
    msgsEl.scrollTop = msgsEl.scrollHeight;
    if (i < list.length) setTimeout(step, 0);
  };
  step();
}
window.pet.onChatHistory((list) => {
  renderGeneration += 1;
  msgsEl.innerHTML = '';
  activePetMsg = null;
  renderHistory(list || [], renderGeneration);
});
window.pet.onChatLine((m) => {
  if (m.role === 'pet' && m.streaming) { appendChunk(m.text); return; }
  if (m.role === 'pet' && m.finish) { finishPetMsg(m.ts); return; }
  appendMsg(m);
});
// 清空确认：main 侧右键菜单已带确认，无 renderer 侧逻辑（GUI_SPEC 6）
// R3_SPEC 2/4：状态机 → 状态点/文案/输入/按钮
const STATUS_UI = {
  connecting: { dot: '#f0a020', text: '连接中…', btn: '发送' },
  online:     { dot: '#95ec69', text: '在线', btn: '发送' },
  generating: { dot: '#4e9bf7', text: '正在想', btn: '停止' },
  speaking:   { dot: '#4e9bf7', text: '正在说', btn: '停止' },
  offline:    { dot: '#f44', text: '连接断开', btn: '重试连接' },
  failed:     { dot: '#f44', text: '回复未完成', btn: '重试' },
};
let curStatus = 'connecting';
window.pet.onCoreState((m) => {
  curStatus = m.status || (m.connected ? 'online' : 'offline');
  const ui = STATUS_UI[curStatus] || STATUS_UI.connecting;
  document.getElementById('statusDot').style.background = ui.dot;
  statusText.textContent = ui.text;
  sendBtn.textContent = ui.btn;
});

// 发送
let composing = false;  // IME composition 中不发送（GUI_SPEC 输入契约）
input.addEventListener('compositionstart', () => { composing = true; });
input.addEventListener('compositionend', () => { composing = false; });
function send() {
  if (composing) return;
  const text = input.value.trim();
  if (curStatus === 'offline') { window.pet.reconnect(); return; }
  if (curStatus === 'failed') { window.pet.reconnect(); return; }
  if (curStatus === 'generating' || curStatus === 'speaking') {
    // GUI_SPEC 9：generating/speaking → 停止（取消回复/停止说话）
    window.pet.stopReply();
    return;
  }
  if (!text) return;
  window.pet.sendChat(text);
  input.value = '';
  sendBtn.disabled = true;
  statusText.textContent = '正在想';
}
input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });
input.addEventListener('input', () => { sendBtn.disabled = !input.value.trim(); });
sendBtn.addEventListener('click', send);
input.focus();
