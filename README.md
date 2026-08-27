# Veranima Companion

Veranima 是一个以 **QQ 私聊为主要收发端** 的人格化 AI 陪伴系统。角色、长期记忆、关系状态、虚拟日程、主动性和任务协作由同一个 Python Agent 管理；Electron 桌宠负责角色形象、TTS、设置、日志和状态展示，也保留次要聊天窗口，但不作为主要通讯通道。

> 当前项目面向 Windows 10/11。仓库不包含 API key、运行数据库、用户图片、模型权重、语音训练素材或本地私有语料。

## 当前能力

| 模块 | 已实现行为 |
|---|---|
| QQ 主通讯 | NapCatQQ OneBot v11 反向 WebSocket；白名单 1v1 私聊；普通对话、主动消息、日程通知和任务状态均以 QQ 为主 |
| 桌宠 | Electron 透明置顶形象、立绘、TTS、拖拽、设置、日志、视觉注意力和次要聊天窗口；不会重复发送 QQ 日程通知 |
| 角色卡 | Character Card V3；内置 `yuki`、`zima`；人格核心、语气、立绘、声音和角色目录日程模板 |
| 长期记忆 | SQLite + FTS5 + sqlite-vec + 本地 bge-m3；五层记忆、版本链、时间线、衰减、整理、审核收件箱和 Context Brief |
| 人格与关系 | PAD、依恋度、PersonaBrief、ResponsePlan、Imprint、冲突跟踪、关系张力和修复过程 |
| 虚拟日程 | 每角色 `virtual_schedule.json`、昼夜节律、结构化次日计划、睡眠/唤醒、grace period、睡眠债务、schedule offset 回正、effective span、日终自传归档、主动分享和用户信息缺口 |
| 虚拟空间 | 每角色有限生活范围、地点池、活动环境、DayRoute、transition 时间占用、CurrentScene、空间事件、离线 reconcile、地点选择策略和 QQ 地点问答；复杂路线全局时间重排与真实 QQ/Electron 双端验收仍未完成 |
| 联网日历 | Nager.Date 公共节假日 JSON API；按年缓存；失败时回退本地工作日/周末；不包含中国官方调休工作日规则 |
| 图片与表情 | QQ/桌宠图片安全校验；QQ 表情候选审核、用户 scope 隔离、TTL、停用/删除、概率和发送间隔；动图不进入静态库 |
| 主动消息 | QQ/pet 使用独立 Gate、冷却和每日额度；角色 sleeping 时统一阻断主动消息；日程状态通知只由 QQ 发送 |
| 联网搜索 | 本地 SearXNG；显式、时效和未知实体搜索；EvidencePack、来源质量、冲突提示、缓存和正文补充 |
| 语音 | SenseVoiceSmall STT；GPT-SoVITS TTS；桌宠语音链与 QQ 文字链共享语义 Agent |
| Style Learning | 本地语料清洗、弱标注、审核、聚合 StyleBrief；原文和产物留在 ignored 目录 |
| 共同创作 | Project/Scene/Decision/Artifact/Thread；证据确认后形成共同经历 |
| 外部任务 | 可选 Hermes Agent 执行后端；陪伴语义仍归 Veranima；默认关闭写代码任务，隔离探针通过后才允许 |

## 架构

```text
QQ / NapCat（主要通讯端，127.0.0.1:8099）
        │
        ▼
QQAdapter ───────────────┐
                          ▼
                    同一个 Agent
                    ├── CharacterCard / Persona
                    ├── MemoryStore / Relationship
                    ├── VirtualSchedule / ProactiveGate
                    ├── Search / Shared Creation
                    └── Hermes task bridge（可选）
                          ▲
Electron 桌宠（次要通讯端）│
├── 形象 / TTS / 状态展示  │
├── 设置 / 日志             │
└── PetServer WS :8765 ─────┘
```

QQ 与桌宠共享人格、状态和记忆，但不共享主动消息的冷却、每日额度和发送结果。日程状态通知只有 QQ adapter 消费；PetServer 只推进日程状态，避免同一句同时发到 QQ 与桌宠。

## 环境要求

- Windows 10/11
- Python `>=3.11`
- `uv`
- Node.js（启动器默认查找 `C:\Program Files\nodejs\node.exe`）
- NapCatQQ（使用 QQ 主通讯时）
- OpenAI 兼容远程聊天 API
- 本地 embedding 模型：`data/models/bge-m3/`
- 桌宠可选：Electron、`ws`、SenseVoiceSmall、GPT-SoVITS runtime/模型

## 安装

以下命令已按项目当前工具链核对，使用 Git Bash：

```bash
cd /d/Hermes_workspace/veranima
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -e .
test -f config/config.yaml || cp config/config.example.yaml config/config.yaml
```

`config/config.yaml` 是本地配置，不进入 Git。至少设置：

```yaml
llm:
  base_url: "https://你的 OpenAI 兼容服务/v1"
  model: "模型名"
  api_key: ""

character_card: "characters/yuki/character.json"

memory:
  embedding_model: "local:data/models/bge-m3"

qq:
  enabled: true
  allowed_qq: [你的QQ号]
```

API key 只写入本地配置或桌宠设置页。角色可切换为 `characters/zima/character.json`。

## QQ 主通讯

1. 启动并登录 NapCatQQ。
2. 在 NapCat 配置反向 WebSocket 客户端：

```text
ws://127.0.0.1:8099/ws
```

3. 启动完整桌宠入口。核心会按 `qq.enabled` 自动挂载 QQ adapter：

```text
双击 run_pet.vbs
```

开发调试：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe scripts/run_pet.py
```

也可只启动 QQ adapter：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.qq
```

白名单为空时拒绝所有私聊。QQ 是正常使用时的主要聊天、主动消息和状态通知出口。

## 桌宠定位

桌宠不是主要通讯端，主要职责是：

- 角色立绘与桌面存在感；
- TTS 语音输出；
- 当前状态和视觉注意力；
- 设置、日志和诊断；
- 次要聊天窗口和语音输入。

桌宠聊天仍可用，但不会消费日程通知；睡前、起床、活动切换等日程消息统一从 QQ 发出，避免重复。

## 虚拟日程

每个角色拥有独立模板：

```text
characters/yuki/virtual_schedule.json
characters/zima/virtual_schedule.json
```

当前实现包括：

- 角色时区、day profile、昼夜节律和跨午夜活动；
- 睡前通知、有限 grace period、强制睡眠和唤醒；
- sleeping 时普通消息不调用 LLM，不自动回复；
- 睡眠消息正文仍按现有消息策略保存，日程归档只保存 message ID 等元数据；
- 醒后最多合并三条未处理消息摘要；
- 结构化 LLM 只能选择角色模板已有 profile/block/activity；非法结果整体回退；
- 完整 LLM adjustments 随 runtime snapshot 持久化；
- 多日 `schedule_offset` 历史、睡眠债务和按角色速率渐进回正；
- 活动中断/恢复及 `effective_span`；
- `DayCloseSummary` 投影为 `truth_class=virtual_simulation` 的独立自传事件；
- 自传事件生成 SelfShareCandidate；用户明确偏好生成带 `source_message_id` 的 UserInfoGap；
- QQ/pet 独立主动 Gate；角色睡眠时所有主动候选被阻断；
- 日程通知仅 QQ 发送。

独立“日程与生活”设置页可配置开关、时区、已有 profile 覆盖、grace、最大延长、自我分享、主动了解和联网日历。

### 联网日历

```yaml
virtual_schedule:
  calendar:
    enabled: true
    base_url: "https://date.nager.at/api/v3/PublicHolidays"
    country_code: "CN"
    timeout_seconds: 8
    cache_ttl_seconds: 86400
```

接口失败、超时或 JSON 非法时只按本地周末/工作日分类，不影响对话或日程生成。

## 设置页

桌宠设置页包含独立页面：

- 角色与桌宠
- 模型连接
- 语音
- 记忆与人格
- 在场与主动
- 日程与生活
- 图片与表情
- 高级

模型连接测试成功后从 API 返回的模型列表下拉选择。API key 读取时只显示掩码。路径类配置使用原生文件/目录浏览器。

## 管理 CLI

当前 `veranima.cli` 是管理命令入口，不是主要聊天入口。实测帮助：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m veranima.cli --help
```

子命令：

```text
roles   多角色管理
task    R5 任务管道
create  共同创作
style   离线文风语料
```

示例：

```bash
.venv/Scripts/python.exe -m veranima.cli roles list
.venv/Scripts/python.exe -m veranima.cli roles switch yuki
.venv/Scripts/python.exe -m veranima.cli roles export yuki yuki.charpkg
.venv/Scripts/python.exe -m veranima.cli roles import yuki.charpkg
```

## 验证

全量自动化：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe -m pytest tests/ -q
```

当前实测：

```text
934 passed, 1 warning
```

Node 语法检查：

```bash
"C:/Program Files/nodejs/node.exe" --check pet/main.js
"C:/Program Files/nodejs/node.exe" --check pet/preload.js
"C:/Program Files/nodejs/node.exe" --check pet/settings-renderer.js
```

真实远程 API 验收：

```bash
unset PYTHONPATH
.venv/Scripts/python.exe scripts/real_schedule_20.py
.venv/Scripts/python.exe scripts/real_schedule_lifecycle.py
```

最近实测结果：

- 连续对话 20/20；重开临时 SQLite 后 20 条 assistant 记录可读；无 fallback、internal reply 或日程协议泄漏；
- 日程 planner 返回 5 个模板内 items；sleeping 回复为空；归档 1 条睡眠消息元数据；恢复 awake；醒后真实 API 回复成功；
- 联网日历 `2026-01-01` 返回 `元旦 / holiday_like / online_calendar`。

## 数据与隐私

以下内容不得提交：

- `config/config.yaml` 和任何密钥；
- `data/`、SQLite 数据库、表情文件和用户图片；
- `logs/`；
- 本地模型、GPT-SoVITS runtime/权重；
- 私有文风语料、审核队列和学习产物；
- `node_modules/`。

图片理解、桌宠聊天图片历史和 QQ 表情复用是三个独立生命周期。睡眠消息归档不复制正文；虚拟生活事件不写入 `shared_episode` 或 `user_fact`。

## 设计与状态文档

- `docs/DESIGN.md`
- `docs/VIRTUAL_SCHEDULE_SPEC.md`
- `docs/VIRTUAL_SPACE_SPEC.md`
- `docs/VIRTUAL_SCHEDULE_COMPLETION_AUDIT.md`
- `docs/VIRTUAL_SCHEDULE_DESIGN_AUDIT_DEEPSEEK_ULTRA.md`
- `docs/VIRTUAL_SCHEDULE_IMPL_AUDIT_DEEPSEEK_ULTRA.md`
- `docs/MEMORY_SPEC.md`
- `docs/PERSONA_LOOP_SPEC.md`
- `docs/QQ_PROACTIVE_SPEC.md`
- `docs/IMAGE_STICKER_LIFECYCLE_SPEC.md`
- `docs/HERMES_AGENT_INTEGRATION_SPEC.md`

README 的“已实现”只表示代码存在且有行为测试；Electron、NapCat、TTS、STT 和外部服务仍需各自的真实运行环境。
