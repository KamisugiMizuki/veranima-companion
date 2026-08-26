# Hermes Agent 集成修改说明

> 文档状态：架构修改方案，尚未实施。
> 适用基线：veranima `main`，设计总纲 v2.2，R5 当前使用 `tools/dsh_bridge.py`。
> 目标：让 Hermes Agent 承担通用 Agent 运行时与工作执行能力，veranima 继续承担人物、关系、陪伴和媒介表达。
> 非目标：把 veranima 改造成 Hermes 角色卡、把普通陪伴对话全部转给 Hermes、用 Hermes 内置记忆替换人物记忆。
> 核验范围：已依据本机 `hermes --help`、`hermes gateway --help`、Hermes 官方 Programmatic Integration / API Server 文档及本机安装源码核对接口名称；尚未配置 `veranima-worker` profile，也未对该集成运行实机任务。

## 1. 修改动机

veranima 已经实现了稳定人格、关系状态、事件记忆、主动发起、QQ/桌宠双端、TTS、立绘和视觉注意力。这些能力直接构成产品身份，必须由 veranima 自己控制。

与此同时，通用 Agent 能力存在重复建设：模型与 provider 管理、工具调用、文件和终端操作、联网搜索、后台任务、执行状态、取消、审批、代码工作树和多 Agent 协作。Hermes Agent 已经提供这些能力，并提供 ACP、TUI Gateway JSON-RPC 和 OpenAI-compatible HTTP API 三种外部程序接入协议。[1][2]

| 接入协议 | 传输 | 适合场景 | 本方案结论 |
|---|---|---|---|
| OpenAI-compatible API | HTTP + SSE | 非 Python 客户端、普通前端、一次性对话 | 保留为普通兼容接口，不作为首期工作执行入口 |
| `/v1/runs` | HTTP + SSE | 后台执行、状态轮询、审批、停止、运行事件 | **首期采用** |
| ACP | stdio JSON-RPC | IDE、Diff/ToolCall 原生展示、权限请求 | 后续 IDE 深度集成时再评估 |
| TUI Gateway JSON-RPC | stdio / WebSocket | 自定义宿主、会话切换、slash 命令、细粒度交互 | 功能最全但耦合最高，首期不采用 |

核心判断：

```text
veranima = 人物与陪伴语义所有者
Hermes Agent = 通用工作能力与执行生命周期所有者
```

这不是框架迁移，而是执行后端替换。首期改动只落在 R5 外部任务协作链，不触碰普通聊天主链。

## 2. 当前实现真值

### 2.1 当前 R5 生产链

```text
CLI `veranima task ...`
-> cli.py::_task_cmd()
-> is_task_request()
-> build_workorder_llm() / build_workorder()
-> clarification_question()
-> run_dsh_task()
-> dsh headless CLI 子进程
-> 结构化 result
```

当前事实：

- `WorkOrder`、任务分类、来源路径提取、危险动作检测和澄清问题已经存在于 `core/workorder.py`。
- `tools/dsh_bridge.py` 是 veranima 唯一接触 DSH 的文件。
- 生产代码中只有 CLI `_task_cmd()` 调用 DSH bridge。
- QQ 与桌宠普通对话尚未接入任务执行链。
- `Agent.task_result_story()` 已能把结构化任务结果转成角色口吻，但当前没有完整产品链消费它。
- R5 当前状态由 bridge 返回值临时表达，没有持久化任务记录、审批请求或重启恢复。

因此，首期不需要重写 WorkOrder，也不需要新增任务编排框架。最小改法是保持上游协议，替换执行 bridge。

### 2.2 当前人物主链

以下能力必须继续由 veranima 持有：

- `Agent.handle()` 和统一 `Reply` 解析边界；
- Character Card V3、PersonaBrief、ResponsePlan；
- PAD、关系模型、关系张力、依恋度和状态持久化；
- `MemoryStore` 五层人物记忆、来源消息、版本链、事件生命周期；
- QQ 主动 Gate、场景锁、事件锚点、有效期和忽略反馈；
- NapCat OneBot v11 私聊链路；
- Electron 桌宠、聊天窗、立绘、TTS/STT、视觉注意力；
- IM/TTS 通道渲染、双语 segments、协议泄漏拦截。

Hermes 的 `SOUL.md`、内置 `MEMORY.md`/`USER.md` 和 Bot profile 不能等价替换这些模块。Hermes 官方内置记忆是有字符上限、在 session 启动时注入的 `MEMORY.md`/`USER.md` 快照；`SOUL.md` 是系统 prompt 的持久人格基础。[5][6] 它们适合保存精炼的用户偏好、工程环境和基线语气，不提供 veranima 所需的事件版本链、关系状态、来源消息 ID 和主动回用生命周期。

## 3. 目标架构

```text
QQ / Electron 桌宠 / CLI
          |
          v
veranima 输入与人物决策
- 角色卡 / PersonaBrief / PAD
- 关系 / 事件记忆 / 主动 Gate
- IM / TTS / 立绘渲染
          |
          +-------------------------------+
          |                               |
          v                               v
普通陪伴对话                         明确工作请求
当前低延迟 LLM 主链                 WorkOrder + 用户确认
                                          |
                                          v
                                  HermesExecutionBridge
                                          |
                                          v
                                Hermes Agent `/v1/runs`
                                - tools / skills / MCP
                                - approvals / stop / steer
                                - sessions / event stream
                                - tests / delegation
                                          |
                                          v
                                  结构化 TaskResult
                                          |
                                          v
                                Agent.task_result_story()
                                          |
                                          v
                                    QQ / 桌宠展示
```

### 3.1 所有权边界

| 数据或行为 | 真值所有者 | Hermes 可见范围 |
|---|---|---|
| 角色核心、关系、PAD、主动性 | veranima | 默认不可见；仅按任务需要给最小表达上下文 |
| 原始聊天历史 | veranima SQLite | 不整体复制；只发送确认后的任务上下文 |
| 用户工程偏好 | 独立 Hermes profile | 可以由 Hermes profile memory/AGENTS.md 持久化 |
| WorkOrder | veranima | 作为单次 run 输入 |
| 工具执行过程 | Hermes | 通过 run 事件映射给 veranima |
| 代码变更和测试 | 阶段 3 验证后的 Hermes worktree 会话 | 返回 diff、commit、测试结果和风险摘要；验证前不开放代码写入 |
| 最终聊天表述 | veranima | Hermes 只返回任务结果，不直接扮演角色发言 |
| 人物长期记忆 | veranima | 任务结果默认不自动写入；用户确认后按现有候选协议写入 |

### 3.2 双会话原则

陪伴会话与工作会话必须分离：

- 陪伴会话由 veranima 保存，追求人物连续性和低延迟。
- 工作会话由独立 Hermes profile 保存，追求工具连续性、工程上下文和可恢复执行。
- 两者只通过 `task_id`、run ID、状态和结果摘要关联。
- 不把 Hermes 工具日志写入人物聊天历史。
- 不把 veranima 的关系状态、私密记忆库或完整聊天记录复制进 Hermes profile。

## 4. 首期修改范围

首期只完成一条垂直链：

```text
CLI 明确任务
-> WorkOrder 校验与确认
-> Hermes `/v1/runs`
-> 状态轮询 / 停止 / 审批
-> 结构化结果
-> 角色化转述
```

### 4.1 保留并复用

- `core/workorder.py`
  - 保留 `WorkOrder` 数据类。
  - 保留 `is_task_request()`，LLM 不决定是否获得工具权限。
  - 保留 `validate_workorder()`、来源路径检查和危险操作确认。
  - 保留 `clarification_question()`。
- `Agent.task_result_story()`
  - 继续作为任务结果进入聊天前的角色化边界。
- `tasks` 配置段
  - 复用 `enabled`、`require_confirmation`、`timeout_seconds`、`output_max_chars`、`allowed_types`。
- 现有线程/异步边界
  - Hermes HTTP 等待不得阻塞 QQ/WebSocket 事件循环。

### 4.2 新增最小 bridge

建议新增：

```text
src/veranima/tools/hermes_bridge.py
```

bridge 只负责协议转换，不复制 Hermes Agent 逻辑。建议最小 API：

```python
class HermesExecutionBridge:
    def health(self) -> bool: ...
    def submit(self, workorder: WorkOrder) -> TaskRun: ...
    def status(self, run_id: str) -> TaskRun: ...
    def stop(self, run_id: str) -> TaskRun: ...
    def approve(self, run_id: str, choice: str) -> TaskRun: ...
```

不为一个 HTTP 实现提前建立 interface/factory。若后续确实需要 DSH fallback，再从实际重复中提取协议。

### 4.3 结构化结果

bridge 对 veranima 返回固定结构：

```python
@dataclass
class TaskRun:
    task_id: str
    run_id: str
    status: Literal[
        "queued", "running", "waiting_for_approval", "succeeded",
        "failed", "cancelled", "timed_out", "orphaned"
    ]
    raw_status: str = ""
    output: str = ""
    error: str = ""
    changed_files: tuple[str, ...] = ()
    test_summary: str = ""
    approval_request: dict | None = None
    started_at: str = ""
    finished_at: str = ""
```

只保留 veranima 真正消费的字段。Hermes 原生终态是 `completed/failed/cancelled`；bridge 可以把 `completed` 归一化为 veranima 的 `succeeded`，但必须把原值写入 `raw_status`。`changed_files` 和 `test_summary` 不是 `/v1/runs` 原生保证字段，只能来自受控任务输出契约，或在已验证的 worktree 上由 veranima 确定性读回；无法验证时保持空值，不能让模型编造。Hermes 原始事件和完整工具输出写入工作日志，不塞入聊天历史或人物记忆。

当前 Hermes `/v1/runs` 提供 run ID、状态轮询、SSE 事件、stop 和 approval；官方文档同时说明终态只短期保留，run transport 不是跨 gateway 重启的持久任务队列。[3] `task_runs` 能支持“veranima 重启、Hermes gateway 仍存活”后的重新关联；若 Hermes 重启或 run 状态过期，GET 返回 404 时必须把非终态本地记录标为 `orphaned`，不能猜测成功、失败或已取消。后续只能通过关联 session、隔离目录/worktree 和目标产物读回真实状态，再让用户选择重跑或人工收尾。

### 4.4 Hermes profile

创建独立 profile，例如 `veranima-worker`：

- 独立 `HERMES_HOME`、sessions、memory、skills 和 approvals。
- worker profile 的 `terminal.cwd` 固定为经安装流程验证的工作区根；首期自修改目标是 veranima 项目根。WorkOrder 可引用绝对来源路径，但 `/v1/runs` 没有公开 per-run cwd 字段，不能宣称每个任务动态换根目录。
- 只启用任务所需 toolsets；首期建议 `terminal,file,web,search,skills`，不要默认加载 messaging、cron、computer-use。
- 工程约束写入仓库 `AGENTS.md`，人物设定不写入 worker 的 `SOUL.md`。
- profile 不与当前开发会话共享 memory writer。

Secrets 只保存在 Hermes profile 的 `.env`；veranima `config.yaml` 仅保存非敏感连接参数和 profile 名。

Profile 路由必须显式实现，不能只保存 profile 名：

- 推荐启用 Hermes `gateway.multiplex_profiles`，bridge 请求 `/p/veranima-worker/v1/...`；该前缀必须使用 `veranima-worker` 自己 `.env` 中的 `API_SERVER_KEY`，默认 profile 的 key 会被拒绝。[3]
- 若不启用 multiplex，则单独启动 worker gateway，并为该 profile 配置独立端口；bridge 的 `base_url` 直接指向该端口，不再追加 `/p/<profile>`。
- 启动时调用目标 profile 路由下的 `/health/detailed`、`/v1/capabilities` 和 `/v1/models`，确认实际到达 `veranima-worker`，防止任务误跑在默认开发 profile。

### 4.5 建议配置

```yaml
tasks:
  enabled: false
  backend: hermes
  require_confirmation: true
  timeout_seconds: 600
  output_max_chars: 12000
  allowed_types: [文档处理, 信息检索, 系统操作, 自动化流程]
  hermes:
    base_url: http://127.0.0.1:8642
    profile: veranima-worker
    multiplex_profiles: true
    worktree_for_code: false  # 阶段 3 隔离探针通过后才允许改为 true
```

API bearer key 不写入此文件。它由环境变量或受保护的 secret source 注入。

`127.0.0.1:8642` 是当前 Hermes 官方文档和本机安装源码核验到的默认绑定地址与端口（`API_SERVER_HOST` / `API_SERVER_PORT`）；`API_SERVER_KEY` 是必需的服务端 Bearer token。[3] bridge 仍必须从配置读取，启动时以 `/health/detailed` 的实际目标为准，不能在代码中写死。客户端 token 的本地安全注入方式必须在阶段 0 明确，禁止写入受 Git 跟踪的 YAML。

## 5. 修改步骤

### 阶段 0：基线与可逆性

1. 冻结当前 R5 行为测试和 CLI 冒烟结果。
2. 记录 DSH 当前可用/不可用行为，避免迁移后误报“原功能一直可用”。
3. 建立独立 `veranima-worker` profile，不复用默认 profile。
4. 选择并验证 profile 路由：multiplex 模式使用 `/p/veranima-worker/...` + worker 专属 key；独立端口模式使用 worker 自己的 host/port。
5. 使用本机 `hermes --help`、官方 Programmatic Integration 文档和 `/v1/capabilities` 确认实际版本支持 `/v1/runs`、approval、stop 和 event stream。
6. Hermes API Server 只监听 loopback，配置独立 bearer key。
7. 暂不删除 `dsh_bridge.py`，保留一个发布周期作为回滚路径。

完成条件：Hermes worker 从已验证的 `terminal.cwd` 读取目标仓库；能在明确传入且不属于仓库的临时目录完成一次文件生成并读回；能查询 run 状态和停止运行。任何 cwd/路径与预期不一致都必须停止，不能让 agent 自行寻找“可能的项目目录”。

### 阶段 1：Hermes bridge

1. 增加 `hermes_bridge.py`。
2. `health()` 调用 `/health` 或 `/v1/capabilities`，区分服务离线、鉴权失败和功能缺失。
3. `submit()` 把 WorkOrder 转为自包含任务 prompt，并显式传递：目标、来源、约束、异常预案、允许修改范围、验证命令。
4. 在现有 veranima SQLite 增加独立 `task_runs` 表，保存 `task_id <-> run_id`、Hermes session ID、归一化状态、raw status、审批事件、时间和结果摘要。它是执行审计记录，不属于五层人物记忆；禁止另建 JSON 状态文件或只放内存。
5. `status()` 读取终态和输出，限制最大字符数。
6. `stop()` 调用 `/v1/runs/{id}/stop`；`stopping` 不等于已停止，继续轮询到终态。veranima 本地等待超时后也必须先请求 stop；只有远端最终确认 cancelled 才记 `timed_out`，无法确认则记 `orphaned`。
7. `approve()` 只接受 `once/session/always/deny` 四种 choice，并调用 `POST /v1/runs/{run_id}/approval`。当前 Hermes API 按 run 解析 pending approval，不接受 request ID；UI 可保存最新 `approval.request` 事件用于展示和防陈旧，但提交身份以 run ID 为准。run 已无 pending approval 时服务端返回 409，bridge 必须 fail-closed。

完成条件：fake HTTP 行为测试覆盖成功、失败、超时、取消、审批、服务离线、错误 JSON 和输出截断。

### 阶段 2：CLI 替换

1. 修改 `cli.py::_task_cmd()`，从 `run_dsh_task()` 切到 Hermes bridge。
2. 仍由 `is_task_request()` 决定是否进入任务管道。
3. 有 `needs_clarification` 时停止，不再像当前演示逻辑一样继续转交。
4. `require_confirmation=true` 时打印工单摘要并等待显式确认；非交互环境直接返回“需要确认”，不得默认执行。
5. 显示 run ID、状态、审批请求、停止结果和最终摘要。
6. 成功后调用 `Agent.task_result_story()`；原始输出可另存日志，不直接作为角色回复。

完成条件：真实 Hermes 冒烟覆盖文档读取、只读检索和一个临时目录文件生成；不修改主工作树。

### 阶段 3：代码任务 worktree 隔离

官方 `/v1/runs` 当前公开请求只承诺 `input`、`session_id`、`instructions`、`conversation_history` 和 `previous_response_id` 等字段，没有承诺每个 run 自动创建 worktree，也没有公开每次请求的 `cwd/worktree` 字段。[3] Hermes 的 worktree 文档描述的是 CLI/session 工作流。[4] 因此阶段 1-2 的 `/v1/runs` 默认只开放只读任务或明确位于隔离临时目录的写入任务；**在程序化 worktree 入口完成实测前，代码仓库修改任务必须拒绝执行**。不能靠 prompt 中一句“请使用 worktree”代替隔离。

1. 在 WorkOrder 中增加或复用任务类型识别，识别代码仓库修改任务，并默认标记 `requires_isolated_worktree=true`。
2. 在以下候选路径中做一次 spike，选择最小且受官方协议支持的路径：
   - TUI Gateway session API 创建受控会话并切换 worktree；
   - 受控子进程启动 `hermes --worktree --in <repo>`，再通过其会话协议取得状态；
   - 若后续 Hermes `/v1/runs` 正式暴露 worktree/cwd capability，则直接采用该字段。
3. 只有 `/v1/capabilities` 或实际行为探针证明“run 的文件工具根目录就是新建 worktree”，才允许提交代码任务。
4. 工作树创建、保留和清理由 Hermes 管理；veranima 只保存 path/branch/session/run 的关联，不复制 git worktree 实现。
5. 任务指令与工具策略同时约束：禁止修改 `main`、禁止 push/merge，必须运行项目指定测试。prompt 只是补充约束，不是安全边界。
6. TaskRun 返回 worktree、分支、commit、changed files、测试摘要和残余未验证项。
7. 用户确认后再由单独“应用结果”动作执行合并；普通任务完成不自动合并。
8. 合并前检查主工作树是否有用户修改；有冲突则停止并报告。

完成条件：在临时分支完成一次小改动，测试通过，主工作树在用户确认前保持不变；拒绝或失败后可清理 worktree。

### 阶段 4：QQ/桌宠任务入口

1. 不修改普通 `Agent.handle()` 的工具权限。
2. 在 adapter 前增加任务候选分流：只有 `is_task_request()` 命中并完成澄清、确认后才 submit。
3. 聊天界面显示任务卡片或最小状态文本：等待确认、运行中、等待审批、成功、失败、已取消。
4. 用户普通聊天消息不能被当成 approval；审批必须绑定 run ID，并通过明确按钮/专用命令选择 `once/session/always/deny`。展示内容来自该 run 最新 `approval.request` 事件；409 `approval_not_pending` 视为已过期，不重试到其他 run。
5. 任务运行期间普通陪伴对话继续使用 veranima，不与 Hermes session 混线。
6. 完成后由 `task_result_story()` 转述；文件清单、diff 和测试结果提供可展开详情。

完成条件：QQ/桌宠可提交、查看、停止一个任务；在任务运行时仍能正常聊天；veranima 重启而 Hermes gateway/run 仍存活时能恢复关联。Hermes run 404 时显示 orphaned，不显示“仍在运行”。

### 阶段 5：可选通用能力迁移

只有在 R5 实测稳定后逐项评估：

- 联网搜索是否迁给 Hermes；
- 通用定时任务是否迁给 Hermes cron；
- 文档转换是否改用 Hermes skill；
- 复杂项目任务是否用 delegation/kanban；
- 是否接入 MCP 服务。

每项迁移都必须证明可以删除 veranima 的重复代码，并保持人物输出边界。不能因为 Hermes “也有这个功能”就同时保留两套实现。

## 6. 自修改能力

### 6.1 允许的用户体验

用户可以直接对 chatbot 说：

- 修改 veranima 某个功能；
- 检查一个 bug；
- 更新设置界面；
- 运行测试并给出 diff；
- 在隔离分支尝试一种架构方案。

veranima 将其翻译为 WorkOrder，Hermes 在独立工作会话和 worktree 中执行，再由 veranima 转述结果。

### 6.2 硬边界

普通闲聊永远不能直接获得写仓库权限。以下条件缺一不可：

1. `is_task_request()` 命中明确任务动作；
2. WorkOrder 可验证，来源和作用域明确；
3. 用户确认执行；
4. Hermes approval 通过危险工具调用（绑定 run ID，choice 为 `once/session/always/deny`）；
5. 代码任务使用 worktree；
6. 测试完成并返回真实结果；
7. 用户确认合并。

禁止：

- 根据“这个功能烦死了”自动删代码；
- 根据角色扮演内容执行系统操作；
- 自动修改角色卡、人物记忆或安全边界；
- 自动提交到 `main`、push、创建 release；
- 将 `--yolo` 用作生产默认模式；
- 把审批按钮简化为普通自然语言“好/行/可以”。

## 7. 增益

### 7.1 开发试错减少

- 复用 Hermes 已验证的工具循环、session、provider、超时、停止和审批机制。
- 代码实验在阶段 3 worktree 能力实测通过后进入隔离工作树，失败不污染主工作树；在此之前代码任务保持禁用。
- 使用现成 terminal/file/web/browser/MCP/skills，避免逐个实现工具协议。
- Hermes 可直接读取仓库 `AGENTS.md` 和测试命令，减少上下文反复解释。
- 执行过程有 run ID 和事件流，故障定位从“一个阻塞子进程”升级为可观察生命周期。

### 7.2 工作能力提升

- 不局限于 DSH 支持的能力和 provider。
- 可以使用网页检索、浏览器、文件、终端、MCP、技能和委派。
- 支持中途 steer、人工 approval、stop 和后台执行。
- 可按 profile 切换模型、toolsets 和工程记忆。
- 复杂任务可在不扩展 veranima 人物内核的前提下增长。

### 7.3 产品体验提升

- 用户仍与同一人物对话，不需要另开开发工具。
- 工作结果由角色化转译，但事实、文件和测试结果可追溯。
- 任务与陪伴并行，长任务不会锁死聊天。
- veranima 自身重启后，只要 Hermes gateway 与 run 仍存活，就能从 `task_runs` 恢复关联并继续查询；Hermes gateway 重启不具备同等保证。
- 普通聊天保持当前低延迟和人物一致性。

### 7.4 维护收益

- 可删除 DSH 专属 Node 安装、patch 配置和独立 provider 凭据链。
- 后续可逐步删除重复搜索/调度代码，但只在替代链验证后执行。
- Hermes 更新可统一获得 provider、工具、MCP 和安全修复。
- veranima 的测试重点收敛到人物语义、协议边界和 bridge 契约。

## 8. 削弱与代价

### 8.1 延迟和资源

- Hermes Agent loop 比直接 LLM 请求更重；启动、工具选择和审批增加延迟。
- API Server、profile 和 veranima 核心成为多个常驻进程，启动和诊断更复杂。
- 工具 schema、skills 和工程上下文增加 token 成本。
- worktree 会占用额外磁盘空间。

因此普通陪伴对话不迁移到 Hermes。只有明确工作请求承担这部分成本。

### 8.2 可预测性下降

- 通用 Agent 有更大行动空间，同一任务可能选择不同工具路径。
- Hermes 更新可能改变工具行为、事件字段或默认策略。
- Skills/MCP/plugin 组合扩大了故障面和供应链风险。
- 自动代码修改无法只靠单元测试证明正确，仍需工作树审查和真实验收。

### 8.3 人物体验削弱风险

- 若 Hermes 输出直接送用户，工具腔、分析腔和工程日志会破坏角色感。
- 若完整聊天历史发送给 worker，工作记忆会污染人物隐私边界。
- 若任务完成自动写人物记忆，临时工程细节会挤占共同经历。
- 若所有对话都经 Hermes，工具导向会使角色变成“会扮演人物的通用助手”，偏离产品定位。

### 8.4 部署和分发代价

- clone 后不再只是安装 veranima 依赖，还需要 Hermes Agent、独立 profile 和 API Server 配置。
- Windows 后台服务、端口、Bearer key 和升级兼容需要额外安装检查。
- Hermes 官方 QQ adapter 使用 QQ Bot API，不能直接替代当前 NapCat OneBot 链；强行迁移会改变账号形态和已有私聊能力。
- 若 Hermes 未运行，任务功能不可用，但陪伴功能必须继续可用。

## 9. 风险、缺陷与防护

| 风险 | 后果 | 必须的防护 |
|---|---|---|
| 闲聊误触发工作任务 | 未经意图的文件/系统操作 | 规则触发 + 澄清 + 显式确认三层门禁 |
| Prompt injection 借任务控制工具 | 数据泄露、越权操作 | WorkOrder 只给最小输入；Hermes approval；限制 toolsets/目录 |
| 普通自然语言被当审批 | 危险命令放行 | 审批绑定 run ID；只接受四种 choice；使用按钮或专用命令 |
| Hermes 与 veranima 共享 memory writer | 记忆交叉污染和竞态 | 独立 profile/HERMES_HOME；人物记忆不共享 |
| 代码任务直接修改主工作树 | 用户改动丢失、难以回滚 | 强制 worktree；合并前检查 dirty state |
| stop 返回但工具仍在退出 | 误报取消、后台仍写文件 | `stopping` 不是终态；持续轮询直到 executor 退出 |
| API Server 端口或 key 暴露 | 本机或局域网未授权调用 | loopback 监听、Bearer key、不得关闭鉴权 |
| bridge 路由到默认 profile | 使用错误 memory、skills、审批和工作目录 | 显式 profile URL/端口；worker 专属 key；启动三端点身份探针 |
| Hermes 升级造成协议漂移 | bridge 失效 | 启动查询 `/v1/capabilities`；版本兼容测试；固定最低版本 |
| 任务输出进入人物记忆 | 工程日志污染关系与人格 | 默认不写；用户确认后只写摘要和证据 |
| 工具日志直接显示 | 协议/分析泄漏、角色破坏 | TaskResult 结构化；角色化转译；原始日志独立展示 |
| 外部 skill/MCP 不可信 | 供应链与任意代码风险 | allowlist、Hermes security audit、默认不自动安装 |
| veranima 重启后关联丢失 | 用户无法知道 Hermes run 是否仍在运行 | `task_id <-> run_id` 持久化；启动时重新轮询 |
| Hermes gateway 重启或 run 状态过期 | `/v1/runs/{id}` 返回 404，无法证明终态 | 非终态记录改为 orphaned；读回 session/worktree/产物；禁止猜测或自动重跑 |
| 两个 Agent 同时修改仓库 | merge 冲突、状态覆盖 | 每任务独立 worktree；限制并发；合并串行化 |
| 误以为 `/v1/runs` 自带 worktree | 实际直接修改主工作树 | 能力探针未通过前禁用代码任务；不得用 prompt 代替隔离 |
| Hermes 不可用 | 任务功能完全失效 | fail-closed，陪伴主链继续；保留 DSH 一个发布周期回滚 |
| 自修改循环失控 | Agent 修改其自身门禁后继续执行 | 门禁/bridge/审批相关文件列为高风险，必须人工 review |

### 9.1 已知设计缺陷

本方案不会消除以下问题：

- LLM 仍可能误解任务目标；WorkOrder 只降低而不能消除歧义。
- 工具执行成功不等于产品行为正确，必须读取回目标和运行测试。
- Hermes 的通用 memory 不能提供人物关系记忆；两套记忆仍然并存。
- profile 隔离带来配置重复，模型/provider 改动需要明确同步策略。
- 任务结果角色化可能省略关键失败细节，UI 必须保留原始技术详情入口。
- 浏览器、computer-use 和外部 MCP 的权限模型比文件任务更复杂，首期不开放。
- 当前 QQ/桌宠没有成熟的任务卡片和审批组件，阶段 4 需要真实 GUI/OneBot 验收。
- `/v1/runs` 本身不是耐久任务队列；需要跨 Hermes 重启继续执行的任务不能仅靠该接口保证。

## 10. 回滚策略

1. 首期保留 `dsh_bridge.py` 和原测试，不立即删除。
2. `tasks.backend` 支持 `hermes` 与暂时的 `dsh` 回滚值；只在过渡期保留，稳定一个发布周期后删除 DSH 分支。
3. Hermes bridge 只在现有 SQLite 增加独立 `task_runs` 执行表，不修改五层人物记忆语义；关闭 `tasks.enabled` 即可完全停用。
4. 阶段 3 验证通过后，所有代码任务都在 worktree 中，拒绝合并即可回滚；验证前代码任务本就不开放。
5. API Server 故障不影响 `Agent.handle()`、QQ、桌宠、TTS 和主动消息。
6. 若协议升级不兼容，capability check 失败并禁用任务入口，不做猜测式降级。

## 11. 测试与验收

### 11.1 单元和契约测试

- WorkOrder：闲聊不触发、缺路径追问、危险操作确认、deadline、task type。
- Bridge：health、submit、status、stop、approval、超时、错误 JSON、401/403、输出截断。
- Task runs：SQLite 原子写入、状态单向迁移、run ID 唯一、veranima 重启恢复、Hermes run 404 → orphaned、终态幂等。
- 身份关联：task ID、run ID 原样传递，不混用；approval 事件只关联同一 run。
- 状态机：queued → running → terminal；waiting_for_approval；stopping 不算 cancelled；completed 归一化为 succeeded 时保留 raw status。
- 隔离：任务结果不写人物 memory；工具输出不进入聊天 history。

### 11.2 生产接线测试

```text
用户任务输入
-> is_task_request
-> WorkOrder
-> clarification / confirmation
-> Hermes submit
-> run events
-> TaskRun
-> task_result_story
-> QQ/桌宠可见文本
```

必须断言最终可见文本不包含 API key、system prompt、tool schema、内部分析、原始 stderr 或未经转义的协议 JSON。

### 11.3 真实 Hermes 冒烟

至少覆盖：

1. 只读仓库并返回文件摘要；
2. 在临时目录创建一个文件并读回；
3. 启动长任务后 stop，确认最终进入 cancelled；
4. 触发一个需要 approval 的操作，拒绝后无副作用；
5. worktree 中修改一行代码、运行定向测试、返回 diff；
6. veranima 重启而 Hermes 保持运行时恢复同一 run；Hermes 重启后把非终态 run 标为 orphaned，并从 session/worktree/产物读回，不误报终态；
7. Hermes 离线时普通 QQ/桌宠聊天仍可用。

### 11.4 完成定义

阶段 0-2 的只读/临时目录首期只有同时满足以下条件才算完成：

- 当前 R5 CLI 中可安全迁移的只读和隔离临时目录任务由 Hermes `/v1/runs` 替换；DSH 仍保留为显式回滚后端，不做静默 fallback；
- WorkOrder 澄清和确认不会被绕过；
- 状态、停止和审批均为真实生产链可用；
- 代码任务明确禁用，不因首期完成而宣称自修改已可用；
- 任务结果经角色化转译，原始详情仍可查看；
- 人物记忆、关系状态和普通聊天未迁移、未污染；
- Hermes 离线不影响陪伴主链；
- 定向测试、全量 pytest、真实 Hermes 冒烟全部通过；
- 文档明确标记已实现、部分实现、暂缓和未实机验证项。

阶段 3 的代码自修改能力另行完成，附加条件是：程序化 worktree 隔离探针通过、用户确认前主工作树零变化、真实 diff/测试结果可读回、拒绝合并可回滚。`/v1/runs` 不具备可验证 worktree 隔离时，阶段 3 保持未完成，不能降级到主工作树。

## 12. 建议实施顺序与停点

```text
阶段 0 基线/独立 profile
  -> 阶段 1 bridge
  -> 阶段 2 CLI 替换
  -> 阶段 3 代码 worktree
  -> 实测收益评估（强制停点）
  -> 阶段 4 QQ/桌宠入口
  -> 阶段 5 按证据逐项迁移通用能力
```

阶段 3 后必须停下来比较：

- 任务成功率；
- 平均完成时间和 token 成本；
- 用户澄清次数；
- worktree 回滚成功率；
- 任务失败是否可诊断；
- 人物聊天是否出现工具腔或延迟回归。

只有数据证明 Hermes 后端优于 DSH，才进入 QQ/桌宠任务入口。搜索、cron、MCP 和普通对话不随 R5 一起迁移。

## 13. 文件级修改清单

| 文件 | 修改 | 状态 |
|---|---|---|
| `docs/HERMES_AGENT_INTEGRATION_SPEC.md` | 本方案 | 本次新增 |
| `docs/R5_SPEC.md` | 实施时将 dsh bridge 更新为 Hermes bridge，并同步状态 | 待实施 |
| `src/veranima/tools/hermes_bridge.py` | `/v1/runs`、status、stop、approval 协议 | 待实施 |
| `src/veranima/core/workorder.py` | 仅补必要执行元数据，不重写识别/校验 | 待实施 |
| `src/veranima/memory/schema.py` | 增加独立 `task_runs` 执行审计表 | 待实施 |
| `src/veranima/memory/store.py` | 增加 task run 原子 CRUD/恢复，不并入五层记忆 | 待实施 |
| `src/veranima/cli.py` | `_task_cmd()` 改用 Hermes bridge | 待实施 |
| `src/veranima/core/agent.py` | 复用 `task_result_story()`；不改普通 handle 工具权限 | 待实施 |
| `src/veranima/pet_server.py` | 阶段 4 增加任务状态 WS 消息 | 后续可选 |
| `src/veranima/adapters/qq.py` | 阶段 4 增加显式任务确认/状态入口 | 后续可选 |
| `pet/chat.*` | 阶段 4 增加任务卡片和审批按钮 | 后续可选 |
| `src/veranima/tools/dsh_bridge.py` | 过渡期保留，稳定后删除 | 待淘汰 |
| `tests/test_r5_tasks.py` | bridge 契约、状态、确认、隔离、回滚 | 待实施 |

## 14. 明确不做

- 不把 veranima 改成 Hermes Bot profile 的一个角色。
- 不用 `SOUL.md` 取代 Character Card V3、PersonaBrief 或关系状态。
- 不用 Hermes 内置 `MEMORY.md`/`USER.md` 取代人物 SQLite。
- 不迁移当前 NapCat OneBot 到 Hermes 官方 QQ Bot API。
- 不让普通陪伴聊天默认经过工具 Agent。
- 不在首期接 computer-use、cron、MCP、浏览器自动化或多 Agent kanban。
- 不默认自动 commit、push、merge、发布或修改安全门禁。
- 不为尚不存在的多后端需求提前设计插件式 executor 框架。

## 15. 本地依据

- veranima 总纲：[`DESIGN.md`](DESIGN.md)
- veranima R5：[`R5_SPEC.md`](R5_SPEC.md)
- 人物记忆契约：[`MEMORY_SPEC.md`](MEMORY_SPEC.md)
- 人格循环契约：[`PERSONA_LOOP_SPEC.md`](PERSONA_LOOP_SPEC.md)

## Sources

[1] https://hermes-agent.nousresearch.com/docs/developer-guide/architecture — Hermes Agent Architecture
[2] https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration — Hermes Programmatic Integration
[3] https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server — Hermes API Server
[4] https://hermes-agent.nousresearch.com/docs/user-guide/git-worktrees — Hermes Git Worktrees
[5] https://hermes-agent.nousresearch.com/docs/user-guide/features/memory — Hermes Persistent Memory
[6] https://hermes-agent.nousresearch.com/docs/user-guide/features/personality — Hermes Personality and SOUL.md
