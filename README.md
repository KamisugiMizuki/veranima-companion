# Veranima Companion

Veranima 是一个本地优先的 AI 陪伴系统。它把固定角色卡、长期记忆、关系状态、桌面存在感和 QQ 文字聊天接到同一个 Python Agent 上，再按媒介分别渲染。

核心目标不是让回复“更像 AI”，而是让同一个角色在长期互动中保持可解释的连续性：记得共同经历，会受当前精力和情绪影响，也会对关系中的沟通落差产生有限、可恢复的反应。

> 当前项目是 Windows 桌面端优先的开发工作区。远程 OpenAI 兼容 API、NapCatQQ、本地 STT、GPT-SoVITS 和桌宠 Electron 壳都属于可选运行组件；仓库不包含真实 API key、模型权重、运行数据库或本地语料。

## 当前能力

| 模块 | 当前行为 |
|---|---|
| 统一 Agent | 角色卡、记忆、精力、情绪、依恋度、PAD、关系模型和表达计划由同一 Agent 管理 |
| 角色卡 | 支持 `characters/yuki/` 与 `characters/zima/`，兼容 Character Card V3 和旧版自定义 JSON |
| CLI 对话 | `python -m veranima.cli` 进入交互式对话；也提供角色、文风、共同创作和任务命令 |
| QQ | NapCatQQ OneBot v11 反向 WebSocket，默认监听 `127.0.0.1:8099`，仅支持白名单私聊 |
| Windows 桌宠 | Electron 透明置顶窗口、立绘、拖拽、独立聊天窗口、设置窗口、日志窗口和右键菜单 |
| 记忆 | SQLite、FTS5、sqlite-vec、本地 embedding、五层记忆、版本链、混合召回和 Context Brief |
| 关系张力 | TV 不满值、事件账本、每日增减上限、6 小时衰减、重启恢复、修复阶段和 QQ 期待闭环 |
| 主动消息 | QQ 与桌宠分别维护自己的 `min_gap_minutes`；两个通道互不影响，不存在同源间隔或全局间隔 |
| 视觉注意力 | 扫视/注视状态机、前台窗口与鼠标焦点、敏感窗口策略、观察预算和视觉候选 |
| 图片与表情 | QQ/桌宠图片统一校验；静态表情包可标注、检索和发送；动图只用于当轮理解，不进入静态库 |
| 语音 | 本地 SenseVoiceSmall STT；GPT-SoVITS 作为桌宠 TTS 链路。STT 结果只回填输入框，不自动发送 |
| Style Learning | 本地私有语料清洗、分句、弱标注、抽样复核和聚合 StyleBrief；原文与产物留在 ignored 数据目录 |
| 共同创作 | Project、Scene、Decision、Artifact、Thread 和确认后的共同经历，可通过 CLI 操作 |
| 外部任务 | 可选 R5 管道：模糊指令转工单，再交给外部 `dsh` CLI；默认不启用 |
| 联网搜索 | 本地 SearXNG、显式/时效/未知实体兜底搜索、动态状态语义定位、证据注入、缓存、来源质量、冲突提示和有界正文补充 |

## 架构

```text
Windows Electron 壳（pet/）
├── 透明置顶桌宠窗口
├── QQ 风格独立聊天窗口
├── 设置窗口 / 日志窗口
└── 启动并管理 Python 核心与本地语音服务

Python 核心（src/veranima/）
├── Agent
│   ├── 角色卡与 Persona Brief
│   ├── AgentState / PAD / RelationshipModel
│   ├── RelationalTension（TV 不满值）
│   ├── SQLite 记忆与 Context Brief
│   └── ResponsePlan / 通道渲染
├── pet_server：桌宠 WebSocket，127.0.0.1:8765
├── QQAdapter：OneBot v11，默认 127.0.0.1:8099
├── attention/：视觉注意力循环
├── stt/：SenseVoiceSmall OpenAI 兼容服务，默认 9890
└── tts/：本地 TTS 服务接口，默认 9880
```

桌宠壳负责窗口和子进程生命周期；Python 核心负责 Agent、记忆和对话。桌宠模式下，QQ Adapter 可以由核心进程挂载，与桌宠共用 Agent、记忆和锁。

## 环境要求

- Windows 10/11
- Python `>=3.11`
- `uv`
- Node.js：当前启动脚本默认查找 `C:\Program Files\nodejs\node.exe`
- Git Bash、Windows Terminal 或其他能运行 POSIX 命令的终端
- 远程 OpenAI 兼容聊天 API，或你自行准备的兼容服务
- 使用本地 embedding 时，需要准备 `data/models/bge-m3/`
- 使用桌宠时，需要准备 Electron 与 `ws` 运行依赖
- 使用本地语音时，需要准备 SenseVoiceSmall、GPT-SoVITS runtime 和相应模型

### 桌宠 Node 依赖说明

`pet/package.json` 当前只声明 Electron 启动脚本，`node_modules/` 被 Git 忽略，仓库没有把 Electron 与 `ws` 依赖提交进来。启动器会检查以下路径：

```text
pet/node_modules/electron/cli.js
pet/node_modules/ws/package.json
```

缺失时请准备实际的 Electron 和 `ws` 目录，或按启动器提示建立 junction。不要把 `node_modules/`、模型和 runtime 加入 Git。

## 安装

以下命令按 Git Bash 写法。PowerShell/CMD 请将路径和环境变量语法换成对应写法。

```bash
cd /d/Hermes_workspace/veranima

uv venv .venv
uv pip install --python .venv/Scripts/python.exe -e .

test -f config/config.yaml || cp config/config.example.yaml config/config.yaml
```

只在本地配置不存在时复制示例，避免覆盖已有 API key 和运行参数。`config/config.yaml` 是本地配置，已被 Git 忽略。至少检查并修改：

```yaml
llm:
  base_url: "https://你的 OpenAI 兼容服务/v1"
  model: "你的模型名"
  api_key: ""

character_card: "characters/yuki/character.json"

memory:
  embedding_model: "local:data/models/bge-m3"
```

API key 只写入本地 `config/config.yaml` 或通过设置页保存，不要写入 `config/config.example.yaml`、README、测试、日志或提交内容。角色路径可改为 `characters/zima/character.json`。

默认示例中的 `character_card` 是兼容占位路径；如果它不存在，Agent 会回退到内置默认角色。要使用仓库中的角色，请明确改成 `characters/yuki/character.json` 或 `characters/zima/character.json`。

日常配置优先使用桌宠设置页，包括模型 profile、TTS/STT、记忆、视觉观察、QQ、双通道主动间隔、关系张力和联网搜索。API key 在读取时只显示掩码。

### 模型配置 profile

设置页支持多份 OpenAI 兼容模型配置：每份保存名称、Base URL、模型名、temperature、max tokens、超时和 API key。可以新增、编辑、切换和删除非当前 profile；当前 profile 和最后一份 profile 不能删除。旧版顶层 `llm` 字段保留兼容，真实 key 只存在本地配置。

## 启动

### Windows 桌宠

推荐无控制台启动：

```text
双击项目根目录的 run_pet.vbs
```

兼容入口：

```text
双击项目根目录的 run_pet.bat
```

开发调试入口：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe scripts/run_pet.py
```

`run_pet.vbs` 会检查 `.venv/Scripts/pythonw.exe`、Electron 和 `ws` 是否存在。`scripts/run_pet.py` 会启动 Electron 壳，Electron 再启动 Python 核心，并按配置管理 TTS/STT。启动前会检查并清理确认属于 Veranima 的残留进程，避免端口被孤儿进程占用。

### CLI 对话

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli
```

CLI 启动时会检查 LLM 可用性和模型；配置不正确时会直接给出连接或模型错误。对话中支持的主要斜杠命令包括 `/memory`、`/export`、`/forget`、`/style`、`/status`、`/reset --style` 和 `/quit`。

先查看 CLI 总帮助：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli --help
```

### QQ 独立入口

1. 启动 NapCatQQ。
2. 配置反向 WebSocket 客户端连接：`ws://127.0.0.1:8099/ws`。
3. 在 `config/config.yaml` 设置：

```yaml
qq:
  enabled: true
  allowed_qq: [你的QQ号]
```

白名单为空时拒绝所有私聊。启动 QQ Adapter：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.qq
```

桌宠模式下通常不需要单独启动它：核心会按 `qq.enabled` 挂载 QQ Adapter。

## 角色管理

当前仓库内置角色：

```text
characters/yuki/  水上由岐
characters/zima/  Зима
```

命令均以实际 CLI 为准：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli roles list
.venv/Scripts/python.exe -m veranima.cli roles switch yuki
.venv/Scripts/python.exe -m veranima.cli roles clone yuki my-role
.venv/Scripts/python.exe -m veranima.cli roles export yuki yuki.charpkg
.venv/Scripts/python.exe -m veranima.cli roles import yuki.charpkg
```

切换角色后需要重启核心。记忆与关系状态默认继续保留，这是当前产品设计，不是角色包隔离数据库。

角色卡支持的运行时边界包括：

- 核心人格字段：`core_drives`、`value_order`、`inner_tensions`、`long_term_desires`、`relationship_expectation`；
- 沟通风格与语言风格；
- 语气标签和立绘表达映射；
- 初始好感、虚拟背景、禁忌和角色级关系表达模式；
- 可选 `tension_expression.mode`：`restrained`、`direct`、`hurt`、`neutral`。

系统级身份与现实行动边界不会因为更换角色卡而被覆盖。

## 记忆、关系与主动性

### 记忆

记忆数据默认位于 `data/veranima.db`，运行产物不入 Git。系统使用：

- `core_profile`：常驻档案；
- `semantic`：长期事实；
- `episodic`：共同经历、关系事件和关系张力事件账本；
- `procedural`：承诺与协作规则；
- `session`：短期会话内容；
- SQLite FTS5、sqlite-vec 和本地 embedding；
- 版本链：纠正生成新版本，不覆盖旧证据；
- Context Brief：按层和字符预算选择相关内容注入 prompt；
- 时间衰减、确定性整理器和用户控制导出/遗忘。

默认 embedding 配置指向 `data/models/bge-m3`。模型文件不在仓库内；没有可用本地模型时，召回能力会降级或按配置尝试其他 provider。

### AI 不满值 / 关系张力

关系张力是近期互动落差的快变量，不是 `attachment`、PAD 或 `conflict_tension` 的替代品。

已实现行为：

- TV 0–100 与 `calm / guarded / cool / repair / high` 阶段；
- 事件证据、置信度、去重键和 episodic 账本；
- 每日负向/正向增量上限；
- 每完整 6 小时衰减一次；
- Agent 重启后恢复 TV、事件去重和当天增量额度；
- QQ 主动问题的 `pending → replied/expired` 期待状态；
- 24 小时未回复只对明确期待回复的主动问题生效；
- 未闭合的 QQ 直接问题超过配置窗口可形成一次中途离场事件；
- 用户主动回来、认真回答问题或明确解释后逐步修复；
- 高张力可生成一次修复型主动机会；
- 用户确认关系事件候选后，才允许有限更新 `conflict_tension`。

边界：

- QQ 和桌宠只计算自己的 `min_gap_minutes`，每个通道只有这一条硬间隔，彼此不阻塞；
- TV 不拒绝用户主动请求，不改变通道间隔，不允许辱骂、威胁或强制道歉；
- 高张力修复型主动默认关闭，可在设置页显式开启；
- 已读不回不实现，因为当前 OneBot 链路没有可靠已读证据；
- 关系事件候选已有后端确认接口，但当前没有独立的用户确认编辑器。

### 主动消息

两个通道只保留各自一条硬间隔：

```yaml
proactive:
  channels:
    qq:
      min_gap_minutes: 120
    pet:
      min_gap_minutes: 30
```

除此之外还会受各自每日上限、静默时段、用户状态和内容相关性限制。不存在：

```text
source_gap_minutes
全局主动间隔
跨通道主动间隔
同源退避
```

QQ 主动策略只读取 `messages.channel == "qq"` 的历史，不把桌宠 TTS 或视觉观察当成 QQ 素材。桌宠主动也不会更新 QQ 的通道间隔。

消息表保留 `channel` 和 `created_at`：QQ、桌宠和 CLI 的历史可以按通道隔离召回，并在 prompt 中显示本地时间。主动反馈同样按 `channel` 持久化，QQ/桌宠分别维护 pending、responded、expired 和每日 Gate 状态。

### 联网搜索

联网搜索使用本机 SearXNG，默认地址为 `http://127.0.0.1:8080`。默认关闭，需要在设置页或本地 `config/config.yaml` 开启：

```yaml
search:
  enabled: true
  base_url: "http://127.0.0.1:8080"
  allow_user_explicit_request: true
  allow_implicit_freshness_search: false
  semantic_locator_enabled: false
  fetch_pages: false
```

已实现的搜索行为：

- 显式“帮我查/搜一下/给我来源”触发搜索；
- 可选的“最近/目前/最新/更新/风评”等时效搜索；
- 新游戏、新软件、事件等疑似未知实体的事实兜底搜索；
- 发布年份、发布日期、上线时间等事实查询联网核对；
- 中文相对时间会归一化为绝对日期：今年/去年/前年/明年/后年、上/下个月、明天、N 天前、近 N 天、后面 N 天、过去 N 个月等；
- 绝对日期会同时写入搜索词和 EvidencePack 时间范围，例如“明天杭州天气”会附加具体年月日；
- “五天前”表示单个历史日期，“近五天内”表示包含今天的五日窗口，“后面五天里”表示从明天开始的五日窗口；
- 默认以 `2025-01-31` 为模型知识截止线：请求范围进入 2025-02-01 及以后时强制搜索，即使没有“最近/现在”等词；
- 日期词不等于事实查询：生活安排和承诺（如“明天补高数”“今晚早点睡”）不会因截止线联网；天气、节日、活动、新番等动态事实仍会搜；
- “再试试/再试试看”在上一轮有搜索主题时会复用该主题并强制刷新，没有上一轮搜索时不会凭空联网；
- “不行不行，你再试试”这类带情绪前缀、尾部为重试短句的表达也会复用上一轮搜索；
- 动态状态语义定位：识别静态知识、动态状态、动态事件、观点/评价和模糊指代；
- “现在/最近/最新”时间锚定、最多 3 个策略查询和最多 1 个验证查询；
- 从结果中提取候选活动/事件名，无法区分时让模型向用户澄清；
- 结果 HTML 清洗、URL 追踪参数移除、私网/回环地址拒绝、查询隐私 fail-closed；
- 对动态查询执行结果相关性过滤，丢弃与实体无关的 MAC/股票等搜索后端噪声；
- 15 分钟进程内缓存、强制刷新、发布日期过滤、来源质量排序和冲突提示；
- EvidencePack 只注入当前 prompt，不写入长期记忆；TTS 机械移除来源 URL，不朗读链接；
- 摘要不足时可选补充最多两条公开 HTML 页面正文，限制大小和字符数，失败自动回退。

页面正文补充默认关闭：

```yaml
search:
  fetch_pages: true
  max_page_results: 2
  page_char_limit: 1200
  max_page_bytes: 524288
```

- 搜索失败、超时、空结果或页面抓取失败都会回退到普通对话，不把未经处理的搜索结果直接发给用户。搜索不会由桌宠视觉观察、TTS 或主动消息后台任务触发。
- 动态查询会丢弃标题、摘要和 URL 都不包含目标实体的后端噪声，例如 MAC 地址或股票教程结果；
- 如果模型回显内部风格参数或搜索规则，统一回复出口会清理这些内部 prompt 文本。
- 如果模型输出“思考过程”、`最终调整`草稿或 `<think>...</think>`，只保留最终答案；清理后才保存历史、渲染 QQ/桌宠回复。
- IM/TTS 主对话现在统一要求结构化 JSON `{"segments":[...]}`：IM/单语 TTS 使用 `text`，双语 TTS 使用 `ja`（送语音）+ `zh`（显示）；OpenAI 兼容 API 支持时发送 `response_format=json_object`，不支持则自动回退普通请求并继续清理。
- HTTP 200 但模型偶尔返回普通文本或错误 JSON 时不重复请求：交给对应通道解析器提取可见内容；混合“分析文本 + 多个 JSON 候选”时取最后一个有可见 `segments` 的对象；只有真正空响应、网络、鉴权或服务端错误才显示卡顿兜底。

## 文风学习

文风学习是本地私有功能。只有在你确认拥有明确的本地分析授权后，才导入语料：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli style import \
  my-corpus notes.txt more.md \
  --source "用户自有文本" \
  --owner user \
  --license private-local-consent \
  --consent

unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli style review-export my-corpus --limit 24
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli style review-apply my-corpus
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli style activate my-corpus
```

其他管理命令：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli style status my-corpus
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli style deactivate my-corpus
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli style delete my-corpus
```

运行时只注入聚合后的 StyleBrief，不注入原文，不把语料作者的事实、身份或立场写进角色记忆。语料、复核队列和产物都应留在被 Git 忽略的 `data/` 下。

## 共同创作与任务管道

共同创作 CLI 的参数形式：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli create project story "屋顶短篇" "完成初稿"
.venv/Scripts/python.exe -m veranima.cli create scene <project_id> "第一幕" "完成开场"
.venv/Scripts/python.exe -m veranima.cli create decision <project_id> <scene_id> "采用哪个结尾" "开放式" <message_id>
.venv/Scripts/python.exe -m veranima.cli create artifact <project_id> <scene_id> "草稿" "正文内容" <message_id>
.venv/Scripts/python.exe -m veranima.cli create thread <project_id> "待补设定" "下次继续"
.venv/Scripts/python.exe -m veranima.cli create confirm <project_id> "完成开场" <message_id>
.venv/Scripts/python.exe -m veranima.cli create list
```

R5 任务管道是可选能力：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli task "帮我查一下今天的天气"
```

它需要外部 `dsh` CLI。未安装时命令会明确退出，不会伪造任务已执行。`dsh` 的独立环境变量和安装方式不属于 Veranima 核心配置。

## 图片、视觉与语音

### 图片与表情

- QQ 支持 OneBot image segment、CQ image 字符串、`file/path/url` 和 `get_image` 回查；
- QQ 原生 `face` 段和 CQ face 会转换为 `[QQ表情：...]` 语义占位，混合文字不会丢失；
- 桌宠聊天支持图片输入，持久化记录使用占位符，不把 base64 写进聊天历史；
- 图片会先经过 MIME、文件头、大小、像素数和来源边界校验；QQ 本地回查路径必须落在配置的 `image_roots` 白名单内，远程图片主机还要通过允许列表和公网 IP 校验；
- 动态 GIF/WebP 只用于当前轮视觉理解，不写入静态表情库；
- 静态表情库需要多模态模型完成标注，并按情绪/情境匹配发送。

### 视觉注意力

视觉注意力默认受配置和隐私策略控制。启用后，系统通过前台窗口、鼠标焦点、显著度和扫视/注视状态机决定是否观察；敏感窗口会在捕获前拦截。原始截图默认不保存到项目数据目录，观察预算和策略见 `config/config.yaml` 的 `attention` 段。

### STT

SenseVoiceSmall 服务默认使用 `127.0.0.1:9890`，支持 `language=auto` 和中英日混说。桌宠录音链路是：

```text
MediaRecorder → Electron preload IPC → 本地 STT HTTP → 回填输入框
```

不会自动发送识别结果。模型文件和 runtime 不在 Git 中。单独调试服务时可参考：

设置页保存 STT 开关、地址、模型、语言和输入设备后会重启核心并按开关启动/停止 sidecar；关闭 STT 不会阻塞普通文字聊天。

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.stt.server \
  --model-path data/models/sensevoice-small \
  --port 9890
```

### TTS

桌宠默认设计为本地 GPT-SoVITS v4，服务端口通常为 `127.0.0.1:9880`。GPT-SoVITS runtime、模型权重、参考音频和训练产物均不进入仓库。没有可用 TTS 时保留文字气泡。

## 数据与隐私

以下内容只应存在本地，并且不应提交：

```text
config/config.yaml
data/
logs/
tts/gpt-sovits/
本地模型权重
本地语料、复核队列和 Style Learning 产物
```

实际运行时还会在 Electron 的 `app.getPath('userData')` 下保存聊天记录、窗口位置和聊天图片缓存。远程 LLM 模式会把当轮 prompt、选中的历史和用户主动发送的图片交给配置的远程服务；如果要求数据不出本机，应使用本地兼容服务，并同时准备本地 embedding。

保留的硬边界：不代为现实联系、不约线下活动、不声称参与现实活动、不编造可验证精确外部事实、不绕过隐私删除。

## 文档索引

### 总体与阶段契约

- [DESIGN.md](docs/DESIGN.md)：总体设计与实现顺序
- [R0_SPEC.md](docs/R0_SPEC.md)、[R1_SPEC.md](docs/R1_SPEC.md)、[R2_SPEC.md](docs/R2_SPEC.md)、[R3_SPEC.md](docs/R3_SPEC.md)、[R4_SPEC.md](docs/R4_SPEC.md)、[R5_SPEC.md](docs/R5_SPEC.md)：阶段契约
- [MEMORY_SPEC.md](docs/MEMORY_SPEC.md)：记忆、召回、版本链和整理
- [PERSONA_LOOP_SPEC.md](docs/PERSONA_LOOP_SPEC.md)：人格循环、关系和表达
- [RELATIONAL_TENSION_SPEC.md](docs/RELATIONAL_TENSION_SPEC.md)：AI 不满值/关系张力
- [QQ_PROACTIVE_SPEC.md](docs/QQ_PROACTIVE_SPEC.md)：QQ 主动对话与用户状态
- [WEB_SEARCH_SPEC.md](docs/WEB_SEARCH_SPEC.md)：SearXNG 联网搜索、未知实体兜底和动态内容语义定位

### 桌面端与输入输出

- [GUI_SPEC.md](docs/GUI_SPEC.md)：Electron 窗口、聊天、设置和无障碍
- [VISION_SPEC.md](docs/VISION_SPEC.md)：视觉注意力与敏感窗口
- [STT_SPEC.md](docs/STT_SPEC.md)：SenseVoiceSmall STT
- [IMAGE_MESSAGE_SPEC.md](docs/IMAGE_MESSAGE_SPEC.md)：图片输入和生命周期
- [QQ_STICKER_SPEC.md](docs/QQ_STICKER_SPEC.md)：静态表情库

### 角色、学习和协作

- [CHARPKG_SPEC.md](docs/CHARPKG_SPEC.md)：`.charpkg` 角色包
- [STYLE_LEARNING_SPEC.md](docs/STYLE_LEARNING_SPEC.md)：本地文风学习
- [SHARED_CREATION_SPEC.md](docs/SHARED_CREATION_SPEC.md)：共同创作
- [DISTRIBUTION_SPEC.md](docs/DISTRIBUTION_SPEC.md)：分发与本地资源边界
- [config/character.example.json](config/character.example.json)：角色卡示例
- [characters/yuki/card.md](characters/yuki/card.md)：Yuki 角色卡说明
- [characters/zima/card.md](characters/zima/card.md)：Zima 角色卡说明

## 验证

项目当前使用 `.venv` 验证：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m pytest tests/ -q

unset PYTHONPATH
hermes verify --json --skip-start

"C:/Program Files/nodejs/node.exe" --check pet/main.js
"C:/Program Files/nodejs/node.exe" --check pet/chat-renderer.js
"C:/Program Files/nodejs/node.exe" --check pet/preload.js
"C:/Program Files/nodejs/node.exe" --check pet/settings-renderer.js
```

最近一次验证结果：`740 passed, 1 warning`，Hermes `ok=true`（使用 `--skip-start`，因为通用 FastAPI runtime 探针不适配本项目 Electron/桌宠入口）。分通道结构化 JSON（IM `text`、单语 TTS `text`、双语 TTS `ja`/`zh`）与当前远程 API 的真实 IM/TTS Agent→解析→持久化→渲染环路已验证通过。唯一 warning 来自依赖侧的 Starlette/httpx 弃用提示。Electron 视觉交互、NapCatQQ 真实消息回传、远程服务行为、本地大模型运行和页面正文补充的公网实际内容属于需要人工/环境条件的实机验收，不把静态测试当成实机通过。

## 当前状态

| 范围 | 状态 |
|---|---|
| Agent、角色卡、记忆、状态、PAD、关系模型 | 已实现，行为测试覆盖 |
| TV 不满值与关系张力 T0–T4 | 核心已实现，行为测试覆盖；关系事件确认暂无独立编辑器 |
| QQ 主动策略与 QQ 用户状态 | 五维时机、睡眠/不要打扰、反馈闭环和失败不消耗额度已实现，真实 NapCat 消息链仍需实机验收 |
| QQ/桌宠独立主动间隔 | 已实现；每通道只有一个 `min_gap_minutes` |
| Electron 桌宠、聊天窗口、设置和日志 | 代码与测试已覆盖，真实桌面交互需人工验收 |
| 视觉注意力 | 代码与测试已覆盖，屏幕捕获和视觉主动需人工验收 |
| STT | 本地服务、设置页重启和关闭 sidecar 行为已覆盖，持续 sidecar/真实麦克风需人工验收 |
| Style Learning | MVP 已实现；LoRA、消息级撤回和原文修辞标签暂缓 |
| CHARPKG | 安全导入导出与 CLI 已实现；完整版本 diff/设置页编辑器暂缓 |
| SHARED CREATION | 后端和 CLI 已实现；聊天工作台、时间线和导出 UI 暂缓 |
| R5 dsh | 工单与桥接已实现；外部 dsh 安装和真实执行是可选环境能力 |

明确暂缓或不做：群聊/多会话 QQ、可靠已读状态推断、RLHF/模型微调、复杂博弈/报复/冷暴力、把 TV 直接线性映射到 attachment/trust、独立关系张力数据库、自动发送 STT 结果。
