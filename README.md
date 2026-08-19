# Veranima Companion

AI 陪伴系统：QQ bot + Windows 桌宠双端。固定的性格与行为模式、长期记忆与遗忘、视觉注意力、双语日语配音。

## 功能概览

| 端 | 能力 |
|---|---|
| **QQ bot** | OneBot v11 私聊（NapCatQQ）、定时问候/节庆、离线思考（"迟来的回应"）、贴纸、多角色切换 |
| **桌宠** | 透明置顶立绘、点击/拖拽、QQ 风格独立聊天窗口（记录持久化）、右键菜单、四态形象 + 表情标签驱动、流式打字机、无缝衔接（回到电脑前接续 QQ 话题） |
| **语音** | GPT-SoVITS v4 本地日语合成（微调音色）、整段合成一次出声、双语输出（ja 配音 / zh 显示） |
| **视觉注意力** | 扫视-注视状态机 + 显著度地图（运动/对比/结构）+ 鼠标焦点 + 前台窗口追踪 + 习惯化 + 分层观察冷却（详见 docs/VISION_SPEC.md） |
| **记忆** | SQLite（FTS5 + bge-m3 embedding 本地化）、分层检索、遗忘衰减、事件记忆提取 |

## 架构

```
Electron 壳 (pet/)                    Python 核心 (src/veranima/)
├─ 主窗口（透明置顶立绘）             ├─ pet_server  WS 127.0.0.1:8765
├─ 聊天窗口（QQ 风格，独立窗口）      ├─ agent（人格/记忆/打断/双语）
├─ 设置/日志窗口                      ├─ qq bot  WS 127.0.0.1:8099 (OneBot v11)
└─ spawn ────────────────>           └─ attention 包（视觉注意力循环）
        │
        └─ spawn GPT-SoVITS api_v2.py（127.0.0.1:9880，本地日语 TTS）
```

- 启动器：`run_pet.bat`（项目根，双击）或 `.venv\Scripts\python.exe scripts\run_pet.py`——壳自动 spawn 核心 + TTS；`qq.enabled: true` 时连带拉起 QQ bot（后台无窗口，退出一起停）
- 日志按模块落盘：`logs/core.log`（核心）/ `logs/tts.log`（TTS 原始输出，带时间戳）/ `logs/shell.log`（壳）
- 聊天记录持久化：`%APPDATA%\veranima-pet\chat.json`（右键菜单可清空）

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | 主设计文档（方案唯一权威） |
| [docs/R0_SPEC.md](docs/R0_SPEC.md) | R0 角色内核与统一 Reply（角色卡真值/prompt 分层/解析契约） |
| [docs/R1_SPEC.md](docs/R1_SPEC.md) | R1 共同经历与状态连续性（记忆/状态/跨重启一致） |
| [docs/R2_SPEC.md](docs/R2_SPEC.md) | R2 同一个人的表达（IM/TTS 事实立场一致 + 失败降级） |
| [docs/R3_SPEC.md](docs/R3_SPEC.md) | R3 桌宠作为「在场的人」（Electron/聊天/状态闭环） |
| [docs/R4_SPEC.md](docs/R4_SPEC.md) | R4 有分寸的在场与主动性（Presence/Attention/主动发起） |
| [docs/R5_SPEC.md](docs/R5_SPEC.md) | R5 外部任务协作（dsh 可选能力，不属陪伴核心） |
| [docs/GUI_SPEC.md](docs/GUI_SPEC.md) | **GUI 实现契约**（窗口/组件/状态/动效/无障碍/分批实现） |
| [docs/VISION_SPEC.md](docs/VISION_SPEC.md) | **视觉注意力模块**（仿生模型：三层感知/显著度/扫视-注视/习惯化） |
| [config/character.example.json](config/character.example.json) | 角色卡示例（字段说明见 DESIGN.md 4.8） |

## 快速开始（CLI）

```bash
# 1. 环境（Python 3.11+，推荐 uv）
uv venv .venv
.venv/Scripts/python.exe -m pip install -e .

# 2. 配置（远程 OpenAI 兼容 API）
cp config/config.example.yaml config/config.yaml
# 编辑 config.yaml：llm.base_url / llm.api_key / llm.model
# 角色卡：config.yaml 的 character_card 指向 characters/<name>/character.json

# 3. 运行（CLI 对话入口：多角色管理 / R5 任务管道）
.venv/Scripts/python.exe -m veranima.cli roles list          # 列出角色（当前激活 yuki）
.venv/Scripts/python.exe -m veranima.cli roles switch <id>   # 切换激活角色（重启生效）
.venv/Scripts/python.exe -m veranima.cli task "帮我查一下今天的天气"   # R5 任务管道（需 dsh）
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

- **聊天**：点击形象打开 QQ 风格独立聊天窗口（右侧用户绿泡/左侧桌宠白泡+立绘头像，Enter 发送，记录跨重启保留，右键菜单可清空）
- **右键菜单**：戳一下 / 打开聊天 / 清空聊天记录 / 显示隐藏 / 设置 / 日志 / 重启核心 / 退出
- **TTS**：GPT-SoVITS v4 本地日语合成（端口 9880）。模型权重 + 参考音频在 `characters/yuki/voice/`（gitignore），配置在 config.yaml `[tts]` 段
- **视觉注意力**：自动运行（logs/core.log 可看事件：window_switch / fixation_shift / 观察注入 / 联想主动发起），参数在 config.yaml `[attention]` 段

### 桌宠所需的外部资源（gitignore，clone 后手动准备）

| 资源 | 位置 | 说明 |
|---|---|---|
| GPT-SoVITS 整合包 | `tts/gpt-sovits/`（~12G） | 从 sakura 整合包整体拷贝（含 runtime、pretrained_models v4、api_v2.py） |
| 桌宠壳依赖 | `pet/node_modules/` | electron + ws；可建 junction 复用其他项目（run_pet.py 有缺失检测提示） |

## 角色包（.char 导出/导入）

```bash
.venv/Scripts/python.exe -m veranima.cli roles export <id>       # 导出角色目录 → .char zip
.venv/Scripts/python.exe -m veranima.cli roles import <file.char> # 导入（zip 防护 + 立绘批量映射）
```

## 测试

```bash
.venv/Scripts/python.exe -m pytest tests/ -q    # 251 passed
```

## 状态

**设计基线：v2.1（2026-08-19 重构，人物中心）**。实现按 `docs/DESIGN.md` 的 R0-R5 顺序分批推进：

| 里程碑 | 内容 | 状态 |
|---|---|---|
| R0 | 角色内核与统一 Reply（角色卡真值/prompt 分层/Reply 解析） | 契约已定，待实现 |
| R1 | 共同经历与状态连续性（记忆分类/状态字段/版本链） | 契约已定，待实现 |
| R2 | 同一个人的表达（Reply DTO/IM/TTS 降级/turn_id 取消） | 契约已定，待实现 |
| R3 | 桌宠闭环（状态广播/聊天真实状态/错误恢复/历史分批） | 契约已定，待实现 |
| R4 | 有分寸的在场（Presence/Attention/主动仲裁） | 契约已定，R0-R3 验收后扩展 |
| R5 | 外部任务协作（dsh 可选） | 契约已定，R0-R3 验收后扩展 |

已实现（历史功能，与新契约兼容演进中）：QQ bot 双端、透明置顶桌宠壳、QQ 风格独立聊天窗、GPT-SoVITS v4 日语合成、attention 包 V1（显著度/扫视-注视/鼠标焦点/习惯化/分层冷却，`docs/VISION_SPEC.md`）。

## 已知约束

- 角色语音为日语（GPT-SoVITS 微调音色 yuki）；`bilingual.enabled` 角色缺日语台词时只显示不合成（防中文送日语模型）
- STT 段已就绪（OpenAI 兼容 `/v1/audio/transcriptions`），未接模型（base_url 留空不报错）
- 桌宠核心 WS 为单客户端设计（8765）；TTS 服务常驻（模型加载 ~60s，单句推理实时）
