# Veranima Companion

固定人格、拥有共同经历、会随当下状态变化的 AI 陪伴系统：QQ bot + Windows 桌宠双端，同一 Agent 的两种媒介。产品原则：**不追求更像 AI，追求更像「某个人」**。

## 功能概览

| 端 | 能力 |
|---|---|
| **QQ bot** | OneBot v11 私聊（NapCatQQ 反向 WS）、定时问候/节庆、离线思考（"迟来的回应"）、贴纸、多角色切换 |
| **桌宠** | 透明置顶立绘（立绘底边锚点布局，气泡增长窗口向上扩）、点击/拖拽、QQ 风格独立聊天窗口（记录持久化）、右键菜单、表情标签驱动、双语输出（ja 配音 / zh 显示） |
| **语音** | GPT-SoVITS v4 本地日语合成（微调音色，整段合成一次出声） |
| **记忆** | SQLite + FTS5 + sqlite-vec + 本地 bge-m3；版本链（修正不覆盖）、Context Brief 预算注入、确定性整理器、历史压缩（完整契约见 `docs/MEMORY_SPEC.md`） |
| **文风学习** | 从用户自然消息提取稳定文风画像（句长/问句/正式度/直接度，EMA 慢速演化），融合时保持角色人格不被覆盖 |
| **视觉注意力** | 扫视-注视状态机 + 显著度地图 + 鼠标焦点 + 前台窗口追踪 + 敏感窗口策略（详见 `docs/VISION_SPEC.md`） |
| **任务协作** | R5 可选能力：模糊指令 → 工单 → dsh CLI 子进程（取消/超时/输出截断） |

## 架构

```
Electron 壳 (pet/)                    Python 核心 (src/veranima/)
├─ 主窗口（透明置顶立绘）             ├─ pet_server  WS 127.0.0.1:8765
├─ 聊天窗口（QQ 风格，独立窗口）      ├─ agent（人格/记忆/打断/双语/文风学习）
├─ 设置/日志窗口                      ├─ qq bot  WS 127.0.0.1:8099 (OneBot v11)
└─ spawn ────────────────>           └─ attention 包（视觉注意力循环）
        │
        └─ spawn GPT-SoVITS api_v2.py（127.0.0.1:9880，本地日语 TTS）
```

- **启动器**：双击 `run_pet.bat`，或 `.venv\Scripts\python.exe scripts\run_pet.py`——壳自动 spawn 核心 + TTS；`config.yaml` 的 `qq.enabled: true` 时连带拉起 QQ bot（后台无窗口，退出一起停）
- 日志按模块落盘：`logs/core.log`（核心）/ `logs/tts.log`（TTS）/ `logs/shell.log`（壳）
- 聊天记录持久化：`%APPDATA%\veranima-pet\chat.json`（右键菜单可清空）

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | 主设计文档（v2.2，方案唯一权威） |
| [docs/PERSONA_LOOP_SPEC.md](docs/PERSONA_LOOP_SPEC.md) | **人格循环专项契约**（角色核心/用户框架/自我与关系模型/共同意义/反思/回用与防回声室） |
| [docs/MEMORY_SPEC.md](docs/MEMORY_SPEC.md) | **记忆专项契约**（五层类型/候选校验/版本链/混合召回/Context Brief/整理器/文风学习/用户控制/评测） |
| [docs/R0_SPEC.md](docs/R0_SPEC.md) | R0 角色内核与统一 Reply（角色卡真值/prompt 分层/解析契约） |
| [docs/R1_SPEC.md](docs/R1_SPEC.md) | R1 共同经历与状态连续性（记忆/状态/跨重启一致） |
| [docs/R2_SPEC.md](docs/R2_SPEC.md) | R2 同一个人的表达（IM/TTS 事实立场一致 + 失败降级） |
| [docs/R3_SPEC.md](docs/R3_SPEC.md) | R3 桌宠作为「在场的人」（Electron/聊天/状态闭环） |
| [docs/R4_SPEC.md](docs/R4_SPEC.md) | R4 有分寸的在场与主动性（Presence/Attention/9 闸门主动仲裁） |
| [docs/R5_SPEC.md](docs/R5_SPEC.md) | R5 外部任务协作（dsh 可选能力） |
| [docs/GUI_SPEC.md](docs/GUI_SPEC.md) | GUI 实现契约（窗口/组件/状态/动效/无障碍/分批实现） |
| [docs/VISION_SPEC.md](docs/VISION_SPEC.md) | 视觉注意力模块（三层感知/显著度/扫视-注视/敏感窗口） |
| [config/character.example.json](config/character.example.json) | 角色卡示例（字段说明见 DESIGN.md） |

## 快速开始（CLI）

```bash
# 1. 环境（Python 3.11+，推荐 uv）
uv venv .venv
.venv/Scripts/python.exe -m pip install -e .

# 2. 配置（远程 OpenAI 兼容 API）
cp config/config.example.yaml config/config.yaml
# 编辑 config.yaml：llm.base_url / llm.api_key / llm.model
# 角色卡：character_card 指向 characters/<name>/character.json（默认 yuki）

# 3. 多角色管理
.venv/Scripts/python.exe -m veranima.cli roles list           # 列出角色
.venv/Scripts/python.exe -m veranima.cli roles switch <id>    # 切换激活角色
.venv/Scripts/python.exe -m veranima.cli roles export <id>    # 导出 .char 包
.venv/Scripts/python.exe -m veranima.cli roles import <file.char>  # 导入（zip 防护 + 立绘映射）

# 4. CLI 对话（无参数进入交互模式）
.venv/Scripts/python.exe -m veranima.cli
# 对话中斜杠命令：/memory [/export /forget /style /status /reset --style /quit]

# 5. R5 任务管道（模糊指令 → 工单 → dsh，需 dsh CLI）
.venv/Scripts/python.exe -m veranima.cli task "帮我查一下今天的天气"
```

## QQ 接入（NapCatQQ，OneBot v11）

```bash
# 1. 登录 NapCatQQ，配置「反向 WebSocket 客户端」→ ws://127.0.0.1:8099/ws
# 2. config.yaml 的 [qq] 段：enabled: true、allowed_qq: [你的QQ号]（白名单必填）
# 3. 启动
.venv/Scripts/python.exe -m veranima.qq
```

QQ 形态额外启用：定时问候/节庆、离线思考（静默 30 分钟后低概率"迟来的回应"）。

## 桌宠（Electron + GPT-SoVITS）

```bash
# 启动（双击 run_pet.bat，或：）
.venv/Scripts/python.exe scripts\run_pet.py
```

- **聊天**：点击形象打开 QQ 风格独立聊天窗口（右侧用户绿泡/左侧桌宠白泡+立绘头像，Enter 发送，IME 选词不误发，记录跨重启保留）
- **右键菜单**：戳一下 / 打开聊天 / 清空聊天记录 / 显示隐藏 / 设置 / 日志 / 重启核心 / 退出
- **TTS**：GPT-SoVITS v4 本地日语合成（端口 9880）。模型权重 + 参考音频在 `characters/yuki/voice/`（gitignore），配置在 config.yaml `[tts]` 段
- **视觉注意力**：自动运行（logs/core.log 可看事件：window_switch / fixation_shift / 观察注入 / 联想主动发起），参数在 config.yaml `[attention]` 段

### 桌宠所需的外部资源（gitignore，clone 后手动准备）

| 资源 | 位置 | 说明 |
|---|---|---|
| GPT-SoVITS 整合包 | `tts/gpt-sovits/`（~12G） | 从 sakura 整合包整体拷贝（含 runtime、pretrained_models v4、api_v2.py） |
| 桌宠壳依赖 | `pet/node_modules/` | electron + ws；可建 junction 复用其他项目（run_pet.py 有缺失检测提示） |

## 测试

```bash
.venv/Scripts/python.exe -m pytest tests/ -q    # 382 passed
```

行为级验收测试覆盖：Reply 解析（纯文本/JSON/fence/残缺/非法标签）、记忆契约（版本链/session TTL/混合召回/整理器/文风画像/离线评测集 10 例）、R4 主动闸门 9 条、R5 工单生命周期、视觉分层与敏感窗口、模块联通（Context Brief 注入/LLM 候选消费/主动反馈链路）。

## 状态

**设计基线 v2.2（2026-08-19）**。R0-R5、MEMORY_SPEC M1-M8、VISION、GUI 已实现；人格循环已完成专项设计，待按 P-0~P-9 实施：

| 里程碑 | 内容 | 状态 |
|---|---|---|
| R0 | 角色内核与统一 Reply（角色卡真值/prompt 分层/确定性解析/五阶段 handle） | ✅ 实现 |
| R1 | 共同经历与状态连续性（记忆分类/状态字段/版本链） | ✅ 实现 |
| R2 | 同一个人的表达（Reply DTO/IM/TTS 降级/turn_id 取消） | ✅ 实现 |
| R3 | 桌宠闭环（R3 协议/状态机/聊天窗真实状态/错误恢复/历史分批） | ✅ 实现 |
| R4 | 有分寸的在场（场景锁/通道互斥/9 闸门/忽略自愈/visual 只产候选） | ✅ 实现 |
| R5 | 外部任务协作（workorder/dsh bridge 取消/超时/截断） | ✅ 实现 |
| MEMORY_SPEC | M1 版本真值 / M2 写入契约 / M3 混合召回 / M4 Context Brief / M5 整理器 / M6 文风学习 / M7 用户控制 / M8 离线评测 | ✅ 实现 |
| PERSONA LOOP | P0 角色核心 / P1 用户框架 / P2 共同意义 / P3 多维关系 / P4 Persona Brief / P5 反思 / P6 独立回用 / P7 冲突修复 / P8 评测 / P9 PAD+ResponsePlan+表层印记 | ✅ 第一版闭环；章节编辑器暂缓 |
| VISION | 三层感知（L1/L2 禁 LLM、L3 只产 Observation）/敏感窗口/观察预算 | ✅ 实现 |
| GUI | 状态广播/历史分批/历史搜索/人物档案章节/清空确认/立绘锚点布局/交叉淡入/IME/a11y | ✅ 实现 |

## 已知约束

- 角色语音为日语（GPT-SoVITS 微调音色 yuki）；`bilingual.enabled` 角色缺日语台词时只显示不合成（防中文送日语模型）
- 本地 embedding 为 bge-m3（sentence-transformers）；远程 API 不支持 embeddings 时必需
- 桌宠核心 WS 为单客户端设计（8765）；TTS 服务常驻（模型加载 ~60s，单句推理实时）
- 视觉观察有日预算（默认 120 次）与敏感窗口策略，不截取/不发送敏感内容
