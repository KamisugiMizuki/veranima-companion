# Veranima Companion

一个给予用户真实感的情感陪伴 agent：拥有较为固定的行为模式、性格、语言方式，具备长期记忆与遗忘机制，并随与用户的逐步交流进行学习式自我修改。

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/IDEA.md](docs/IDEA.md) | 原始构想 |
| [docs/DESIGN.md](docs/DESIGN.md) | 主设计文档（方案唯一权威） |
| [docs/CHARACTER_TEMPLATE.md](docs/CHARACTER_TEMPLATE.md) | 角色卡模板 |
| [docs/STRUCTURE_DESIGN.md](docs/STRUCTURE_DESIGN.md) | 结构设计演进记录 |
| [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md) | 文件管理规范 |

## 技术栈

Python + SQLite(sqlite-vec/FTS5) + Qwen3-8B + bge-m3 embedding

## 快速开始

```bash
# 1. 环境（Python 3.11+）
python -m venv .venv
.venv/Scripts/pip install -e .

# 2. 配置（远程 OpenAI 兼容 API）
cp config/config.example.yaml config/config.yaml
# 编辑 config.yaml：llm.base_url / llm.api_key / llm.model（如 DeepSeek/通义/硅基流动）
# 并把 memory.embedding_model 改为 openai:<你的embedding模型>

# 3. 角色卡（默认使用内置卡，可选自定义）
cp config/character.example.json config/character.json
# 编辑 config/character.json 自定义角色形象；模板与字段说明见 docs/CHARACTER_TEMPLATE.md

# 4. 运行
.venv/Scripts/python -m veranima.cli
```

## QQ 接入（NapCatQQ，OneBot v11）

```bash
# 1. 登录 NapCatQQ（或任意 OneBot v11 实现），配置"反向 WebSocket 客户端"
#    连接 ws://127.0.0.1:8099/ws（与 config.yaml 的 qq.ws_host/ws_port 对应；
#    默认 8099 是因为本机 8080 被 SearXNG 占用）
# 2. config/config.yaml 的 [qq] 段：
#    enabled: true
#    allowed_qq: [你的QQ号]      # 白名单必填（1v1 私聊），空 = 拒绝所有消息
# 3. 启动
.venv/Scripts/python -m veranima.qq
```

QQ 形态额外启用：定时问候/节庆纪念主动消息、8.7.4 离线思考（静默 30 分钟后低概率"迟来的回应"，可配置）。

## 状态

MVP1~MVP3 完成（人格 + 状态机 + 五层记忆 + 本地/远程 LLM 对话 + CLI + 联网搜索 + NapCatQQ 接入）。8.6 表情包/图像能力未实现（见 DESIGN.md）。
