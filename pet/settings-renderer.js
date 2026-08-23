// 设置页：模型配置切换 + 其他本地配置编辑
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

let snapshot = null;
let llmProfiles = [];
let activeProfileId = '';
let selectedProfileId = '';

function selectedProfile() { return llmProfiles.find((p) => p.id === selectedProfileId) || llmProfiles[0] || null; }
function renderProfiles(payload) {
  llmProfiles = Array.isArray(payload.profiles) ? payload.profiles : [];
  activeProfileId = payload.active_profile || (llmProfiles[0] && llmProfiles[0].id) || '';
  selectedProfileId = activeProfileId;
  const select = $('llm-profile-select');
  select.textContent = '';
  llmProfiles.forEach((p) => {
    const option = document.createElement('option');
    option.value = p.id; option.textContent = `${p.name || p.id}${p.id === activeProfileId ? '（正在使用）' : ''}`;
    select.appendChild(option);
  });
  select.value = selectedProfileId;
  const current = selectedProfile();
  if (current) fillProfile(current);
  $('llm-profile-status').textContent = current ? `${current.id === activeProfileId ? '正在使用' : '已保存'} · API Key ${current.has_api_key ? '已配置' : '未配置'}` : '暂无配置';
}
function fillProfile(profile) {
  set('llm-profile-name', profile.name || profile.id);
  set('llm-base', profile.base_url); set('llm-model', profile.model);
  num('llm-temp', profile.temperature, 0.8); num('llm-max-tokens', profile.max_tokens, 4096); num('llm-timeout', profile.timeout, 30); num('llm-timeout-retries', profile.timeout_retries, 3);
  $('llm-key').value = '';
  $('llm-key-status').textContent = profile.has_api_key ? `已配置：${profile.api_key}` : '未配置；填写后保存会写入该配置';
  $('llm-summary').textContent = `${profile.base_url || '未配置'} · ${profile.model || '未选择模型'}`;
}
function profileFields(includeName = true) {
  const data = {
    base_url: $('llm-base').value.trim(), model: $('llm-model').value.trim(),
    temperature: Number($('llm-temp').value), max_tokens: Number($('llm-max-tokens').value),
    timeout: Number($('llm-timeout').value), timeout_retries: Number($('llm-timeout-retries').value),
  };
  if (includeName) data.name = $('llm-profile-name').value.trim();
  const key = $('llm-key').value.trim(); if (key) data.api_key = key;
  return data;
}
async function profileAction(action, payload, message = '已保存，核心正在重启') {
  const response = await window.pet.profileConfig(action, payload);
  if (!response || !response.ok) throw new Error((response && response.error) || '核心未连接或操作失败');
  showMsg(message, true); window.pet.restartCore();
}

function renderRoles(cfg) {
  const sel = $('role-select'); const roles = cfg.roles || [];
  sel.innerHTML = roles.length ? roles.map((r) => `<option value="${r.id}">${r.name || r.id}</option>`).join('') : '<option value="">无角色</option>';
  const current = (cfg.character_card || '').split('/')[1] || '';
  if (current) sel.value = current;
}

window.pet.getConfig().then((cfg) => {
  if (!cfg) { showMsg('核心未连接', false); return; }
  snapshot = cfg;
  const llm = cfg.llm || {}, tts = cfg.tts || {}, stt = cfg.stt || {}, qq = cfg.qq || {};
  const mem = cfg.memory || {}, att = cfg.attention || {}, pro = cfg.proactive || {}, search = cfg.search || {};
  renderProfiles(llm);
  renderRoles(cfg); $('card-path').textContent = cfg.character_card || '未设置';
  num('avatar-height', cfg.pet && cfg.pet.avatar_height, 200);
  set('tts-base', tts.base_url); set('tts-model', tts.model); set('tts-voice', tts.voice);
  set('stt-base', stt.base_url); set('stt-model', stt.model); set('stt-language', stt.language); bool('stt-enabled', stt.enabled ?? true);
  if ($('stt-device')) loadAudioDevices(stt.input_device_id);
  set('mem-embedding', mem.embedding_model); num('mem-top-k', mem.recall_top_k, 5); num('mem-threshold', mem.recall_threshold, 0.3); num('mem-total-budget', mem.max_injected_chars, 5600); num('mem-curator-turns', mem.curator_turns, 8); bool('mem-decay', mem.decay_enabled);
  $('mem-status').textContent = `数据库：${mem.db_path || 'data/veranima.db'}`; $('mem-db').textContent = mem.db_path || 'data/veranima.db'; $('mem-effective').textContent = mem.embedding_model || '未配置';
  bool('att-enabled', att.enabled ?? true); bool('att-paused', att.paused); num('att-scan', att.global_scan_sec, 5); num('att-budget', att.observe_daily_budget, 120);
  const channels = pro.channels || {}; const qqPro = channels.qq || pro; const petPro = channels.pet || pro;
  const tension = cfg.relationship_tension || {};
  bool('pro-enabled', pro.enabled ?? true); bool('pro-quiet', pro.quiet_hours_enabled ?? true); num('pro-qq-max', qqPro.max_per_day, 2); num('pro-qq-gap', qqPro.min_gap_minutes, 120); num('pro-pet-max', petPro.max_per_day, 2); num('pro-pet-gap', petPro.min_gap_minutes, 30);
  bool('search-enabled', search.enabled ?? false); set('search-base', search.base_url || 'http://127.0.0.1:8080'); num('search-timeout', search.timeout_seconds, 8); num('search-cache', search.cache_ttl_seconds, 900); bool('search-implicit', search.allow_implicit_freshness_search ?? false); bool('search-semantic', search.semantic_locator_enabled ?? false); bool('search-pages', search.fetch_pages ?? false);
  bool('tension-enabled', tension.enabled ?? true); bool('tension-high-proactive', tension.high_tension_proactive ?? false);
  set('qq-allowed', (qq.allowed || []).join(',')); bool('qq-proactive', qq.proactive); bool('qq-offline', qq.offline_think && qq.offline_think.enabled);
}).catch(() => showMsg('读取配置失败', false));

$('llm-profile-select').addEventListener('change', () => {
  selectedProfileId = $('llm-profile-select').value;
  const profile = selectedProfile(); if (profile) fillProfile(profile);
  $('llm-profile-status').textContent = profile ? `${profile.id === activeProfileId ? '正在使用' : '已保存'} · API Key ${profile.has_api_key ? '已配置' : '未配置'}` : '暂无配置';
});
$('llm-switch').addEventListener('click', async () => {
  try { await profileAction('switch', { profile_id: $('llm-profile-select').value }, '已切换配置，核心正在重启'); }
  catch (e) { showMsg(e.message || '切换失败', false); }
});
$('llm-save-profile').addEventListener('click', async () => {
  try { await profileAction('update', { profile_id: selectedProfileId, ...profileFields() }); }
  catch (e) { showMsg(e.message || '保存配置失败', false); }
});
$('llm-add-profile').addEventListener('click', async () => {
  try {
    const data = profileFields();
    if (!data.name) throw new Error('请先填写配置名称');
    await profileAction('add', data, '已新增配置，核心正在重启');
  } catch (e) { showMsg(e.message || '新增配置失败', false); }
});
$('llm-delete').addEventListener('click', async () => {
  try { await profileAction('delete', { profile_id: $('llm-profile-select').value }, '已删除配置，核心正在重启'); }
  catch (e) { showMsg(e.message || '删除失败；当前配置不能删除', false); }
});

$('save').addEventListener('click', async () => {
  const allowed = $('qq-allowed').value.split(',').map((s) => s.trim()).filter(Boolean);
  const data = {
    tts: { base_url: $('tts-base').value.trim(), model: $('tts-model').value.trim(), voice: $('tts-voice').value.trim() },
    stt: { enabled: $('stt-enabled').value === 'true', base_url: $('stt-base').value.trim(), model: $('stt-model').value.trim(), language: $('stt-language').value.trim(), input_device_id: $('stt-device').value },
    memory: { embedding_model: $('mem-embedding').value.trim(), recall_top_k: Number($('mem-top-k').value) || 5, recall_threshold: Number($('mem-threshold').value) || 0.3, max_injected_chars: Number($('mem-total-budget').value) || 5600, curator_turns: Number($('mem-curator-turns').value) || 8, decay_enabled: $('mem-decay').value === 'true' },
    attention: { enabled: $('att-enabled').value === 'true', paused: $('att-paused').value === 'true', global_scan_sec: Number($('att-scan').value) || 5, observe_daily_budget: Number($('att-budget').value) || 120 },
    search: { enabled: $('search-enabled').value === 'true', base_url: $('search-base').value.trim(), timeout_seconds: Number($('search-timeout').value) || 8, cache_ttl_seconds: Number.isFinite(Number($('search-cache').value)) ? Number($('search-cache').value) : 900, allow_implicit_freshness_search: $('search-implicit').value === 'true', semantic_locator_enabled: $('search-semantic').value === 'true', fetch_pages: $('search-pages').value === 'true' },
    proactive: { enabled: $('pro-enabled').value === 'true', quiet_hours_enabled: $('pro-quiet').value === 'true', channels: { qq: { max_per_day: Number($('pro-qq-max').value) || 2, min_gap_minutes: Number($('pro-qq-gap').value) || 120 }, pet: { max_per_day: Number($('pro-pet-max').value) || 2, min_gap_minutes: Number($('pro-pet-gap').value) || 30 } } },
    relationship_tension: { enabled: $('tension-enabled').value === 'true', high_tension_proactive: $('tension-high-proactive').value === 'true' },
    qq: { allowed, proactive: $('qq-proactive').value === 'true', offline_think: { enabled: $('qq-offline').value === 'true' } },
    pet: { avatar_height: Number($('avatar-height').value) || 200 },
  };
  const role = $('role-select').value; if (role) data.character_card = `characters/${role}/character.json`;
  $('save').disabled = true; showMsg('正在保存…', true);
  try {
    const ok = await window.pet.saveConfig(data);
    if (!ok) throw new Error('核心未连接或保存失败');
    showMsg('已保存，核心正在重启', true); window.pet.restartCore();
  } catch (e) { showMsg(e.message || '保存失败', false); }
  finally { $('save').disabled = false; }
});
