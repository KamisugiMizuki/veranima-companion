# Veranima Companion — 文件管理规范

本规范约束仓库内所有文件/目录的归属与命名，开工后新增内容一律按此执行。

---

## 1. 顶层目录结构

```
veranima-companion/
├── README.md                 # 项目简介 + 文档索引 + 快速开始
├── pyproject.toml            # Python 依赖与打包声明
├── .gitignore                # 忽略规则（见第 4 节）
├── docs/                     # 设计文档与规范（只读归档，不写代码）
│   ├── IDEA.md               # 原始构想
│   ├── STRUCTURE_DESIGN.md   # 结构设计演进记录（含原始技术栈讨论）
│   ├── DESIGN.md             # 主设计文档（已确认方案的唯一权威）
│   ├── CHARACTER_TEMPLATE.md # 角色卡模板
│   └── PROJECT_STRUCTURE.md  # 本规范
├── src/
│   └── veranima/             # 主程序 Python 包（唯一代码入口）
│       ├── core/             # 内核：人格/状态/关系/学习演化
│       ├── memory/           # 记忆系统：五层存储/检索/遗忘/整理
│       ├── llm/              # LLM 接入：Ollama 客户端/prompt 组装/上下文预算
│       ├── adapters/         # 适配层：CLI / OneBot(NapCatQQ) 等
│       └── ...               # 按模块继续划分，见第 3 节
├── components/               # 独立子组件（可独立测试/复用，见第 2 节）
│   └── <component>/          # 每个组件自包含：README + 自己的依赖声明
├── tests/                    # 测试（镜像 src 结构）
├── scripts/                  # 开发辅助脚本（初始化/备份/迁移/模型下载）
├── config/                   # 默认配置模板（入库，如 config.example.yaml）
└── data/                     # 运行时数据（gitignore，见第 4 节）
```

## 2. 目录职责与规则

| 目录 | 职责 | 规则 |
|---|---|---|
| `docs/` | 设计、规范、模板文档 | 只存 Markdown；主文档大写命名（`DESIGN.md` 等）；修改必须同步 commit+push |
| `src/veranima/` | 主程序源代码 | Python 包，模块划分与 DESIGN.md 章节对应；**内核代码禁止依赖任何 adapter**（适配层可以依赖内核，反向不行） |
| `components/` | 独立子组件 | 每个组件一个子目录，自带 README 与依赖声明；可独立运行/测试；与主程序通过明确接口通信 |
| `tests/` | 自动化测试 | 镜像 `src/veranima/` 结构；`tests/test_<module>.py` 命名 |
| `scripts/` | 辅助脚本 | 一次性/低频任务；脚本头注明用途与运行方式 |
| `config/` | 配置模板 | 只放模板（`*.example.*`），实际配置生成到 `data/` 或本地，不入库 |
| `data/` | 运行时数据 | SQLite 库、向量索引、日志、导出、模型缓存；**全部 gitignore，由程序自动创建** |

## 3. 模块划分原则（src/veranima/）

- 按 DESIGN.md 的功能章节对应建包：`memory/`、`core/`（人格+状态+学习）、`llm/`、`adapters/`
- **分层依赖单向**：`adapters → llm/core/memory`，`core → memory/llm`，禁止反向依赖
- 记忆系统对外只暴露五个原语（store / recall / decay / curate / erase），内部实现可自由更换（详见 DESIGN.md 记忆章节）

## 4. .gitignore 与提交规则

忽略项（已写入 `.gitignore`）：

- Python 产物：`__pycache__/`、`.venv/`、`*.egg-info/`、build/dist
- 运行时数据：`data/`（整个目录）、`*.db`、`logs/`
- 本地配置：`config.yaml`（模板 `config.example.yaml` 正常入库）
- 大文件/模型权重：`*.gguf`、`*.safetensors`
- 编辑器与系统文件：`.idea/`、`.vscode/`、`.DS_Store`
- Hermes 本地状态：`.hermes/`

提交规则（全局约定）：

- 每次修改后自动 `git add -A && git commit && git push`，不积压
- 提交信息格式：`<type>: <简述>`（`docs:` / `feat:` / `fix:` / `refactor:` / `test:`）
- 大文件（>50MB）、密钥、本地数据一律不提交；误提交立即从仓库清除

## 5. 命名约定

| 对象 | 规则 | 示例 |
|---|---|---|
| Python 包/模块 | snake_case | `memory_store.py` |
| 类 | PascalCase | `MemoryStore` |
| 主设计文档 | 全大写 | `DESIGN.md` |
| 其他文档 | 大写或 kebab-case | `PROJECT_STRUCTURE.md` |
| 子组件目录 | kebab-case | `napcat-adapter/` |
| 数据库/数据文件 | snake_case | `veranima.db` |
