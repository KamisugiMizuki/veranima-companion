# SHARED_CREATION_SPEC：共同创作与共同经历系统

> 状态：设计稿 v1.0（2026-08-20）。
> 实现状态：当前仓库已有 `shared_episode/shared_meaning/commitment/relationship_event`、P-0~P-9 和 MemoryStore 基础；本文细化“项目/章节/决策/产物”工作流，**不宣称 UI 和完整 DTO 已实现**。
> 核心原则：关系链接来自可追溯的共同事件、共同决策、分歧修复和完成结果，不来自消息数量、在线天数或固定 attachment 加法。

## 1. 产品命题

陪伴不只发生在“问一句答一句”。用户和角色可以共同写故事、设计游戏、推进一个学习主题、维护一项软件工作或完成一组虚构任务。真正有价值的不是系统宣布“我们更亲密了”，而是：

- 双方对正在做什么有共同定义。
- 用户的选择确实改变了产物或下一幕。
- 角色保留自己的判断，可以不同意、卡住和修正。
- 完成/失败/暂停都留下可回看的事件与意义。
- 关系变化来自证据和用户确认，而不是计数器。

## 2. 范围与边界

### 支持

- 共同创作虚构故事、角色、世界观、剧本、游戏规则。
- 共同完成学习/研究/软件/创作项目；角色的参与仅通过对话、分析、草稿、检查清单和工具调用表达。
- 共同决策、分支实验、回顾、重写和恢复。
- 产物版本、贡献记录、未完成线程和共同意义。

### 不支持

- 角色声称自己在现实世界亲自完成了用户没有提供证据的动作。
- 替用户联系第三方、约线下见面、打电话、寄送物品或作现实承诺。
- 用“共同项目”伪造关系升级、依赖或持续后台意识。
- 让 LLM 直接改 SQLite、关系值或项目状态；所有状态变更走程序校验。
- 复制原作、受保护作品或特定作者的原文作为“共同作品”；用户需自行确认使用权。

## 3. 核心对象

第一版用现有 SQLite/MemoryStore 承载，允许先使用 `meta.kind` 和 JSON，不新增平行人格数据库。

### 3.1 Project

```json
{
  "project_id": "p_01",
  "kind": "story|software|learning|game|research|custom",
  "title": "屋顶上的短篇",
  "purpose": "共同完成一篇可回看的短篇故事",
  "owner": "user|shared",
  "status": "draft|active|paused|blocked|completed|abandoned|archived",
  "current_arc_id": "arc_01",
  "scope": {"included": [], "excluded": []},
  "user_boundary": {"topics": [], "style": [], "do_not_store": []},
  "created_at": "...",
  "updated_at": "...",
  "version": 3
}
```

### 3.2 Arc / Scene

- `Arc`：项目中的阶段或章节，包含目标、出口条件和 open threads。
- `Scene`：一次可恢复的对话工作单元；有输入、当前分支、参与者、决定和产物引用。
- `status`：`planned/active/paused/blocked/resolved`。
- Scene 不等于聊天消息；消息只是证据，Scene 是整理后的协作状态。

### 3.3 Decision

```json
{
  "decision_id": "d_01",
  "scene_id": "s_01",
  "question": "主角是否回到屋顶？",
  "options": [{"id":"a","label":"回去","consequence":"..."}],
  "chosen": "a",
  "decided_by": "user|character|both",
  "rationale_user": "...",
  "rationale_character": "...",
  "status": "proposed|chosen|reopened|rejected",
  "evidence_message_ids": [123]
}
```

角色可以提出选项和理由，但用户拥有最终项目控制权；双方理由分开保存，不把角色意见伪装成用户意见。

### 3.4 Contribution / Artifact

- `Contribution`：某次对话、草稿、代码片段、设定、审阅或决策对项目的贡献。
- `Artifact`：可回看的文件/文本/结构化结果，保存 `artifact_id/version/hash/path_or_inline_text/source_message_ids`。
- 默认只保存摘要、版本哈希和用户明确确认的正文；大文件仍在工作区，不把完整 stdout/日志塞进记忆。
- “由角色贡献”表示模型生成了草稿，不表示角色在现实中执行了动作。

### 3.5 OpenThread

```json
{
  "thread_id": "t_01",
  "project_id": "p_01",
  "summary": "需要决定反派是否知道真相",
  "priority": "low|normal|high",
  "blocked_by": ["d_03"],
  "next_action": "下次先比较两个版本",
  "status": "open|snoozed|resolved|dropped",
  "last_confirmed_at": "..."
}
```

未完成线程可以作为后续入口，但不能变成无条件主动消息；必须通过场景、用户可用性、冷却和 R4 gate。

## 4. 生命周期

```text
draft
  → active
  → paused ↔ active
  → blocked → active
  → completed → archived
  ↘ abandoned
```

### 4.1 开始

1. 用户提出共同目标，或角色提出候选。
2. 程序提取项目类型、目标、边界和完成定义。
3. UI 显示“将创建什么、会保存什么、不会保存什么”。
4. 用户确认后创建 Project；单句“我们以后可以写个故事”只生成 candidate，不直接建项目。

### 4.2 进行

每个 Scene 固定顺序：

```text
加载当前 project/arc/open threads
→ 显示本次目标
→ 角色提出最少必要选项
→ 用户/双方决策
→ 生成或修改 artifact
→ 记录 contribution 和 evidence
→ 更新 open threads
→ 询问是否确认本次共同事件
```

角色不能为了制造亲密感而擅自增加支线、改变用户明确的边界或把自己的草稿写成最终版本。

### 4.3 节点与完成

可触发回顾的节点：开始、第一次共同决定、连续受阻、方案改变、阶段完成、最终完成、用户要求暂停/删除、冲突修复。节点摘要应包含事实、双方解释、未决问题和后续动作。

完成条件由用户或项目定义决定，例如：故事完成终稿、软件功能通过测试、学习章节完成复盘。模型不得仅凭“聊了很多”宣布完成。

## 5. 共同事件、记忆和关系写入

### 5.1 记忆映射

| 对象 | MemoryStore 映射 | 写入条件 |
|---|---|---|
| 事实/产物结果 | `episodic`, `meta.kind=shared_episode` | 有事件、结果和 evidence |
| 双方解释 | `episodic`, `meta.kind=shared_meaning` | 用户解释与角色解释分开；共识可为空 |
| 未完成动作 | `procedural`, `meta.kind=commitment` | 用户明确同意或任务确有责任 |
| 冲突/越界/修复 | `episodic`, `meta.kind=relationship_event` | 有触发、双方处理和状态 |
| 临时场景 | `session`, 有 `expires_at` | 不得自动晋升长期 |

### 5.2 写入闸门

```text
候选生成
→ kind/主体/敏感度/证据校验
→ 检查是否真实发生而非角色预设
→ 用户确认或明确共同完成
→ 去重/版本链
→ 写入 shared_episode/shared_meaning/commitment
→ 可选关系事件候选
```

没有用户确认时，可以保留短期 `candidate` 或 session，不得写“我们已经完成了”“这改变了我们的关系”。

### 5.3 关系变化

关系模型继续复用现有 `RelationshipModel/AgentState`，不新增“共同创作亲密度”单独分数。共同事件最多产生一个待审核 `RelationshipEventCandidate`，维度包括：

- `trust`：用户是否能依赖角色的协作行为。
- `familiarity`：双方是否形成可复用的共同语境。
- `reciprocity`：决策和贡献是否双向。
- `repair`：分歧后是否完成澄清/修复。
- `boundary_respect`：是否尊重暂停、重写和删除。

程序规则：

- 事件必须有 evidence 和用户确认；单次事件的变化有上限。
- 同一项目连续事件不线性累加；相似事件进入冷却和去重。
- 消息数量、字数、在线天数、项目数量不能单独增加 attachment。
- 失败/暂停不自动降低关系；越界且未修复才产生负向候选。
- 用户可以拒绝关系解释；拒绝保留事件事实，但不写入关系变化。

## 6. 分歧、失败和恢复

### 分歧

双方解释分别保存：

```json
{
  "event": "我们删掉了第一版结局",
  "user_interpretation": "终于不再被旧设定绑住",
  "character_interpretation": "删掉很可惜，但新的方向更自由",
  "agreed_meaning": null,
  "status": "contested"
}
```

角色可以保留不同意见；不能把 `contested` 伪装成共识。

### 失败

- 生成失败：保留用户决定和已有 artifact，scene 标记 `blocked`，提供重试/手写/回退版本。
- 长期中断：项目进入 `paused`，open thread 保留但不自动催促。
- 用户改变目标：创建新版本或新 arc，不覆盖旧事实。
- 用户删除：删除项目派生摘要和关系候选；原始聊天是否删除单独确认。
- 角色卡切换：项目事实/用户贡献保留；角色解释和风格按角色边界重新计算，不把 Yuki 的个人记忆迁移成 Zima 的经历。
- 现实项目：角色只能声明“我们在对话中共同规划/审阅了”，不能声称已经替用户提交、联系或执行现实动作。

## 7. UI 设计

复用现有聊天窗口，不新增平行 App：

### 项目入口

- 顶部可折叠“共同项目”区域。
- 当前项目名、状态、当前 Scene、下一步和未完成线程。
- `新建 / 继续 / 暂停 / 回顾 / 导出 / 删除`。

### Scene 工作台

- 当前目标和边界。
- 决策卡：选项、用户理由、角色理由、重新打开。
- 产物预览：版本、差异、回退、确认保存。
- “确认写入共同经历”明确展示写入内容和 evidence。
- 失败状态保留输入和已有草稿，提供重试与回退。

### 回顾

按时间线显示：开始、决定、受阻、修复、完成。区分“发生的事实”“用户解释”“角色解释”“双方共识/分歧”。不要只显示“亲密度 +1”。

### 无障碍与隐私

- 所有项目/决定/版本操作有明确文本 label 和键盘路径。
- 删除、清空、确认写入需要明确确认；撤销窗口至少覆盖本次 session。
- 用户可设置项目是否允许进入长期记忆、是否允许作为主动性来源、是否包含在角色包导出。
- 私人项目默认不进入风格语料和角色包。

## 8. 实现切片

1. C-1：Project/Scene/OpenThread DTO，先存 `session`，只做聊天内项目状态。
2. C-2：Decision/Contribution/Artifact 版本和回退。
3. C-3：shared_episode/shared_meaning 写入确认与回用。
4. C-4：关系事件 candidate、分歧/修复闭环，接现有 RelationshipModel。
5. C-5：聊天窗口项目面板、时间线、删除/导出和无障碍。
6. C-6：离线 benchmark，覆盖正常完成、暂停、失败、分歧、删除、换卡隔离和无证据拦截。

## 9. 行为验收

- 用户说“以后一起写”不会无确认创建长期项目。
- 每次共同决定都能回到对应 evidence 和 artifact 版本。
- 角色提出意见但不能抢走用户最终控制权。
- 失败、暂停、重写和删除不会伪造成功或破坏已有事实。
- 共同意义保留双方解释和分歧，不能自动生成虚假共识。
- 只有用户确认/明确完成的事件进入 shared_episode/relationship candidate。
- 相关新话题能自然回用一次共同意义，普通闲聊不机械引用。
- 同一项目重复事件不会线性提高 attachment。
- 换角色后项目事实连续，但旧角色的自我解释不泄漏。
- 真实项目中角色不声称完成了现实行动。

暂缓：多人协作、实时协同编辑器、在线同步、项目市场、自动发布、角色自主创建现实任务和后台“等待用户”的拟人化意识流。
