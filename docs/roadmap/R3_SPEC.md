# R3 专项：桌宠作为“在场的人”

> 目标：Electron 只做身体与媒介，不持有 Agent 业务状态。
> 现有复用：`pet/main.js`, `preload.js`, `renderer.js`, `chat.html`, `chat-renderer.js`, `pet_server.py`。
> 参考：sakura 的角色包/历史/音频组织；airi 的可取消回复、异步历史批处理和运行时事件。
> GUI 组件、视觉 token、窗口尺寸、动效、无障碍和分批实现以 `docs/desktop/GUI_SPEC.md` 为唯一真值；本文件只规定进程、协议和产品状态。

## 1. 进程与协议

Python 核心：Agent、MemoryStore、AgentState、SceneLock、Arbitrator、AttentionScheduler、TTS 调度。

Electron：BrowserWindow、IPC、WS 转发、立绘/气泡、音频、聊天历史展示。

核心 WS 事件统一带：`event_id, turn_id, ts, type, payload`。最小事件：

```json
{"type":"state","payload":{"status":"online","character":"Yuki","turn_id":""}}
{"type":"reply_start","payload":{"turn_id":"abc","channel":"tts"}}
{"type":"reply_segment","payload":{"turn_id":"abc","text":"...","audio_b64":"","tone":"","portrait":""}}
{"type":"reply_end","payload":{"turn_id":"abc"}}
{"type":"reply_error","payload":{"turn_id":"abc","code":"tts_failed","recoverable":true}}
{"type":"reply_cancelled","payload":{"turn_id":"abc"}}
```

兼容旧 `speak/speak_chunk/speak_done` 两个版本，迁移完成后删除旧路径。

## 2. 状态广播

`main.js` 提供唯一 `broadcastToWindows(channel, payload)`，不能只给 `win`。状态状态机：

```text
connecting → online → generating → speaking → online
       ↘ offline ↔ reconnecting
任意生成态 → failed/cancelled → online
```

chat window 初始显示“连接中…”，不得静态显示“正在输入…”。收到 core state 后同步标题、状态点、输入可用性和 aria-live。

## 3. 主窗

- 透明置顶、拖动、托盘常驻、位置持久化：保留现有实现。
- 点击形象打开 chat window；右键为辅助入口。
- 首次使用提示存 `userData/onboarding.json`，关闭后永久不再显示。
- 连接状态同时提供颜色、可读文本/tooltip 和无障碍名称。
- 气泡只显示 Reply segment，错误在聊天窗显示；TTS 失败不清空文字。

## 4. 聊天窗

DOM 最小契约：

```html
<header aria-label="角色状态">角色名 + 状态</header>
<main id="msgs" role="log" aria-live="polite"></main>
<form id="inputbar"><label for="input">发送消息</label><textarea id="input"></textarea><button>发送</button></form>
```

功能状态：

| 状态 | 输入 | 按钮 | 消息区 |
|---|---|---|---|
| offline | 可编辑 | 重试连接 | 错误 + 重试 |
| online | 可编辑 | 发送 | 空态/历史 |
| generating | 可继续输入或取消，配置决定 | 停止 | 思考中 |
| speaking | 可取消 | 停止 | segment + 音频 |
| failed | 保留草稿 | 重试 | 角色化失败 |

历史：当前先使用 `userData/chat.json` 兼容现状，最多 500 条；展示端每批 40 条，后续再迁移 SQLite。清空必须确认，并广播刷新所有窗口。角色切换时记录 `character_id`，显示当前角色范围。

## 5. TTS/立绘资源

角色包结构复用现有 `characters/<id>/`：

```text
character.json
portraits/
voice/refs/
voice/models/
```

Electron 不硬编码 Yuki；启动时由核心下发 `character_id/name/avatar_map/voice_profile`。声音、气泡、portrait 必须来自同一个 `turn_id + segment_id`。

## 6. 配置

```yaml
pet:
  enabled: true
  avatar_height: 200
  onboarding: true
  chat_history_limit: 500
  chat_batch_size: 40
  keep_text_on_tts_failure: true
  autoplay: true
```

## 7. 测试/验收

定向：`tests/test_pet_server.py`, 新增 `tests/test_pet_protocol.py`；JS 用 `node --check`，若引入前端测试再用已有 Node 环境，不新增框架。

验收：首次 10 秒知道入口；角色切换全链路一致；chatWin 收到在线/断线；TTS 失败文字保留；取消后旧音频不播；空态、重试、清空确认和历史恢复可用。

GUI 视觉与交互另按 `GUI_SPEC.md` 的验收矩阵执行，避免两份文档重复维护 CSS 和组件细节。

暂缓：Live2D、拖文件、多角色并聊。语音输入、搜索历史已在当前 Electron 聊天链路接入；实时字幕仍由 `STT_SPEC.md` 单独列为暂缓。
