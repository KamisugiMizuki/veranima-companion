// 设置窗口 renderer（sakura 式：导航页 + 读配置 → 编辑 → 保存 → 重启核心）
const pages = document.querySelectorAll('.nav-item');
pages.forEach((btn) => btn.addEventListener('click', () => {
  pages.forEach((b) => b.classList.remove('is-active'));
  btn.classList.add('is-active');
  document.querySelectorAll('.page').forEach((p) => p.classList.remove('is-active'));
  document.querySelector(`.page[data-page="${btn.dataset.page}"]`).classList.add('is-active');
}));

const $ = (id) => document.getElementById(id);

function showMsg(text, ok) {
  const el = $('msg');
  el.textContent = text;
  el.className = ok ? 'ok' : 'err';
}

function renderRoles(cfg) {
  const sel = $('role-select');
  const roles = (cfg.roles || []).map((r) => ({ id: r.id, name: r.name || r.id }));
  if (roles.length === 0) {
    sel.innerHTML = '<option value="">（无角色，请先创建 characters/<id>/）</option>';
    return;
  }
  const current = (cfg.character_card || '').split('/')[1] || ''; // characters/<id>/character.json
  sel.innerHTML = roles
    .map((r) => `<option value="${r.id}" ${r.id === current ? 'selected' : ''}>${r.name}（${r.id}）</option>`)
    .join('');
}

window.pet.getConfig().then((cfg) => {
  if (!cfg) { showMsg('核心未连接', false); return; }
  const llm = cfg.llm || {}, tts = cfg.tts || {}, stt = cfg.stt || {}, qq = cfg.qq || {}, mem = cfg.memory || {};
  // 角色与布局
  renderRoles(cfg);
  $('card-path').textContent = cfg.character_card || '（未设置）';
  $('llm-summary').textContent = `${llm.base_url || '?'} · ${llm.model || '?'}`;
  // 模型供应商
  $('llm-base').value = llm.base_url || '';
  $('llm-model').value = llm.model || '';
  $('llm-temp').value = llm.temperature ?? '';
  $('llm-key').textContent = llm.api_key || '（未设置）';
  // TTS / STT
  $('tts-base').value = tts.base_url || '';
  $('tts-model').value = tts.model || '';
  $('tts-voice').value = tts.voice || '';
  $('stt-base').value = stt.base_url || '';
  $('stt-model').value = stt.model || '';
  $('stt-language').value = stt.language || '';
  // 系统
  $('qq-allowed').value = (qq.allowed || []).join(',');
  $('qq-proactive').value = String(qq.proactive ?? true);
  $('qq-offline').value = String((qq.offline_think || {}).enabled ?? true);
  $('mem-embedding').textContent = mem.embedding_model || '?';
}).catch(() => showMsg('读取配置失败', false));

$('save').addEventListener('click', async () => {
  const allowed = $('qq-allowed').value.split(',').map((s) => s.trim()).filter(Boolean);
  const data = {
    llm: {
      base_url: $('llm-base').value.trim(),
      model: $('llm-model').value.trim(),
      temperature: parseFloat($('llm-temp').value) || 0.8,
    },
    tts: {
      base_url: $('tts-base').value.trim(),
      model: $('tts-model').value.trim(),
      voice: $('tts-voice').value.trim(),
    },
    stt: {
      base_url: $('stt-base').value.trim(),
      model: $('stt-model').value.trim(),
      language: $('stt-language').value.trim(),
    },
    qq: {
      allowed,
      proactive: $('qq-proactive').value === 'true',
      offline_think: { enabled: $('qq-offline').value === 'true' },
    },
  };
  const role = $('role-select').value;
  if (role) data.character_card = `characters/${role}/character.json`;
  const ok = await window.pet.saveConfig(data);
  if (ok) {
    showMsg('已保存，正在重启核心…', true);
    window.pet.restartCore();
  } else {
    showMsg('保存失败（核心未连接）', false);
  }
});
