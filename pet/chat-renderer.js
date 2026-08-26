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
const imagePreview = $('imagePreview');
const attachImage = $('attachImage');
const recordAudio = $('recordAudio');
const MAX_RECORDING_BYTES = 20 * 1024 * 1024;
const MAX_RECORDING_MS = 120 * 1000;

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
let pendingImages = [];
let recordingSession = null;

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
    images: Array.isArray(m.image_data) ? m.image_data.filter((x) => typeof x === 'string') : [],
    image_refs: Array.isArray(m.images) ? m.images.filter((x) => typeof x === 'string') : [],
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
  sendButton.disabled = (!text && !pendingImages.length) || busy;
  sendButton.textContent = offline ? '重试连接' : '发送';
  sendButton.title = offline ? '保留输入并重试连接' : '发送消息';
}
function renderImagePreview() {
  imagePreview.textContent = '';
  imagePreview.hidden = !pendingImages.length;
  pendingImages.forEach((src, index) => {
    const item = document.createElement('div'); item.className = 'image-preview-item';
    const image = document.createElement('img'); image.src = src; image.alt = `待发送图片 ${index + 1}`;
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×';
    remove.title = '移除图片'; remove.setAttribute('aria-label', `移除图片 ${index + 1}`);
    remove.addEventListener('click', () => { pendingImages.splice(index, 1); renderImagePreview(); updateComposer(); });
    item.append(image, remove); imagePreview.appendChild(item);
  });
}
function addClipboardImage(file) {
  if (!file || pendingImages.length >= 4 || file.size > 10 * 1024 * 1024) return;
  const reader = new FileReader();
  reader.onload = () => {
    if (typeof reader.result === 'string' && reader.result.startsWith('data:image/')) {
      pendingImages.push(reader.result); renderImagePreview(); updateComposer();
    }
  };
  reader.readAsDataURL(file);
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
function renderMessageBubble(bubble, m) {
  bubble.textContent = m.text;
  (m.images || []).forEach((src, index) => {
    if (!src.startsWith('data:image/') && !src.startsWith('file://')) return;
    const image = document.createElement('img'); image.src = src; image.alt = `消息图片 ${index + 1}`;
    image.style.cssText = 'display:block;max-width:220px;max-height:180px;margin-top:6px;border-radius:4px;object-fit:contain;';
    bubble.appendChild(image);
  });
}
function appendMessage(m, { forceScroll = false } = {}) {
  const row = document.createElement('article');
  row.className = `message-row ${m.role} ${m.status}`;
  row.dataset.messageId = m.message_id;
  const content = document.createElement('div'); content.className = 'message-content';
  const meta = document.createElement('div'); meta.className = 'message-meta';
  meta.textContent = m.role === 'user' ? '我' : ($('charName').textContent || '角色');
  const bubble = document.createElement('div'); bubble.className = 'message-bubble'; renderMessageBubble(bubble, m);
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
  const bubble = row.querySelector('.message-bubble'); if (bubble) renderMessageBubble(bubble, m);
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
  const text = input.value.trim();
  if (!text && !pendingImages.length) return;
  clearNotice();
  const result = await window.pet.sendChat(text, pendingImages);
  if (!result || !result.ok) {
    const failedId = result && result.message && result.message.message_id;
    if (!failedId) { showNotice('消息没有送出，输入内容已保留。', () => send()); return; }
    input.value = ''; pendingImages = []; renderImagePreview(); resizeInput(); updateComposer();
    showNotice('消息没有送出，已保留在聊天记录。', () => retryMessage(failedId));
    return;
  }
  input.value = ''; pendingImages = []; renderImagePreview(); resizeInput(); updateComposer();
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
// C-5：共同项目面板（只读列表；创建/确认仍走对话确认流，不绕过闸门）
const projectsPanel = $('projectsPanel');
async function loadProjects() {
  const listEl = $('projectsList');
  try {
    const data = await window.pet.listProjects();
    const projects = (data && data.projects) || [];
    if (!projects.length) {
      listEl.innerHTML = '<span class="muted">还没有共同项目。想一起做点什么的话，直接跟我说。</span>';
      return;
    }
    listEl.innerHTML = projects.map((prj) => `
      <div class="chapter">
        <strong>${escapeHtml(prj.title || prj.project_id)}</strong>
        <div class="muted">${escapeHtml(prj.kind || '')} · ${escapeHtml(prj.status || '')}</div>
        <div>${escapeHtml(prj.purpose || '')}</div>
      </div>`).join('');
  } catch (e) {
    listEl.innerHTML = '<span class="muted">读取失败，稍后再试。</span>';
  }
}
function escapeHtml(s) { return String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
$('projectsToggle').addEventListener('click', () => { togglePanel(projectsPanel, $('projectsToggle')); if (!projectsPanel.hidden) loadProjects(); });
$('projectsClose').addEventListener('click', () => { projectsPanel.hidden = true; $('projectsToggle').setAttribute('aria-pressed', 'false'); });
$('searchForm').addEventListener('submit', (e) => { e.preventDefault(); search(); });
$('historyBack').addEventListener('click', backToConversation);
$('clearChat').addEventListener('click', async () => {
  const ok = await window.pet.clearChat();
  if (ok) { conversation = []; backToConversation(); }
});
muteToggle.addEventListener('click', () => setMute(!muted));
attachImage.addEventListener('click', () => showNotice('直接在输入框粘贴图片即可。', null));
input.addEventListener('paste', (event) => {
  const items = Array.from(event.clipboardData?.items || []);
  const image = items.find((item) => item.kind === 'file' && item.type.startsWith('image/'));
  if (!image) return;
  event.preventDefault(); addClipboardImage(image.getAsFile());
});
function cleanupRecording(discard = true) {
  const session = recordingSession;
  if (!session) return;
  session.discard = session.discard || discard;
  clearTimeout(session.timer);
  if (session.recorder.state !== 'inactive') session.recorder.stop();
  if (discard || session.recorder.state === 'inactive') {
    session.stream.getTracks().forEach((track) => track.stop());
  }
  if (session.recorder.state === 'inactive' && recordingSession === session) {
    recordingSession = null;
    recordAudio.textContent = '●';
  }
}

window.pet.onChatHidden(() => cleanupRecording(true));
window.addEventListener('pagehide', () => cleanupRecording(true));
document.addEventListener('visibilitychange', () => {
  if (document.hidden) cleanupRecording(true);
});

recordAudio.addEventListener('click', async () => {
  if (recordingSession) { cleanupRecording(false); return; }
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
    showNotice('当前环境不支持录音。', null); return;
  }
  let stream;
  try {
    const deviceId = await window.pet.getSttInputDevice();
    stream = await navigator.mediaDevices.getUserMedia({
      audio: deviceId ? { deviceId: { exact: deviceId } } : true,
    });
    if (document.hidden) { stream.getTracks().forEach((track) => track.stop()); return; }
    let recorder;
    try {
      recorder = new MediaRecorder(stream);
    } catch (error) {
      stream.getTracks().forEach((track) => track.stop());
      throw error;
    }
    const session = {
      recorder, stream, chunks: [], byteCount: 0, tooLarge: false,
      discard: false, timer: null,
    };
    recordingSession = session;
    const mime = recorder.mimeType || 'audio/webm';
    session.timer = setTimeout(() => cleanupRecording(false), MAX_RECORDING_MS);
    recorder.ondataavailable = (event) => {
      if (!event.data.size) return;
      session.byteCount += event.data.size;
      if (session.byteCount > MAX_RECORDING_BYTES) {
        session.tooLarge = true; cleanupRecording(false); return;
      }
      session.chunks.push(event.data);
    };
    recorder.onerror = () => {
      showNotice('录音失败，文字输入仍可用。', null);
      cleanupRecording(true);
    };
    recorder.onstop = async () => {
      clearTimeout(session.timer);
      stream.getTracks().forEach((track) => track.stop());
      if (recordingSession === session) recordingSession = null;
      recordAudio.textContent = '●';
      if (session.discard) return;
      if (session.tooLarge) { showNotice('录音超过 20MB，已停止且未发送。', null); return; }
      try {
        const blob = new Blob(session.chunks, { type: mime });
        const text = await window.pet.transcribeAudio(await blob.arrayBuffer(), 'voice.webm');
        if (text) {
          input.value = `${input.value}${input.value ? ' ' : ''}${text}`;
          resizeInput(); updateComposer();
        } else {
          showNotice('没有识别到清晰语音，请靠近麦克风再试。', null);
        }
      } catch { showNotice('语音识别失败，文字输入仍可用。', null); }
    };
    recorder.start(1000); recordAudio.textContent = '■'; showNotice('正在录音，再点一次结束（最长 120 秒）。', null);
  } catch {
    if (stream) stream.getTracks().forEach((track) => track.stop());
    showNotice('无法访问麦克风。', null);
  }
});
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
