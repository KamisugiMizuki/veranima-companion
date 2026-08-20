// 聊天窗口：只消费 preload 白名单事件，不直接接触 WS/fs
const $ = (id) => document.getElementById(id);
const messagesEl = $('messages');
const emptyState = $('emptyState');
const input = $('input');
const sendButton = $('send');
const composer = $('composer');
const statusText = $('statusText');
const statusDot = $('statusDot');
const notice = $('notice');
const noticeText = $('noticeText');
const noticeAction = $('noticeAction');
const historyPanel = $('historyPanel');
const archivePanel = $('archivePanel');
const historyQuery = $('historyQuery');
const searchMeta = $('searchMeta');
const chaptersEl = $('chapters');
const characterAvatar = $('characterAvatar');
const avatarFallback = $('avatarFallback');
const stopReplyButton = $('stopReply');
const stopSpeakingButton = $('stopSpeaking');
const muteToggle = $('muteToggle');

const STATUS = {
  connecting: { text: '正在连接', tone: 'busy' },
  online: { text: '在线', tone: 'online' },
  generating: { text: '正在想', tone: 'busy' },
  speaking: { text: '正在说', tone: 'busy' },
  offline: { text: '连接断开', tone: 'error' },
  failed: { text: '回复未完成', tone: 'error' },
};
let status = 'connecting';
let muted = false;
let composing = false;
let view = 'conversation';
let conversation = [];
let searchRows = [];
let activeReply = null;
let petAvatarSrc = '';
let renderGeneration = 0;
let noticeRetry = null;

function escText(value) { return String(value == null ? '' : value); }
function timeText(ts) {
  if (!ts) return '';
  const d = new Date(typeof ts === 'number' ? ts : String(ts));
  if (Number.isNaN(d.getTime())) return '';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
function normalizeMessage(m) {
  return {
    message_id: m.message_id || `legacy-${m.ts || Date.now()}-${Math.random().toString(16).slice(2)}`,
    role: m.role === 'assistant' ? 'pet' : (m.role || 'pet'),
    text: escText(m.text ?? m.content),
    ts: m.ts || m.created_at || Date.now(),
    status: m.status || 'complete',
    turn_id: m.turn_id || '',
    request_id: m.request_id || '',
  };
}
function isNearBottom() {
  return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 48;
}
function scrollBottom(force = false) {
  if (force || isNearBottom()) messagesEl.scrollTop = messagesEl.scrollHeight;
}
function clearNotice() {
  notice.hidden = true;
  noticeRetry = null;
  noticeAction.hidden = true;
  noticeAction.onclick = null;
}
function showNotice(text, retry) {
  noticeText.textContent = text;
  notice.hidden = false;
  noticeRetry = retry || null;
  noticeAction.hidden = !retry;
  noticeAction.textContent = retry ? '重试' : '';
  noticeAction.onclick = retry ? async () => { const fn = noticeRetry; clearNotice(); await fn(); } : null;
}
function setStatus(next, extra = {}) {
  status = next || 'connecting';
  const ui = STATUS[status] || STATUS.connecting;
  statusText.textContent = ui.text;
  statusDot.className = `status-dot ${ui.tone}`;
  statusText.title = extra.reason || '';
  const busy = status === 'generating' || status === 'speaking';
  stopReplyButton.hidden = !busy;
  stopSpeakingButton.hidden = status !== 'speaking';
  updateComposer();
}
function updateComposer() {
  const text = input.value.trim();
  const offline = status === 'offline';
  const busy = status === 'generating' || status === 'speaking';
  sendButton.disabled = !text || busy;
  sendButton.textContent = offline ? '重试连接' : '发送';
  sendButton.title = offline ? '保留输入并重试连接' : '发送消息';
}
function resizeInput() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(Math.max(input.scrollHeight, 36), 132)}px`;
}
function setAvatar(src) {
  if (!src) return;
  petAvatarSrc = src;
  characterAvatar.src = src;
  characterAvatar.parentElement.classList.add('has-image');
  document.querySelectorAll('.message-avatar img[data-pet-avatar]').forEach((img) => { img.src = src; });
}
function updateEmpty() {
  const hasMessages = document.querySelector('.message-row');
  emptyState.hidden = !!hasMessages || view === 'search';
}
function makeAvatar(role) {
  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  if (role === 'user') { avatar.textContent = '我'; return avatar; }
  if (petAvatarSrc) {
    const img = document.createElement('img');
    img.src = petAvatarSrc; img.alt = '角色头像'; img.dataset.petAvatar = '1';
    img.onerror = () => { img.remove(); avatar.textContent = '由'; };
    avatar.appendChild(img);
  } else avatar.textContent = '由';
  return avatar;
}
function statusLabel(m) {
  if (m.status === 'pending') return '发送中';
  if (m.status === 'sent') return '已送达';
  if (m.status === 'failed') return '发送失败';
  if (m.status === 'cancelled') return '已取消';
  return timeText(m.ts);
}
function renderMessageActions(row, m) {
  row.querySelector('.message-actions')?.remove();
  if (!['failed', 'cancelled'].includes(m.status) || m.role !== 'user') return;
  const actions = document.createElement('div');
  actions.className = 'message-actions';
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.textContent = '重试';
  retry.addEventListener('click', () => retryMessage(m.message_id));
  actions.appendChild(retry);
  row.querySelector('.message-content').appendChild(actions);
}
function appendMessage(m, { forceScroll = false } = {}) {
  const row = document.createElement('article');
  row.className = `message-row ${m.role} ${m.status}`;
  row.dataset.messageId = m.message_id;
  const content = document.createElement('div'); content.className = 'message-content';
  const meta = document.createElement('div'); meta.className = 'message-meta';
  meta.textContent = m.role === 'user' ? '我' : ($('charName').textContent || '角色');
  const bubble = document.createElement('div'); bubble.className = 'message-bubble'; bubble.textContent = m.text;
  const state = document.createElement('div'); state.className = 'message-status'; state.textContent = statusLabel(m);
  content.append(meta, bubble, state);
  row.append(makeAvatar(m.role), content);
  renderMessageActions(row, m);
  messagesEl.insertBefore(row, $('newMessageMarker'));
  updateEmpty(); scrollBottom(forceScroll);
  return row;
}
function updateRenderedMessage(m) {
  const row = messagesEl.querySelector(`[data-message-id="${CSS.escape(m.message_id)}"]`);
  if (!row) { appendMessage(m); return; }
  row.className = `message-row ${m.role} ${m.status}`;
  const state = row.querySelector('.message-status'); if (state) state.textContent = statusLabel(m);
  const bubble = row.querySelector('.message-bubble'); if (bubble) bubble.textContent = m.text;
  renderMessageActions(row, m);
}
function renderList(list, { search = false } = {}) {
  renderGeneration += 1;
  messagesEl.querySelectorAll('.message-row, .search-empty').forEach((el) => el.remove());
  const rows = (list || []).map(normalizeMessage);
  if (!rows.length && search) {
    const empty = document.createElement('div'); empty.className = 'search-empty empty-state';
    empty.innerHTML = '<strong>没有找到匹配的消息</strong><span>换个关键词试试。</span>';
    messagesEl.insertBefore(empty, $('newMessageMarker'));
  } else {
    const generation = renderGeneration;
    let i = 0;
    const batch = () => {
      if (generation !== renderGeneration) return;
      const end = Math.min(i + 40, rows.length);
      for (; i < end; i += 1) appendMessage(rows[i]);
      if (i < rows.length) setTimeout(batch, 0);
    };
    batch();
  }
  updateEmpty(); scrollBottom(true);
}
function currentRows() { return view === 'search' ? searchRows : conversation; }
function setConversation(list) {
  conversation = (list || []).map(normalizeMessage);
  if (view === 'conversation') renderList(conversation);
}
function updateOrAppend(m) {
  const msg = normalizeMessage(m);
  const shouldFollow = isNearBottom();
  const index = conversation.findIndex((x) => x.message_id === msg.message_id);
  if (index >= 0) conversation[index] = { ...conversation[index], ...msg };
  else conversation.push(msg);
  if (view === 'conversation') {
    const existing = messagesEl.querySelector(`[data-message-id="${CSS.escape(msg.message_id)}"]`);
    if (existing) updateRenderedMessage(msg);
    else {
      appendMessage(msg, { forceScroll: shouldFollow });
      if (!shouldFollow) $('newMessageMarker').hidden = false;
    }
  }
}
function renderReplyStart(event) {
  const follow = isNearBottom();
  activeReply = { ...event, text: '' };
  const row = appendMessage({ message_id: event.message_id, role: 'pet', text: '…', status: 'pending', ts: Date.now() }, { forceScroll: follow });
  if (!follow) $('newMessageMarker').hidden = false;
  activeReply.row = row;
  const bubble = row.querySelector('.message-bubble'); if (bubble) bubble.classList.add('typing');
}
function renderReplySegment(event) {
  if (!activeReply || activeReply.message_id !== event.message_id) return;
  activeReply.text += event.text || '';
  const bubble = activeReply.row && activeReply.row.querySelector('.message-bubble');
  if (bubble) { bubble.textContent = activeReply.text; bubble.classList.remove('typing'); }
  if (isNearBottom()) scrollBottom();
  else $('newMessageMarker').hidden = false;
}
function renderReplyEnd(event) {
  if (!activeReply || (event.message && event.message.message_id !== activeReply.message_id)) return;
  const message = normalizeMessage(event.message || { message_id: activeReply.message_id, role: 'pet', text: activeReply.text, status: event.status });
  conversation.push(message);
  if (activeReply.row) {
    activeReply.row.dataset.messageId = message.message_id;
    activeReply.row.className = `message-row pet ${message.status}`;
    const bubble = activeReply.row.querySelector('.message-bubble'); if (bubble) bubble.textContent = message.text;
    const state = activeReply.row.querySelector('.message-status'); if (state) state.textContent = statusLabel(message);
  }
  activeReply = null; updateEmpty(); scrollBottom();
}
function renderReplyCancelled() {
  if (!activeReply) return;
  const bubble = activeReply.row && activeReply.row.querySelector('.message-bubble');
  if (bubble) bubble.textContent = activeReply.text || '（已取消）';
  if (activeReply.row) activeReply.row.classList.add('cancelled');
  activeReply = null;
}
function retryMessage(id) {
  window.pet.retryChat(id).then((result) => {
    if (!result || !result.ok) showNotice('重试失败，核心可能未连接。', () => retryMessage(id));
  });
}
async function send() {
  if (composing) return;
  if (status === 'offline') { window.pet.reconnect(); return; }
  const text = input.value.trim(); if (!text) return;
  clearNotice();
  const result = await window.pet.sendChat(text);
  if (!result || !result.ok) { showNotice('消息没有送出，输入内容已保留。', () => send()); return; }
  input.value = ''; resizeInput(); updateComposer();
}
function togglePanel(panel, button) {
  const opening = panel.hidden;
  panel.hidden = !opening; button.setAttribute('aria-pressed', String(opening));
}
async function search() {
  const q = historyQuery.value.trim(); if (!q) return;
  let result;
  try { result = await window.pet.searchHistory(q); }
  catch { showNotice('历史搜索暂时不可用。', () => search()); return; }
  searchRows = (result && result.data) || [];
  view = 'search'; searchMeta.textContent = `${searchRows.length} 条匹配：${q}`;
  renderList(searchRows, { search: true });
}
function backToConversation() {
  view = 'conversation'; searchMeta.textContent = ''; renderList(conversation);
}
async function loadChapters() {
  let result;
  try { result = await window.pet.getSelfModel(); }
  catch {
    chaptersEl.innerHTML = '<span class="muted">人物档案暂时不可用。</span>';
    return;
  }
  const rows = result && result.data && result.data.chapters || [];
  chaptersEl.textContent = '';
  if (!rows.length) { chaptersEl.innerHTML = '<span class="muted">还没有形成章节。</span>'; return; }
  rows.forEach((row) => {
    const item = document.createElement('div'); item.className = 'chapter';
    const title = document.createElement('strong'); title.textContent = row.title || '未命名章节';
    const body = document.createElement('span'); body.textContent = row.self_interpretation || '暂无自我解释';
    item.append(title, body); chaptersEl.appendChild(item);
  });
}
function setMute(value) {
  muted = !!value; muteToggle.setAttribute('aria-pressed', String(muted));
  muteToggle.title = muted ? '打开语音' : '静音语音'; muteToggle.textContent = muted ? '◼' : '♫';
  window.pet.setSpeechMuted(muted);
}

window.pet.onAvatarMap((map) => {
  if (map && Object.keys(map).length) setAvatar(map[Object.keys(map)[0]]);
});
window.pet.onChatProfile((profile) => {
  if (profile && profile.name) $('charName').textContent = profile.name;
});
window.pet.onChatHistory((list) => setConversation(list));
window.pet.onChatLine((message) => updateOrAppend(message));
window.pet.onChatEvent((event) => {
  switch (event.type) {
    case 'reply-start': renderReplyStart(event); break;
    case 'reply-segment': renderReplySegment(event); break;
    case 'reply-end': renderReplyEnd(event); break;
    case 'reply-cancelled': renderReplyCancelled(); break;
    case 'message-update': updateOrAppend(event.message); break;
    case 'reply-error':
      showNotice(event.code === 'tts_failed' ? '语音没有播放，文字仍可阅读。' : '这条回复没有完成。', null);
      break;
    case 'speech-error': showNotice('语音没有播放，文字仍可阅读。', null); break;
    case 'speech-muted': muted = !!event.muted; break;
    case 'speech-stopped': stopSpeakingButton.hidden = true; break;
    default: break;
  }
});
window.pet.onCoreState((m) => {
  setStatus(m.status || (m.connected ? 'online' : 'offline'), m);
  if (m.character) $('charName').textContent = m.character;
  statusText.setAttribute('aria-label', `连接状态：${statusText.textContent}`);
});
window.pet.getChatState().then((state) => {
  if (!state) return;
  setStatus(state.status || 'connecting'); setMute(state.muted);
  if (state.history) setConversation(state.history);
  if (state.active_reply) {
    renderReplyStart(state.active_reply);
    if (state.active_reply.text) renderReplySegment({
      message_id: state.active_reply.message_id,
      text: state.active_reply.text,
    });
  }
});
loadChapters();

$('historyToggle').addEventListener('click', () => togglePanel(historyPanel, $('historyToggle')));
$('archiveToggle').addEventListener('click', () => { togglePanel(archivePanel, $('archiveToggle')); if (!archivePanel.hidden) loadChapters(); });
$('archiveClose').addEventListener('click', () => { archivePanel.hidden = true; $('archiveToggle').setAttribute('aria-pressed', 'false'); });
$('searchForm').addEventListener('submit', (e) => { e.preventDefault(); search(); });
$('historyBack').addEventListener('click', backToConversation);
$('clearChat').addEventListener('click', async () => {
  const ok = await window.pet.clearChat();
  if (ok) { conversation = []; backToConversation(); }
});
muteToggle.addEventListener('click', () => setMute(!muted));
stopReplyButton.addEventListener('click', () => window.pet.stopReply());
stopSpeakingButton.addEventListener('click', () => window.pet.stopSpeaking());
composer.addEventListener('submit', (e) => { e.preventDefault(); send(); });
input.addEventListener('compositionstart', () => { composing = true; });
input.addEventListener('compositionend', () => { composing = false; });
input.addEventListener('input', () => { resizeInput(); updateComposer(); });
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!historyPanel.hidden) {
    historyPanel.hidden = true;
    $('historyToggle').setAttribute('aria-pressed', 'false');
  }
  if (!archivePanel.hidden) {
    archivePanel.hidden = true;
    $('archiveToggle').setAttribute('aria-pressed', 'false');
  }
});
document.querySelectorAll('.starter').forEach((button) => button.addEventListener('click', () => {
  input.value = button.dataset.text || ''; resizeInput(); updateComposer(); input.focus();
}));
$('newMessageMarker').addEventListener('click', () => { scrollBottom(true); $('newMessageMarker').hidden = true; });
messagesEl.addEventListener('scroll', () => { if (isNearBottom()) $('newMessageMarker').hidden = true; });
input.focus(); resizeInput(); updateComposer();
