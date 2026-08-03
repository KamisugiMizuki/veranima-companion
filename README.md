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

# 2. 配置（二选一）
#   方式 A：远程 API（无需本地模型环境）
cp config/config.example.yaml config/config.yaml
# 编辑 config.yaml：llm.base_url / llm.api_key / llm.model（如 DeepSeek/通义）
# 并把 memory.embedding_model 改为 openai:<你的embedding模型>

#   方式 B：本地 LM Studio
#   下载 Qwen3-8B GGUF（ModelScope Qwen/Qwen3-8B-GGUF），放入 LM Studio 模型库
#   bash scripts/run_lmstudio.sh

# 3. 运行
.venv/Scripts/python -m veranima.cli
```

## 状态

MVP1 完成（人格 + 状态机 + 五层记忆 + 本地/远程 LLM 对话 + CLI）。
