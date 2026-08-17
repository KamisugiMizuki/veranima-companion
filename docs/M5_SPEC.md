# M5 专项细化：需求翻译层 + 桌面 Agent（docs/M5_SPEC.md）

> 依据：DESIGN.md 4.2（需求翻译层）、M5 里程碑
> **核心决策（2026-08）**：桌面 Agent 独立于 veranima 主体——独立模块、独立配置、独立 API，
> 采用 DeepSeek Harness（dsh）作为执行引擎。

---

## 1. 模块边界（独立原则）

```
┌───────────────────────── veranima 主体 ─────────────────────────┐
│  Agent（闲聊管道：记忆+社交+表达）                                │
│    │ 能力匹配层判定「可转交任务类型清单」命中                     │
│    ▼                                                            │
│  需求翻译层（4.2 细化）：模糊指令 → 结构化工单                   │
│    │ TASK_TRANSFER_PROTOCOL（JSON 文件，唯一接触点）             │
└────┼────────────────────────────────────────────────────────────┘
     ▼
┌────────────────── 桌面 Agent（独立模块） ────────────────────────┐
│  DeepSeek Harness (dsh) CLI headless                             │
│  - 独立配置目录（dsh 自己的 config / profile）                   │
│  - 独立 API（DEEPSEEK_BASE_URL / DEEPSEEK_API_KEY 环境变量）     │
│  - 独立会话 JSONL（任务历史与 veranima 记忆库完全隔离）          │
└──────────────────────────────────────────────────────────────────┘
```

**独立性约定**：
- **目录独立**：桌面 Agent 作为独立模块（`D:\Hermes_workspace\dsh_import` 或 veranima 仓库外独立目录），不混入 veranima 的 `src/`
- **配置独立**：dsh 用自身的 config/headless profile + 独立环境变量；**不读** veranima config.yaml
- **API 独立**：`DEEPSEEK_BASE_URL`/`DEEPSEEK_API_KEY` 独立设置（可与 veranima 的 LLM 提供商不同——veranima 用 gemai.cc 远程，dsh 可用 DeepSeek 官方 key 或本地模型）
- **依赖独立**：dsh 是 npm 包（node_modules 自带），不进 veranima pyproject
- **进程独立**：dsh 任务在独立子进程跑，与桌宠核心/QQ 无共享状态

## 2. 需求翻译层（DESIGN 4.2 细化）

**职责**：模糊指令 → 结构化任务工单 → 桌面 Agent Prompt。

### 2.1 意图补完五维（每个维度缺失时 LLM 补全或追问）

| 维度 | 说明 | 示例 |
| --- | --- | --- |
| 目标澄清 | 要达成的结果（可验证） | 「把这份文档转成 PPT」→ 输出 .pptx，10 页以内 |
| 来源路径 | 输入文件/数据位置 | 「桌面那个 excel」→ 解析为绝对路径 |
| 用户偏好注入 | 格式/风格/约束 | 中文、16:9、不用深色主题 |
| 优先级约束 | 时间/资源限制 | 5 分钟内完成 / 不能动 D 盘 |
| 异常预案 | 失败/歧义处理 | 找不到文件→返回错误码+建议 |

### 2.2 工单协议 TASK_TRANSFER_PROTOCOL（JSON）

```json
{
  "version": 1,
  "task_id": "2026-08-18-001",
  "goal": "把 D:/docs/周报.xlsx 转成 PPT 并导出 PDF",
  "context": "用户说'帮我做下周报'；来源=桌面；偏好=中文/简洁",
  "constraints": {"deadline": "10min", "format": ["pptx", "pdf"]},
  "fallback": "找不到文件时返回错误说明，不要编造"
}
```

- 发送给 dsh 的任务 = `goal` + `constraints` 拼成自然语言 prompt
- veranima 侧必须给用户「已安排」反馈（角色口吻，人格化转译器负责）
- 工单不落 veranima 记忆库（任务状态与陪伴记忆分离）——完成结果可选回写（用户确认后）

### 2.3 触发条件（能力匹配层判定）

- 可转交任务类型清单（可配置，config.yaml `task_types`）：文档处理 / 信息检索 / 系统操作 / 自动化流程
- 命中 → 需求翻译层；不命中 → 闲聊管道
- 用户可用显式开关：「帮我做个事」/「交给桌面助手」——避免误转交

## 3. 桌面 Agent 执行引擎（DeepSeek Harness）

### 3.1 集成方式（Windows 实测验证，见 deepseek-harness 技能）

- **CLI headless 一次性**：`dsh --profile headless "<任务>"` → 跑完打印最终答复退出，会话 JSONL 落盘可续
- npm 包：`@deepseek-ai/dsh@0.1.0-rc.6`（**锁版本**，官方声明破坏性变更）
- Python SDK 不可用（Windows 无 runtime-bin wheel）→ 不用 SDK
- 前置：Node ^22.19 || >=24（本机 22.23.2 ✅）

### 3.2 API 配置（完全独立于 veranima）

```bash
# dsh 专用环境变量（与 veranima config.yaml 的 llm 段无关）
DEEPSEEK_BASE_URL=http://127.0.0.1:1234/v1    # 示例：LM Studio 本地
DEEPSEEK_API_KEY=sk-xxx                       # 本地模型可任意值；DeepSeek 官方 key 用官方端点
```

- **前提**：模型 tool-calling 可靠（dsh 依赖工具调用执行任务）
- 选项 A：LM Studio 本地模型（不烧远程 token，与游戏共存时 unload）
- 选项 B：DeepSeek 官方 API（key 独立购买，不走 veranima 的 gemai.cc）

### 3.3 调用封装（veranima 侧薄壳）

```python
# veranima/tools/dsh_bridge.py（仅此一个文件接触 dsh）
def run_dsh_task(workorder: dict) -> dict:
    """工单 → dsh headless 子进程 → 结果。超时/失败返回错误码。"""
    cmd = ["dsh", "--profile", "headless", workorder["prompt"]]
    # subprocess.run，timeout=workorder 的 deadline，stdout 捕获
    return {"task_id": workorder["task_id"], "output": out, "exit_code": rc}
```

- 薄壳原则：不封装 dsh 全部功能，只暴露「工单 → 结果」
- 超时（默认 10min）、进程失败 → 返回错误码，veranima 角色化转述（「那事我让助手去办了，它说卡住了，我再看看」）

## 4. M5 验收清单

| 项 | 验收 |
| --- | --- |
| 2.1 意图补全 | 模糊指令 → 五维工单（缺失维度 LLM 补全或追问） |
| 2.2 工单协议 | TASK_TRANSFER_PROTOCOL JSON 生成正确；发送后必给「已安排」反馈 |
| 2.3 触发 | 任务类型清单命中 → 转交；闲聊 → 不转交；显式开关可用 |
| 3.1 dsh 集成 | headless CLI 真实跑通一次任务（含 tool 调用） |
| 3.2 API 独立 | dsh 用自己的 base_url/key；veranima config.yaml 改动不影响 dsh |
| 3.3 薄壳 | 工单 → 结果薄壳工作；超时/失败返回错误码并角色化转述 |

## 5. 与 DESIGN.md 的关系

M5 完成后核心结论回写 DESIGN.md（4.2 扩展 + M5 里程碑行更新）。

## 6. 暂缓内容（YAGNI）

- dsh Web UI / ACP / API Gateway 接入（Hermes 不用）
- 任务结果自动回写 veranima 记忆（等用户实际需要「记住任务结果」再设计）
- 多任务队列/并发（MVP 单任务串行）
