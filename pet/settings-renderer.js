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
function stickerSettings() {
  return {
    enabled: $('sticker-learning-mode').value !== 'off' || $('sticker-send-rate').value !== 'off',
    dir: $('sticker-dir').value.trim(), learning_mode: $('sticker-learning-mode').value,
    send_rate: $('sticker-send-rate').value, min_reply_gap: Number($('sticker-min-gap').value),
    pending_ttl_days: Number($('sticker-ttl').value), max_items: Number($('sticker-max-items').value),
  };
}
async function refreshStickers() {
  const list = $('sticker-list'); if (!list) return;
  const payload = await window.pet.listStickers(); list.textContent = '';
  if (!payload?.ok) { list.textContent = payload?.error || '核心未连接'; return; }
  const entries = payload.data?.entries || [];
  if (!entries.length) { list.textContent = '暂无待审核或已授权表情'; return; }
  entries.forEach((entry) => {
    const row = document.createElement('div'); row.className = 'sticker-row';
    const info = document.createElement('span');
    info.textContent = `${entry.status === 'pending' ? '待审核' : entry.status === 'disabled' ? '已停用' : '已启用'} · ${entry.meaning || '未标注'} · ${(entry.moods || []).join('、')}`;
    row.appendChild(info);
    const actions = document.createElement('span'); actions.className = 'inline-actions';
    const addAction = (action, label) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'subtle-button'; button.textContent = label; button.onclick = async () => {
      button.disabled = true; const result = await window.pet.stickerAction(action, entry.id, entry.owner_scope);
      if (!result?.ok) showMsg(result?.error || '操作失败', false); await refreshStickers();
    }; actions.appendChild(button); };
    if (entry.status === 'pending') { addAction('approve', '批准'); addAction('reject', '拒绝'); }
    if (entry.status === 'active') { addAction('disable', '停用'); addAction('delete', '删除'); }
    if (entry.status === 'disabled') { addAction('enable', '启用'); addAction('delete', '删除'); }
    row.appendChild(actions); list.appendChild(row);
  });
}

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
  // 模型下拉重置为「需重新测试」；已配置的模型名保留在 readonly 输入框里
  const sel = $('llm-model-select');
  sel.innerHTML = '<option value="">先测试连接获取模型列表</option>';
  if (profile.has_api_key) setTestStatus(`API Key 已配置（${profile.api_key}）；可点「测试连接」刷新模型列表`, false);
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
  $('mem-status').textContent = `数据库：${mem.db_path || 'data/veranima.db'}`; $('mem-db').textContent = mem.db_path || 'data/veranima.db';
  set('mem-db-path', mem.db_path || 'data/veranima.db'); $('mem-effective').textContent = mem.embedding_model || '未配置';
  bool('att-enabled', att.enabled ?? true); bool('att-paused', att.paused); num('att-scan', att.global_scan_sec, 5); num('att-budget', att.observe_daily_budget, 120);
  const channels = pro.channels || {}; const qqPro = channels.qq || pro; const petPro = channels.pet || pro;
  const tension = cfg.relationship_tension || {};
  const stickers = qq.stickers || {};
  const schedule = cfg.virtual_schedule || {};
  bool('pro-enabled', pro.enabled ?? true); bool('pro-quiet', pro.quiet_hours_enabled ?? true); num('pro-qq-max', qqPro.max_per_day, 2); num('pro-qq-gap', qqPro.min_gap_minutes, 120); num('pro-pet-max', petPro.max_per_day, 2); num('pro-pet-gap', petPro.min_gap_minutes, 30);
  bool('search-enabled', search.enabled ?? false); set('search-base', search.base_url || 'http://127.0.0.1:8080'); num('search-timeout', search.timeout_seconds, 8); num('search-cache', search.cache_ttl_seconds, 900); bool('search-implicit', search.allow_implicit_freshness_search ?? false); bool('search-semantic', search.semantic_locator_enabled ?? false); bool('search-pages', search.fetch_pages ?? false);
  bool('tension-enabled', tension.enabled ?? true); bool('tension-high-proactive', tension.high_tension_proactive ?? false);
  const tasks = cfg.tasks || {}, tk = tasks.hermes || {};
  bool('tasks-enabled', tasks.enabled ?? false); set('tasks-backend', tasks.backend || 'hermes');
  set('tasks-base', tk.base_url || 'http://127.0.0.1:8642'); set('tasks-profile', tk.profile || '');
  bool('tasks-multiplex', tk.multiplex_profiles ?? false);
  set('tasks-workspace', tk.workspace_root || ''); bool('tasks-worktree', tk.worktree_for_code ?? false);
  num('tasks-timeout', tasks.timeout_seconds, 600); num('tasks-approval-timeout', tk.approval_timeout_seconds, 600);
  $('tasks-key').value = '';
  $('tasks-key-status').textContent = tk.has_api_key ? `已配置：${tk.api_key}` : '未配置；填写后保存会写入该配置';
  set('qq-allowed', (qq.allowed || []).join(',')); bool('qq-proactive', qq.proactive); bool('qq-offline', qq.offline_think && qq.offline_think.enabled);
  set('sticker-learning-mode', stickers.learning_mode || (stickers.enabled ? 'review' : 'off'));
  set('sticker-send-rate', stickers.send_rate || (stickers.enabled ? 'normal' : 'off'));
  set('sticker-min-gap', stickers.min_reply_gap ?? 3); set('sticker-ttl', stickers.pending_ttl_days ?? 7);
  set('sticker-max-items', stickers.max_items ?? 100); set('sticker-dir', stickers.dir || 'data/stickers');
  set('qq-image-roots', (qq.image_roots || []).join('; ')); bool('qq-trusted-image-proxy', qq.trusted_image_proxy);
  refreshStickers();
  bool('schedule-enabled', schedule.enabled ?? true); set('schedule-timezone', schedule.timezone || 'system');
  set('schedule-profile', schedule.day_profile || 'auto'); set('schedule-variation', schedule.variation || 'moderate');
  set('schedule-grace', schedule.grace_period_minutes ?? 30); set('schedule-extension', schedule.max_extension_minutes ?? 30);
  set('schedule-share', schedule.self_share || 'low'); set('schedule-curiosity', schedule.curiosity || 'low');
  $('schedule-status').textContent = `当前角色：${cfg.character_card || '未设置'} · 日程${schedule.enabled === false ? '已关闭' : '已开启'}`;
  $('schedule-template-path').textContent = cfg.character_card ? cfg.character_card.replace(/character\.json$/, 'virtual_schedule.json') : '未设置角色卡';
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
function setTestStatus(text, ok) { const el = $('llm-test-status'); el.textContent = text; el.className = ok ? 'hint ok-text' : 'hint'; }
$('llm-test').addEventListener('click', async () => {
  const btn = $('llm-test'); btn.disabled = true;
  setTestStatus('正在测试连接…', false);
  try {
    const payload = { base_url: $('llm-base').value.trim(), api_key: $('llm-key').value.trim() };
    if (!payload.base_url) throw new Error('请先填写 Base URL');
    // key 留空 → 核心自动用本地已保存的密钥测试；框内有则用框内的
    const result = await window.pet.testLlm(payload);
    if (!result || !result.ok) throw new Error((result && result.error) || '连接失败');
    const sel = $('llm-model-select');
    sel.innerHTML = '';
    (result.models || []).forEach((id) => {
      const option = document.createElement('option');
      option.value = id; option.textContent = id;
      sel.appendChild(option);
    });
    if (!result.models || !result.models.length) { setTestStatus('连接成功，但服务未返回任何模型', false); return; }
    // 当前模型在列表中则选中，否则选第一个并同步输入框
    const current = $('llm-model').value.trim();
    sel.value = result.models.includes(current) ? current : result.models[0];
    $('llm-model').value = sel.value;
    setTestStatus(`连接成功，返回 ${result.models.length} 个模型${result.used_saved_key ? '（使用已保存的 Key）' : ''}；已从下拉选定`, true);
  } catch (e) { setTestStatus(`测试失败：${e.message || e}`, false); }
  finally { btn.disabled = false; }
});
$('llm-model-select').addEventListener('change', () => {
  const v = $('llm-model-select').value;
  if (v) $('llm-model').value = v;
});
// 记忆数据库位置：原生浏览框
$('tasks-workspace-browse').addEventListener('click', async () => {
  const picked = await window.pet.pickPath({ type: 'dir', title: '选择任务工作区根目录' });
  if (picked) { $('tasks-workspace').value = picked; showMsg('工作区已选择，保存后生效', true); }
});
$('mem-db-browse').addEventListener('click', async () => {
  const picked = await window.pet.pickPath({ type: 'file', title: '选择记忆数据库文件',
    filters: [{ name: 'SQLite', extensions: ['db', 'sqlite'] }] });
  if (picked) { $('mem-db-path').value = picked; showMsg('数据库路径已选择，保存后重启核心生效', true); }
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
$('sticker-refresh').addEventListener('click', refreshStickers);
$('sticker-dir-browse').addEventListener('click', async () => {
  const picked = await window.pet.pickPath({ type: 'dir', title: '选择表情包目录' });
  if (picked) $('sticker-dir').value = picked;
});
$('qq-image-root-browse').addEventListener('click', async () => {
  const picked = await window.pet.pickPath({ type: 'dir', title: '选择 NapCat 图片缓存根目录' });
  if (picked) $('qq-image-roots').value = picked;
});

$('save').addEventListener('click', async () => {
  const allowed = $('qq-allowed').value.split(',').map((s) => s.trim()).filter(Boolean);
  const data = {
    tts: { base_url: $('tts-base').value.trim(), model: $('tts-model').value.trim(), voice: $('tts-voice').value.trim() },
    stt: { enabled: $('stt-enabled').value === 'true', base_url: $('stt-base').value.trim(), model: $('stt-model').value.trim(), language: $('stt-language').value.trim(), input_device_id: $('stt-device').value },
    memory: { embedding_model: $('mem-embedding').value.trim(), db_path: $('mem-db-path').value.trim(), recall_top_k: Number($('mem-top-k').value) || 5, recall_threshold: Number($('mem-threshold').value) || 0.3, max_injected_chars: Number($('mem-total-budget').value) || 5600, curator_turns: Number($('mem-curator-turns').value) || 8, decay_enabled: $('mem-decay').value === 'true' },
    attention: { enabled: $('att-enabled').value === 'true', paused: $('att-paused').value === 'true', global_scan_sec: Number($('att-scan').value) || 5, observe_daily_budget: Number($('att-budget').value) || 120 },
    search: { enabled: $('search-enabled').value === 'true', base_url: $('search-base').value.trim(), timeout_seconds: Number($('search-timeout').value) || 8, cache_ttl_seconds: Number.isFinite(Number($('search-cache').value)) ? Number($('search-cache').value) : 900, allow_implicit_freshness_search: $('search-implicit').value === 'true', semantic_locator_enabled: $('search-semantic').value === 'true', fetch_pages: $('search-pages').value === 'true' },
    proactive: { enabled: $('pro-enabled').value === 'true', quiet_hours_enabled: $('pro-quiet').value === 'true', channels: { qq: { max_per_day: Number($('pro-qq-max').value) || 2, min_gap_minutes: Number($('pro-qq-gap').value) || 120 }, pet: { max_per_day: Number($('pro-pet-max').value) || 2, min_gap_minutes: Number($('pro-pet-gap').value) || 30 } } },
    relationship_tension: { enabled: $('tension-enabled').value === 'true', high_tension_proactive: $('tension-high-proactive').value === 'true' },
    qq: { allowed, proactive: $('qq-proactive').value === 'true', offline_think: { enabled: $('qq-offline').value === 'true' },
      stickers: stickerSettings(), image_roots: $('qq-image-roots').value.split(';').map((s) => s.trim()).filter(Boolean),
      trusted_image_proxy: $('qq-trusted-image-proxy').value === 'true' },
    virtual_schedule: { enabled: $('schedule-enabled').value === 'true', timezone: $('schedule-timezone').value,
      day_profile: $('schedule-profile').value, variation: $('schedule-variation').value,
      grace_period_minutes: Number($('schedule-grace').value), max_extension_minutes: Number($('schedule-extension').value),
      self_share: $('schedule-share').value, curiosity: $('schedule-curiosity').value },
    tasks: {
      enabled: $('tasks-enabled').value === 'true',
      backend: $('tasks-backend').value,
      timeout_seconds: Number($('tasks-timeout').value) || 600,
      hermes: {
        base_url: $('tasks-base').value.trim(), profile: $('tasks-profile').value.trim(),
        multiplex_profiles: $('tasks-multiplex').value === 'true',
        workspace_root: $('tasks-workspace').value.trim(),
        worktree_for_code: $('tasks-worktree').value === 'true',
        approval_timeout_seconds: Number($('tasks-approval-timeout').value) || 600,
        api_key: $('tasks-key').value.trim(),
      },
    },
    pet: { avatar_height: Number($('avatar-height').value) || 200 },
  };
  const role = $('role-select').value; if (role) data.character_card = `characters/${role}/character.json`;
  $('save').disabled = true; showMsg('正在保存…', true);
  try {
    const result = await window.pet.saveConfig(data);
    if (!result || result.ok !== true) throw new Error((result && result.error) || '核心未连接或保存失败');
    showMsg('已保存，核心正在重启', true); window.pet.restartCore();
  } catch (e) { showMsg(e.message || '保存失败', false); }
  finally { $('save').disabled = false; }
});
