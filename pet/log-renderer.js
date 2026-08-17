// 日志窗口 renderer：接收 log-line/log-history，环形显示
const logEl = document.getElementById('log');
const statusEl = document.getElementById('status');
let lineCount = 0;
const MAX_LINES = 500; // 与 main 环形缓冲一致

function append(entry) {
  const div = document.createElement('div');
  // 着色：核心/核心错误/壳
  if (entry.includes('[core-err]')) div.className = 'core-err';
  else if (entry.includes('[core]')) div.className = 'core';
  else if (entry.includes('[shell]')) div.className = 'shell';
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
