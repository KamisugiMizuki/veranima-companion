// 日志窗口 renderer：接收 log-line/log-history，环形显示 + 搜索/模块筛选（GUI-5）
const logEl = document.getElementById('log');
const statusEl = document.getElementById('status');
const searchEl = document.getElementById('search');
const modEl = document.getElementById('mod');
let lineCount = 0;
const MAX_LINES = 500; // 与 main 环形缓冲一致
let paused = false;

function moduleOf(entry) {
  if (entry.includes('[core-err]')) return 'core-err';
  if (entry.includes('[core]')) return 'core';
  if (entry.includes('[shell]')) return 'shell';
  if (entry.includes('[tts]')) return 'tts';
  return 'other';
}

function visible(entry) {
  if (paused) return false;
  const mod = modEl.value;
  if (mod !== 'all' && moduleOf(entry) !== mod) return false;
  const q = searchEl.value.trim().toLowerCase();
  if (q && !entry.toLowerCase().includes(q)) return false;
  return true;
}

function append(entry) {
  if (!visible(entry)) return;
  const div = document.createElement('div');
  div.className = moduleOf(entry);  // 着色：核心/核心错误/壳/TTS
  div.textContent = entry;
  logEl.appendChild(div);
  lineCount++;
  while (lineCount > MAX_LINES) {
    logEl.removeChild(logEl.firstChild);
    lineCount--;
  }
  logEl.scrollTop = logEl.scrollHeight; // 自动滚底
}

window.pet.onLogLine((entry) => {
  append(entry);
  statusEl.textContent = `${lineCount} 行`;
});

window.pet.onLogHistory((history) => {
  logEl.innerHTML = '';
  lineCount = 0;
  history.forEach(append);
  statusEl.textContent = `${lineCount} 行`;
});

document.getElementById('clear').addEventListener('click', () => {
  logEl.innerHTML = '';
  lineCount = 0;
  statusEl.textContent = '0 行';
});
// GUI-5：搜索/筛选变化 → 重放内存历史（main 有环形缓冲，这里重放当前已渲染的）
searchEl.addEventListener('input', () => {
  paused = true;
  const shown = Array.from(logEl.children).map((d) => d.textContent);
  logEl.innerHTML = '';
  lineCount = 0;
  shown.forEach((e) => {
    if (visible(e)) {
      const div = document.createElement('div');
      div.className = moduleOf(e);
      div.textContent = e;
      logEl.appendChild(div);
      lineCount++;
    }
  });
  paused = false;
  statusEl.textContent = `${lineCount} 行`;
});
modEl.addEventListener('change', () => {
  searchEl.dispatchEvent(new Event('input'));
});
// 暂停自动滚动（鼠标悬停即停，离开恢复）
logEl.addEventListener('mouseenter', () => { paused = true; });
logEl.addEventListener('mouseleave', () => { paused = false; logEl.scrollTop = logEl.scrollHeight; });
