// 聊天窗口 renderer：QQ 风格消息列表 + 输入框（独立窗口，聊天记录持久化在 main）
const msgsEl = document.getElementById('msgs');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const statusText = document.getElementById('statusText');
const charName = document.getElementById('charName');
let activePetMsg = null;   // 流式进行中的桌宠气泡元素（speak_chunk 追加）

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
  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  if (m.role === 'pet') {
    const img = document.createElement('img');
    img.src = 'assets/idle.png';
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
window.pet.onChatHistory((list) => {
  msgsEl.innerHTML = '';
  activePetMsg = null;
  (list || []).forEach(appendMsg);
});
window.pet.onChatLine((m) => {
  if (m.role === 'pet' && m.streaming) { appendChunk(m.text); return; }
  if (m.role === 'pet' && m.finish) { finishPetMsg(m.ts); return; }
  appendMsg(m);
});
window.pet.onCoreState((m) => {
  document.getElementById('statusDot').style.background = m.connected ? '#95ec69' : '#f0a020';
  statusText.textContent = m.connected ? (m.state === 'thinking' ? '思考中…' : '在线') : '核心未连接';
});

// 发送
function send() {
  const text = input.value.trim();
  if (!text) return;
  window.pet.sendChat(text);
  input.value = '';
  sendBtn.disabled = true;
  statusText.textContent = '等待回复…';
}
input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });
input.addEventListener('input', () => { sendBtn.disabled = !input.value.trim(); });
sendBtn.addEventListener('click', send);
input.focus();
