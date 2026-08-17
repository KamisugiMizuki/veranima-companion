// 设置窗口 renderer：读配置（打码 key）→ 编辑 → 保存 → 请求重启核心
const el = {
  base: document.getElementById('base-url'),
  model: document.getElementById('model'),
  key: document.getElementById('masked-key'),
  allowed: document.getElementById('allowed'),
  msg: document.getElementById('msg'),
};

function showMsg(text, ok) {
  el.msg.textContent = text;
  el.msg.className = ok ? 'ok' : 'err';
}

// 读配置（main 转发 WS → 核心 get_config）
window.pet.getConfig().then((cfg) => {
  if (!cfg) { showMsg('核心未连接', false); return; }
  el.base.value = cfg.llm.base_url || '';
  el.model.value = cfg.llm.model || '';
  el.key.textContent = cfg.llm.api_key || '（未设置）';
  el.allowed.value = (cfg.qq.allowed || []).join(',');
}).catch(() => showMsg('读取配置失败', false));

document.getElementById('save').addEventListener('click', async () => {
  const allowed = el.allowed.value.split(',').map(s => s.trim()).filter(Boolean);
  const ok = await window.pet.saveConfig({
    llm: { base_url: el.base.value.trim(), model: el.model.value.trim() },
    qq: { allowed },
  });
  if (ok) {
    showMsg('已保存，正在重启核心…', true);
    window.pet.restartCore(); // 核心重启后自动重连，配置生效
  } else {
    showMsg('保存失败（核心未连接）', false);
  }
});
