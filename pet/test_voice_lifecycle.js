// 行为测试：pet/main.js 的语音按需启停（touchVoice/keepVoiceAlive/stopVoice）。
// 不启动 Electron——截取 main.js 的语音管理代码块，vm 沙箱注入假 spawn/stop + 虚拟时钟。
// 跑法：node pet/test_voice_lifecycle.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, 'main.js'), 'utf8');
const start = src.indexOf('const VOICE_IDLE_MS');
const end = src.indexOf('function terminateProcessTree');
if (start < 0 || end < start) { console.error('FAIL: voice block not found'); process.exit(1); }
const block = src.slice(start, end)
  .replace('const VOICE_IDLE_MS = 5 * 60 * 1000;', 'const VOICE_IDLE_MS = 100;');  // 虚拟时钟可驱动

const st = { spawnTTS: 0, spawnSTT: 0, stopTTSCalls: 0, stopSTTCalls: 0, ttsProc: null, sttProc: null, isQuitting: false };
let now = 0;
const timers = new Map();
let timerId = 0;
const sandbox = {
  st,
  get isQuitting() { return st.isQuitting; },
  get ttsProc() { return st.ttsProc; }, set ttsProc(v) { st.ttsProc = v; },
  get sttProc() { return st.sttProc; }, set sttProc(v) { st.sttProc = v; },
  startTTS: () => { st.spawnTTS++; st.ttsProc = { fake: 1 }; },
  startSTT: () => { st.spawnSTT++; st.sttProc = { fake: 1 }; },
  stopTTS: () => { st.stopTTSCalls++; st.ttsProc = null; },
  stopSTT: () => { st.stopSTTCalls++; st.sttProc = null; },
  setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { at: now + ms, fn }); return id; },
  clearTimeout(id) { timers.delete(id); },
};
vm.createContext(sandbox);
vm.runInContext(block + '\nthis.touchVoice=touchVoice;this.keepVoiceAlive=keepVoiceAlive;this.stopVoice=stopVoice;', sandbox);
const { touchVoice, keepVoiceAlive, stopVoice } = sandbox;
function advance(ms) {
  const target = now + ms;
  for (;;) {
    const due = [...timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at);
    if (!due.length) break;
    const [id, t] = due[0];
    timers.delete(id); now = t.at; t.fn();
  }
  now = target;
}
const a = (cond, msg) => { if (!cond) { console.error('FAIL: ' + msg); process.exit(1); } };

// 1) 首次活动：拉起 TTS+STT
touchVoice();
a(st.spawnTTS === 1 && st.spawnSTT === 1, 'touchVoice should spawn both once');

// 2) 已拉起后再活动：不重复 spawn，只重置空闲
touchVoice(); touchVoice();
a(st.spawnTTS === 1 && st.spawnSTT === 1, 'touchVoice must not double-spawn');

// 3) 保活续命 → 到期后两者一起释放
advance(50); keepVoiceAlive();
advance(50);
a(st.stopTTSCalls === 0, 'keepVoiceAlive must extend idle window');
advance(100);
a(st.stopTTSCalls === 1 && st.stopSTTCalls === 1 && st.ttsProc === null, 'idle release stops both');

// 4) 释放后再次活动：重新 spawn（循环可用）
touchVoice();
a(st.spawnTTS === 2 && st.spawnSTT === 2, 'restart after release');

// 5) 未激活时 keepVoiceAlive 是 no-op
stopVoice();
advance(1000);
keepVoiceAlive();
advance(1000);
a(st.stopTTSCalls === 2 && st.spawnTTS === 2, 'keepVoiceAlive without active voice is a no-op');

// 6) isQuitting 时 touchVoice 不再拉起
st.isQuitting = true;
touchVoice();
a(st.spawnTTS === 2, 'touchVoice suppressed while quitting');

console.log('voice lifecycle: all assertions passed');
