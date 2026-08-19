# GUI_SPEC：Electron 桌宠界面实现契约

> 目标：让用户首先感到“她在这里”，其次才看到应用控件。
> 模式：桌宠主窗属于 Experience；聊天/设置/日志属于 Operate。
> 技术：保留现有原生 HTML/CSS/JS + Electron，不迁移 React/Vue，不引入组件框架。
> 参考：sakura 的 SubtitleController/PortraitController/HistoryWindow/统一布局；airi 的 reusable windows、ChatArea、stop-speaking、caption 与独立 chat window。

## 1. 取舍

### 参考行为与目标架构边界

sakura 只提供行为参考：等待、分段、取消令牌、立绘预加载/切换、历史分批渲染、底边锚点和失败显示。airi 只提供行为参考：异步可复用窗口、状态快照/订阅、发送失败恢复草稿、停止说话、静音和输入 IME 处理。veranima 仍使用 Electron + 原生 HTML/CSS/JS；不得复制 Qt 类、Vue/Pinia、同步阻塞弹窗或参考项目的窗口划分。

所有异步回复回调必须携带并校验 `replyId + segmentToken`；所有跨窗口 payload 必须通过 preload 白名单 API。

### 借鉴

- sakura：回复段落序列、等待动效、取消清队列、立绘预加载与 300ms 交叉淡入、历史分批加载/空态/清空确认、布局锚点计算。
- airi：独立 chat/caption/settings 窗口、reusable window 防重复创建、发送失败恢复草稿、停止说话、静音状态、aria-label/pressed、150-250ms 状态动效。

### 不借鉴

- sakura 的单窗口大控制面板与 PySide 组件树。
- airi 的 Vue/Pinia/UnoCSS/插件系统、3D/Live2D 舞台、会话抽屉、多端账户和复杂主题编辑器。

veranima 保持四个窗口：透明主窗、独立聊天、设置、日志。

## 2. 视觉系统

人物是唯一视觉主角。操作窗口采用安静、中性、低装饰的桌面工具语言。

### 2.1 基础 token

建议新增 `pet/theme.css`，所有非透明窗口引用：

```css
:root {
  --bg-app: #f3f5f7;
  --bg-panel: #ffffff;
  --bg-subtle: #eef1f4;
  --text-main: #20242b;
  --text-muted: #66707c;
  --border: #dce1e7;
  --accent: #4f78a8;
  --accent-soft: #e7eef7;
  --success: #3d7a55;
  --warning: #9a6b24;
  --danger: #b14b4b;
  --focus: #2369b7;
  --radius-sm: 4px;
  --radius-md: 7px;
  --shadow-popover: 0 8px 24px rgb(23 33 46 / 14%);
  --motion-fast: 160ms;
  --motion-normal: 220ms;
}
```

角色包允许覆盖：`--accent`, `--accent-soft`, `--bubble-pet`, `--avatar`。禁止覆盖错误/成功语义色、字体尺寸和焦点样式。

### 2.2 字体与密度

- UI：`Segoe UI, Microsoft YaHei, sans-serif`。
- 正文 14px/1.6；辅助文字 12px，但对比度 >=4.5:1；时间戳可 11px，必须 >=3:1 且不承载关键状态。
- 标题仅 16/18px 两级；不用展示字体。
- icon button 32-36px，点击区至少 36px；危险动作不做无标签裸图标。
- border radius 最大 7px，聊天气泡可 8px；不做圆角卡片套圆角卡片。

## 3. 窗口管理

`pet/main.js` 保留统一管理，新增最小 helper：

```js
createReusableWindow(name, factory)
broadcast(channel, payload)
showWindow(name)
hideWindow(name)
```

只抽重复生命周期，不做通用 WindowManager 类。每个命名单例窗口必须遵守：factory 可同步/异步；创建期间共享同一个 Promise；创建失败清空 Promise 允许重试；renderer 不可用或 `closed` 后清空引用并允许重建；show 必须等待 get 完成后再 show/focus。

窗口关闭、隐藏、销毁和应用退出是四条不同路径：隐藏保留窗口与订阅；销毁清理引用/监听器/定时器；应用退出统一 destroy；renderer 崩溃按清理引用后重建处理。窗口启动先调用 `getState()` 快照，再订阅 `onStateChange()`，不能只等待广播。

veranima 当前不实现独立 caption overlay 或 desktop grounding overlay。只借鉴 caption 的 TTL/过期行为，并将字幕放在主窗气泡；不创建额外透明跟随窗口，不引入 BroadcastChannel、MCP polling 或 ghost pointer。

窗口契约：

| 窗口 | 尺寸 | 关闭 | 状态来源 |
|---|---|---|---|
| pet | 动态包络立绘，非 resizable | hide | core state + character |
| chat | 420x640，min 360x480，可调整 | hide | core state/history/reply |
| settings | 680x720，min 600x560 | hide | config snapshot |
| log | 720x560，min 520x400 | hide | log stream |

所有窗口 `ready-to-show` 后再显示；创建失败写 shell log；关闭应用只走托盘“退出”。导航保护和 `contextIsolation:true` 保留。

### 3.1 状态所有权

| 状态 | 唯一所有者 | renderer 是否可修改 |
|---|---|---|
| core online/generating | Python core | 否，只展示 |
| current character | Python core/角色卡 | 否，只缓存资源映射 |
| current turn/segment | Python core 产生，Electron ReplyPresenter 播放 | 只能标记本地播放完成 |
| TTS mute/stop | Electron main | renderer 发动作请求 |
| chat draft/scroll/menu | chat renderer | 是 |
| window bounds/visible | Electron main | renderer 只发 move/fit 请求 |
| history records | main `chat.json`（后续 SQLite） | renderer 请求分页，不直接写文件 |

renderer 不得根据 DOM 自行推断 core 在线状态；main 不得维护第二份角色名/表情状态。

### 3.2 窗口线框

```text
主窗（透明）                 聊天窗 420x640
┌────────────────┐          ┌──────────────────────────┐
│ [首次提示]      │          │ 头像 角色名   状态  历史 音量│
│    ┌────气泡──┐│          ├──────────────────────────┤
│    │ 当前字幕 ││          │                          │
│  [current portrait]       │      消息历史 / 空态      │
│  [next portrait overlay]  │                          │
│             ●状态│          ├──────────────────────────┤
└────────────────┘          │ textarea       停止/发送  │
                            │ inline error / retry      │
                            └──────────────────────────┘

设置窗 680x720              日志窗 720x560
┌────────┬────────────────┐ ┌──────────────────────────┐
│角色    │ 页面标题        │ │ 模块 筛选 搜索 自动滚动  │
│模型    │                │ ├──────────────────────────┤
│声音    │ 当前页表单      │ │ [time][level][module] msg│
│在场隐私│                │ │ ...                      │
│QQ/高级 │                │ │                          │
├────────┴────────────────┤ ├──────────────────────────┤
│ 状态             保存   │ │ 500 行 / 暂停状态        │
└─────────────────────────┘ └──────────────────────────┘
```

## 4. 主桌宠窗口

### 4.1 组件树

```text
#pet-stage
├── #portrait-stack
│   ├── #avatar-current
│   └── #avatar-next
├── #speech-bubble[aria-live=polite]
├── #presence-indicator
└── #first-run-hint
```

`avatar-next` 只用于交叉淡入。立绘按路径缓存，角色切换时更新 profile、默认立绘和映射；解码失败显示具体路径/格式错误，文字仍可用。默认交叉淡入统一为 300ms（重叠比例 0.8）；同一资源不动画。`prefers-reduced-motion` 时立即切换。

### 4.2 布局

新增纯函数 `computePetLayout({portraitWidth, portraitHeight, bubbleWidth, bubbleHeight, inputHeight, inputOffset})`，返回 stage/portrait/bubble/input rect 和全局锚点。主进程负责调整窗口尺寸并保持全局 portrait anchor；renderer 只摆放子控件，不能在 relayout 内再次 resize 主窗口，避免递归和漂移。以立绘底边为锚点，气泡增长时窗口向上扩，不让角色脚底跳动。

- 立绘完整可见，不裁掉脸/手。
- 气泡最大宽 280px，最多约 8 行；打字机过程中按行高逐步增高，受最大高度限制；完成/取消后恢复用户配置高度。超出引导打开聊天窗，不无限扩窗。
- 气泡靠角色头肩侧上方，依据窗口边缘自动左右翻转。
- 主窗透明空白区尽量小，避免挡用户操作。

### 4.3 交互

- 单击：打开 chat。
- 拖动：移动桌宠；拖动阈值 >=5px，避免误开聊天。
- 右键：原生菜单。
- 首次 hint：`点击聊天 · 拖动移动 · 右键菜单`，5 秒后淡出；用户关闭后写 `onboarding.json`。
- 连接状态：小点 + tooltip/accessible text；不在气泡展示技术错误。

## 5. ReplyPresenter（主窗字幕/TTS/立绘协调）

参考 sakura SubtitleController，但用一个 JS 对象实现，不引入类层级：

```js
ReplyPresenter = {
  sequenceId,
  pendingSegments,
  currentSegment,
  show(reply),
  cancel(reason),
  finish(),
}
```

流程：

```text
reply_start → waiting indicator
reply_segment → 预载 portrait/audio，准备下一段
TTS 实际开始回调 → 应用 portrait、开始字幕打字和播放
segment speech_done + audio_done → next
reply_end → idle
cancel/error → 停 timer/audio/queue，保留最后可读文字
```

当前 GPT-SoVITS 整段合成时通常只有一个 segment；接口仍支持多段但不要求逐句并行合成。等待动效使用 `·  ··  ···`，360ms 一帧；不显示虚假的“正在输入”初始状态。

取消语义必须区分：取消回复会停止当前播放、清空当前段和全部待播队列；处理工具确认时只清理尚未开始的排队回复，不误停当前可见台词。每个回调执行前校验 `replyId === activeReplyId && segmentToken === activeSegmentToken`，失效事件不得改立绘、恢复 speaking 或推进下一段。

## 6. 独立聊天窗口

### 6.1 结构

```text
.chat-shell
├── header.character-header
│   ├── avatar + name
│   ├── status[aria-live=polite]
│   └── actions: history / mute
├── main#messages[role=log]
│   ├── empty-state | loading-state | messages
│   └── new-message-marker
├── form.composer
│   ├── label(sr-only)
│   ├── textarea
│   ├── stop-speaking (conditional)
│   └── send
└── inline-error/retry
```

保留 QQ 式左右气泡；角色侧白/轻角色色，用户侧使用 `accent-soft`，不要复制微信荧光绿。

### 6.2 输入行为

- `<textarea>` 1-5 行自增高；发送模式可配置为 `enter`（默认）/`ctrl-enter`/`double-enter`，兼容 Ctrl/Meta 与 Shift。`compositionstart` 到 `compositionend` 期间不得发送，发送前去掉尾部换行。
- 发送前保存 `textToSend` 和目标 sessionId；失败恢复前确认 active session 未删除/重置且仍是目标，否则不得回填草稿。
- generating/speaking 时显示停止按钮；“停止说话”只停 TTS，“取消回复”才取消生成，两个语义不能混用。
- 停止说话只在真实 TTS speaking 状态显示，动作路由到 Electron main 的音频拥有者；`speechMuted` 是 main 持久化共享状态并阻止后续 TTS，即使辅助 renderer 暂时失联也允许切换静音。
- 输入内容不为空才启用发送；offline 时输入可保留，按钮变“重试连接”。

### 6.3 消息状态

`pending/sent/failed/streaming/cancelled`。每条消息有 `messageId`，streaming 原地更新并按 messageId 去重；迟到事件按 sessionId/replyId 丢弃。失败消息保留原文并带“重试”；完成后落时间戳。

### 6.4 历史

- 历史数据读取与 UI 渲染分离：当前存储可一次读取全部历史，UI 每批渲染 40 条并让出事件循环；这不是 offset 分页。未来若改真正分页，必须另定义 store limit/offset 和滚动位置保持规则。
- 刷新、清空、切换角色或关闭窗口时递增 `renderGeneration`；延迟渲染回调执行前检查 generation 和窗口存活，旧内容先隐藏/脱离父节点再销毁，避免与空态叠加。
- 用户在底部时新消息自动滚动；不在底部则显示“有新消息”按钮。
- 空态：角色头像、`还没聊过。先说一句吧。`、2-3 个角色化开场按钮。
- 清空历史放 header 菜单，二次确认；完成后立即空态。
- 连续相同角色消息可省略重复 meta；气泡宽度随窗口重算，约占可用宽度 82%；内容为可选择纯文本；错误为独立 error 气泡；刷新完成后滚到底部。不实现会话抽屉/多会话。

## 7. 设置窗口

保留左侧导航 + 右侧表单，不做 dashboard。

导航改为：角色 / 对话与模型 / 声音 / 在场与隐私 / QQ / 高级。每页只显示相关字段；保存栏 sticky bottom。

组件状态：label 必须关联 input；hint 不替代 label；保存按钮有 idle/saving/saved/error；脏表单关闭时确认。API key 只显示“已配置/未配置”，不把真实 key 放 DOM。

“在场与隐私”必须包含：视觉注意力开关、暂停、敏感窗口说明、原图不保存说明、主动消息上限/静默时段。

## 8. 日志窗口

日志是开发工具，允许高密度：

- 工具栏：模块筛选、文本搜索、自动滚动、清空显示、打开日志目录。
- 清空只清 UI，不删文件；按钮文案明确。
- error/warn/info 用颜色 + 文本标记，不只靠颜色。
- 默认最近 500 行，暂停自动滚动后不抢位置。

## 9. 状态与文案

系统状态文案是 UI 责任，不强行角色化：

| 状态 | 文案 | 动作 |
|---|---|---|
| connecting | 正在连接 | 无 |
| online | 在线 | 无 |
| generating | 正在想 | 取消回复 |
| speaking | 正在说 | 停止说话 |
| offline | 连接断开 | 重试连接 |
| tts_failed | 语音没有播放，文字仍可阅读 | 重试语音 |
| reply_failed | 这条回复没有完成 | 重试 |

不要用“她睡着了”掩盖核心崩溃。角色气泡负责人物表达，状态栏负责产品诚实。

失败同步契约：生成失败时取消等待/播放流程、清理临时 streaming 节点、写入 error 消息并恢复非 busy；TTS 失败保留字幕并显示可隐藏状态；立绘失败不阻塞文字。重试必须区分“重发原用户消息”和“仅重试 TTS”，默认重试 TTS 不重复调用 LLM。

## 10. 动效

- 通用 160-220ms；立绘 crossfade 300ms；等待点 360ms。
- 动效只表达状态：窗口显示、立绘切换、消息出现、错误/成功。
- 禁止背景粒子、持续漂浮、装饰性 loading、页面入场编舞。
- `prefers-reduced-motion: reduce` 时关闭打字机/crossfade/scale。

## 11. 无障碍与键盘

- 可交互项用 button/input/textarea，不用 div onclick。
- 所有 icon button 有 title + aria-label；toggle 有 aria-pressed。
- `:focus-visible` 2px `--focus` outline；不得 outline:none 后无替代。
- 消息列表 role=log；状态 aria-live=polite；错误 role=alert。
- 正文对比 >=4.5:1；非文本状态不只靠颜色。
- Escape 关闭菜单/弹窗；Ctrl+L 聚焦日志搜索；Ctrl+, 打开设置（后续实现时可选）。
- preload 白名单最小 API：`getState()`、`onStateChange()`、`sendChat()`、`cancelReply()`、`stopSpeaking()`、`setSpeechMuted()`、`openWindow(name)`；renderer 不接触 fs、WebSocket 或任意 ipc channel。

## 12. 文件与实现分批

建议新增：

```text
pet/theme.css
pet/chat.css
pet/gui-state.js       # 窄：状态归一化/文案/广播，不放业务
pet/reply-presenter.js # 主窗 segment/音频/立绘协调
```

不要引入 bundler。实施顺序：

1. GUI-1：统一状态快照/广播 + reusable window 并发/重建 + chat 真实状态 + 错误/重试。
2. GUI-2：chat 结构/textarea/消息状态/历史分批/空态。
3. GUI-3：主窗布局函数 + 首次提示 + 立绘 crossfade + ReplyPresenter。
4. GUI-4：settings 隐私/主动性页面 + 保存状态。
5. GUI-5：log 筛选/搜索/自动滚动 + a11y/对比度收尾。

每批：`node --check` + Python 协议定向测试 + 全量 pytest；真实 Electron 用 background computer capture 验证主窗/聊天/设置三种窗口。

## 13. 验收矩阵

| 场景 | 必须结果 |
|---|---|
| 首次启动 | 5 秒内看懂点击/拖动/右键 |
| 核心连接 | 所有窗口同一状态，无假“正在输入” |
| 发送失败 | 草稿/消息保留，有重试 |
| TTS 失败 | 文字不消失，有静默降级 |
| 用户打断 | 旧音频/队列清空，迟到事件被 turn_id 丢弃 |
| 历史 500 条 | 首屏不一次渲染全部，滚动位置稳定 |
| 换角色 | 名称/头像/主题色/立绘/声音同步 |
| reduced motion | 无强制打字机/crossfade |
| 键盘/NVDA | 可发送、停止、重试、读状态和消息 |
| 多屏/DPI | 立绘底边稳定，气泡不出屏 |
