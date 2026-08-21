// 设置窗口：导航、配置快照、类型化保存、状态反馈
const $ = (id) => document.getElementById(id);
const pages = document.querySelectorAll('.nav-item');
pages.forEach((btn) => btn.addEventListener('click', () => {
  pages.forEach((b) => { b.classList.remove('is-active'); b.removeAttribute('aria-current'); });
  btn.classList.add('is-active'); btn.setAttribute('aria-current', 'page');
  document.querySelectorAll('.page').forEach((p) => p.classList.toggle('is-active', p.dataset.page === btn.dataset.page));
}));
function showMsg(text, ok) { const el = $('msg'); el.textContent = text; el.className = ok ? 'ok' : 'err'; }
function set(id, value) { const el = $(id); if (el) el.value = value ?? ''; }
function bool(id, value) { set(id, String(Boolean(value))); }
function num(id, value, fallback = '') { set(id, value === undefined || value === null ? fallback : value); }
async function loadAudioDevices(selected = '') {
  const select = $('stt-device');
  if (!select || !navigator.mediaDevices?.enumerateDevices) return;
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const inputs = devices.filter((d) => d.kind === 'audioinput');
    select.innerHTML = '<option value="">系统默认麦克风</option>';
    inputs.forEach((d, i) => {
      const option = document.createElement('option');
      option.value = d.deviceId; option.textContent = d.label || `麦克风 ${i + 1}`;
      select.appendChild(option);
    });
    select.value = selected || '';
  } catch (_) {}
}

function renderRoles(cfg) {
  const sel = $('role-select'); const roles = cfg.roles || [];
  sel.innerHTML = roles.length ? roles.map((r) => `<option value="${r.id}">${r.name || r.id}</option>`).join('') : '<option value="">无角色</option>';
  const current = (cfg.character_card || '').split('/')[1] || '';
  if (current) sel.value = current;
}
let snapshot = null;
window.pet.getConfig().then((cfg) => {
  if (!cfg) { showMsg('核心未连接', false); return; }
  snapshot = cfg;
  const llm = cfg.llm || {}, tts = cfg.tts || {}, stt = cfg.stt || {}, qq = cfg.qq || {};
  const mem = cfg.memory || {}, att = cfg.attention || {}, pro = cfg.proactive || {};
  renderRoles(cfg); $('card-path').textContent = cfg.character_card || '未设置';
  num('avatar-height', cfg.pet && cfg.pet.avatar_height, 200);
  $('llm-summary').textContent = `${llm.base_url || '未配置'} · ${llm.model || '未选择模型'}`;
  set('llm-base', llm.base_url); set('llm-model', llm.model); num('llm-temp', llm.temperature, 0.8); num('llm-max-tokens', llm.max_tokens, 4096); num('llm-timeout', llm.timeout, 120);
  $('llm-key-status').textContent = llm.api_key ? `已配置：${llm.api_key}` : '未配置';
  set('tts-base', tts.base_url); set('tts-model', tts.model); set('tts-voice', tts.voice);
  set('stt-base', stt.base_url); set('stt-model', stt.model); set('stt-language', stt.language); bool('stt-enabled', stt.enabled ?? true);
  if ($('stt-device')) loadAudioDevices(stt.input_device_id);
  set('mem-embedding', mem.embedding_model); num('mem-top-k', mem.recall_top_k, 5); num('mem-threshold', mem.recall_threshold, 0.3); num('mem-total-budget', mem.max_injected_chars, 5600); num('mem-curator-turns', mem.curator_turns, 8); bool('mem-decay', mem.decay_enabled);
  $('mem-status').textContent = `数据库：${mem.db_path || 'data/veranima.db'}`; $('mem-db').textContent = mem.db_path || 'data/veranima.db'; $('mem-effective').textContent = mem.embedding_model || '未配置';
  bool('att-enabled', att.enabled ?? true); bool('att-paused', att.paused); num('att-scan', att.global_scan_sec, 5); num('att-budget', att.observe_daily_budget, 120);
  bool('pro-enabled', pro.enabled ?? true); bool('pro-quiet', pro.quiet_hours_enabled ?? true); num('pro-max', pro.max_per_day, 2); num('pro-gap', pro.min_gap_minutes, 30); num('pro-source-gap', pro.source_gap_minutes, 120);
  set('qq-allowed', (qq.allowed || []).join(',')); bool('qq-proactive', qq.proactive); bool('qq-offline', qq.offline_think && qq.offline_think.enabled);
}).catch(() => showMsg('读取配置失败', false));
$('save').addEventListener('click', async () => {
  const allowed = $('qq-allowed').value.split(',').map((s) => s.trim()).filter(Boolean);
  const data = {
    llm: { base_url: $('llm-base').value.trim(), model: $('llm-model').value.trim(), temperature: Number($('llm-temp').value) || 0.8, max_tokens: Number($('llm-max-tokens').value) || 4096, timeout: Number($('llm-timeout').value) || 120 },
    tts: { base_url: $('tts-base').value.trim(), model: $('tts-model').value.trim(), voice: $('tts-voice').value.trim() },
    stt: { enabled: $('stt-enabled').value === 'true', base_url: $('stt-base').value.trim(), model: $('stt-model').value.trim(), language: $('stt-language').value.trim(), input_device_id: $('stt-device').value },
    memory: { embedding_model: $('mem-embedding').value.trim(), recall_top_k: Number($('mem-top-k').value) || 5, recall_threshold: Number($('mem-threshold').value) || 0.3, max_injected_chars: Number($('mem-total-budget').value) || 5600, curator_turns: Number($('mem-curator-turns').value) || 8, decay_enabled: $('mem-decay').value === 'true' },
    attention: { enabled: $('att-enabled').value === 'true', paused: $('att-paused').value === 'true', global_scan_sec: Number($('att-scan').value) || 5, observe_daily_budget: Number($('att-budget').value) || 120 },
    proactive: { enabled: $('pro-enabled').value === 'true', quiet_hours_enabled: $('pro-quiet').value === 'true', max_per_day: Number($('pro-max').value) || 2, min_gap_minutes: Number($('pro-gap').value) || 30, source_gap_minutes: Number($('pro-source-gap').value) || 120 },
    qq: { allowed, proactive: $('qq-proactive').value === 'true', offline_think: { enabled: $('qq-offline').value === 'true' } },
    pet: { avatar_height: Number($('avatar-height').value) || 200 },
  };
  const key = $('llm-key').value.trim(); if (key) data.llm.api_key = key;
  const role = $('role-select').value; if (role) data.character_card = `characters/${role}/character.json`;
  $('save').disabled = true; showMsg('正在保存…', true);
  try {
    const ok = await window.pet.saveConfig(data);
    if (!ok) throw new Error('核心未连接或保存失败');
    showMsg('已保存，核心正在重启', true); window.pet.restartCore();
  } catch (e) { showMsg(e.message || '保存失败', false); }
  finally { $('save').disabled = false; }
});
