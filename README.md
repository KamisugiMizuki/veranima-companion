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

Python + SQLite(sqlite-vec/FTS5) + Qwen3-8B(Ollama) + bge-m3 embedding + APScheduler

## 状态

方案已收敛，未开工。实现按 MVP1（人格 + 状态机 + 五层记忆 + 本地 LLM 对话 + CLI）开始。
