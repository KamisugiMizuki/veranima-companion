# Veranima Companion

一个给予用户真实感的情感陪伴 agent：拥有较为固定的行为模式、性格、语言方式，具备长期记忆与遗忘机制，并随与用户的逐步交流进行学习式自我修改。

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/IDEA.md](docs/IDEA.md) | 原始构想 |
| [docs/DESIGN.md](docs/DESIGN.md) | 主设计文档（方案唯一权威） |
| [docs/M3_SPEC.md](docs/M3_SPEC.md) | M3 桌宠专项细化（airi 式多窗口/进程模型/TTS/前台） |
| [docs/M4_SPEC.md](docs/M4_SPEC.md) | M4 视觉注意力/表情标签驱动专项细化 |
| [docs/CHARACTER_TEMPLATE.md](docs/CHARACTER_TEMPLATE.md) | 角色卡模板 |
| [docs/STRUCTURE_DESIGN.md](docs/STRUCTURE_DESIGN.md) | 结构设计演进记录 |
| [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md) | 文件管理规范 |

## 技术栈

Python + SQLite(sqlite-vec/FTS5) + 远程 OpenAI 兼容 LLM（DeepSeek/通义/硅基流动等）+ bge-m3 embedding（本地）+ Electron 桌宠壳 + Qwen3-TTS 1.7B（本地语音）

## 快速开始（CLI / QQ）

```bash
# 1. 环境（Python 3.11+）
python -m venv .venv
.venv/Scripts/pip install -e .

# 2. 配置（远程 OpenAI 兼容 API）
cp config/config.example.yaml config/config.yaml
# 编辑 config.yaml：llm.base_url / llm.api_key / llm.model（如 DeepSeek/通义/硅基流动）

# 3. 角色卡
cp config/character.example.json config/character.json
# 编辑 config/character.json 自定义角色；模板见 docs/CHARACTER_TEMPLATE.md

# 4. 运行（CLI 对话）
.venv/Scripts/python -m veranima.cli

# 多角色管理（characters/ 目录，见 DESIGN 4.11）
.venv/Scripts/python -m veranima.cli roles list          # 列出角色
.venv/Scripts/python -m veranima.cli roles switch <id>   # 切换激活角色（重启生效）
```

## QQ 接入（NapCatQQ，OneBot v11）

```bash
# 1. 登录 NapCatQQ（或任意 OneBot v11 实现），配置"反向 WebSocket 客户端"
#    连接 ws://127.0.0.1:8099/ws（与 config.yaml 的 qq.ws_host/ws_port 对应）
# 2. config/config.yaml 的 [qq] 段：
#    enabled: true
#    allowed_qq: [你的QQ号]      # 白名单必填（1v1 私聊），空 = 拒绝所有消息
# 3. 启动
.venv/Scripts/python -m veranima.qq
```

QQ 形态额外启用：定时问候/节庆纪念主动消息、离线思考（静默 30 分钟后低概率"迟来的回应"，可配置）。

## 桌宠（Electron + 本地 TTS）

```bash
# 1. 本地 TTS 模型（clone 后需手动放入 data/models/qwen3-tts/，gitignore 排除）：
#    从 ModelScope/HF 下载：
#      Qwen3-TTS-12Hz-1.7B-CustomVoice/（内置 9 种音色，无需参考音频，~4.5GB）
#      Qwen3-TTS-Tokenizer-12Hz/（分词器）

# 2. 依赖（含 CUDA torch；无 GPU 则 CPU 可用但慢）
.venv/Scripts/pip install -e . torch --index-url https://download.pytorch.org/whl/cu128

# 3. 启动桌宠（壳 spawn 核心 + TTS 服务两个子进程，日志在日志窗口可看）
python scripts/run_pet.py
```

- 桌宠三进程：Electron 壳（UI，主窗口+设置窗+日志窗）+ Python 核心（WS 8765，Agent/时空/在场）+ Qwen3-TTS 服务（9880），崩溃自动重启
- TTS 接口为 **OpenAI 兼容 /v1/audio/speech**（远程/本地统一）：config.yaml `tts.base_url` 指向本地 `http://127.0.0.1:9880/v1`，也可改成任意远程 OpenAI 兼容 TTS API
- 桌宠能力：四态形象 + 表情标签驱动（LLM 输出 portrait → 表情图）、流式打字机、点击穿透拖拽、位置持久化、无缝衔接（回到电脑前自动接续 QQ 话题）、视觉注意力（L0 在场检测 → L3 屏幕理解 → 联想式主动）

## 角色包（.char 导出/导入）

```bash
# 导出角色目录 → .char zip（含安全检查，DESIGN 4.11）
.venv/Scripts/python -m veranima.cli roles export <id>
# 导入 .char（zip bomb/路径穿越防护 + 重名自动改名 + 立绘说明.txt 批量映射表情标签）
.venv/Scripts/python -m veranima.cli roles import <file.char>
```

## 状态

**M0-M4 全部完成**：

| 里程碑 | 内容 |
|---|---|
| M0 | 仓库初始化 + 设计文档 + 可复用模块拷贝（bge-m3 embedding 本地化） |
| M1 | Filter 仿生层：记忆断片（四档确信度+噪声注入）/ 打断决策（话题复现 L0-L3 分级+自愈冷却）/ 表达瑕疵（撤回限频） |
| M2 | 通道适配层：handle(channel) 通道感知 + IM 渲染器 + 跨通道共享核心 + 现实行动边界 |
| M3 | 桌宠 MVP：Electron 壳（airi 式多窗口）+ 本地 TTS（Qwen3-TTS 1.7B）+ 时空沉浸（场景锁/心跳/互斥）+ 角色包/多角色 + 流式输出 + 无缝衔接 |
| M4 | 视觉注意力（锚点/三态/L0-L3 分级）+ 表情标签驱动（portrait/tone 结构化输出 + 立绘说明.txt 批量映射） |

M5（需求翻译层 + 桌面 Agent）暂缓。测试基线 226 passed。
