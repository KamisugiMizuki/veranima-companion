# R1 专项：共同经历与状态连续性

> 目标：让角色拥有可追溯的过去和可解释的当下。不要用随机错字/随机遗忘模拟真人。
> 现有复用：`core/agent.py`、`core/state.py`、`memory/store.py`、`memory/schema.py`、`core/prompts.py`。
> 最小新增：记忆分类映射、状态字段、候选记忆校验、版本链关联。
> 记忆系统的完整数据、写入、冲突、召回、遗忘、隐私、文风学习和验收契约以 `docs/memory/MEMORY_SPEC.md` 为唯一真值；本文只保留 R1 阶段边界和摘要。
> 人格形成、用户思维框架、共同意义、多维关系状态、自传反思和表达回用以 `docs/persona/PERSONA_LOOP_SPEC.md` 为唯一真值；本文不重复其规则。

## 1. 数据契约

### 1.1 记忆类型

旧数据库层映射如下，不立刻改表 CHECK：

| R1 类型 | 旧 layer | 写入对象 |
|---|---|---|
| identity | `core_profile` | 角色卡，不写用户对话 |
| user_fact | `semantic` | 用户偏好/经历/边界 |
| shared_episode | `episodic` | 共同事件、情绪、结果 |
| commitment | `procedural` | 承诺与协作规则 |
| session | `session` | 当前任务/短期视觉 context |

人格循环不新增数据库 layer，第一版使用 `meta.kind` 区分：

| meta.kind | layer | 内容 |
|---|---|---|
| user_framework / character_belief | `semantic` | 用户思维框架 / 角色形成的观点 |
| shared_meaning / relationship_event | `episodic` | 共同解释 / 靠近、冲突和修复事件 |
| interaction_rule | `procedural` | 明确关系边界和互动规则 |
| persona_reflection | `session` 或 `core_profile` | 待整合反思 / 版本化自传摘要 |

普通事实不能误判为用户框架；理解用户观点不等于角色采纳。结构稳定且 JSON meta 查询成为瓶颈时，允许按 `PERSONA_LOOP_SPEC.md` 迁入独立 SelfModel/RelationshipModel 表。

`MemoryEntry.meta` 增加：`subject`, `event_time`, `emotion`, `status`, `expires_at`, `supersedes`。不修改旧字段语义。

### 1.2 候选记忆

```json
{
  "kind": "user_fact|shared_episode|commitment",
  "content": "用户喜欢下雨天",
  "confidence": 0.0,
  "importance": 0.0,
  "source_message_id": 123,
  "event_time": "可选 ISO 时间",
  "expires_at": null,
  "needs_confirmation": false
}
```

程序校验：kind 白名单、content 非空且 <=500 字、confidence/importance 截断到 0-1、source 必须存在。LLM 不得直接执行 SQL。

## 2. 写入流程

`Agent.handle()` 中固定顺序：

```text
store_message(user) → recall → LLM reply → store_message(assistant)
→ rule_extract(user) → optional LLM candidate extraction
→ validate → dedupe → store/update_latest
→ persist AgentState
```

低成本实现第一版只做规则提取：

- “记住/以后/我喜欢/我讨厌/我不喜欢” → `user_fact`。
- “我们一起/刚才/上次……结果” → `shared_episode`，仅当有事件或结果。
- “以后提醒/下次记得/你答应” → `commitment`，问句不命中。

候选提取失败不影响回复；写入失败只记录日志，不阻塞用户。

## 3. 去重与修正

- 同层同主题相似度 >=0.92：忽略重复。
- 0.78-0.92：保留新版本，调用 `update_latest()`，`meta.supersedes=old_id`。
- 用户明确纠正：必须写新版本，旧版本不删除。
- 召回只取版本链最新有效记录；旧版本用于解释和审计。

## 4. 召回

复用 `MemoryStore.recall()`，先不新增检索框架。排序最终分数：

```text
0.45 * semantic_similarity
+ 0.20 * FTS relevance
+ 0.15 * freshness
+ 0.10 * importance
+ 0.10 * confidence
```

暂时无法取得某项时归一化剩余权重，不补随机分。每层预算由 config 控制，默认总注入不超过 5600 字符。

注入格式固定：

```text
[共同经历|置信度:高|时间:上周] 用户和角色一起完成了……
[用户事实|置信度:中] 用户喜欢……
```

模型只需遵守表达梯度：高置信自然调用，中置信试探，低置信承认不确定。

## 5. 状态契约

现有 `AgentState` 先保留 energy/mood/attachment，新增：

```python
social_appetite: float = 0.8
attention_topic: str = ""
attention_scene: str = "normal"
last_interaction_channel: str = ""
last_cause: str = "startup"
```

`to_snapshot/from_snapshot` 使用 `.get()` 默认值，旧 SQLite 自动兼容。每次更新调用：

```python
state.apply(event="user_message|assistant_reply|time_decay|scene_change|user_feedback", delta={...})
```

第一版不新建通用状态机库；用 `AgentState` 方法和事件字符串即可。每次变更记录 debug 日志 `state changed cause=...`，不把内部数值直接展示给用户。

`attachment` 继续作为兼容汇总值，但不能承担完整关系建模。人格循环 P-3 允许新增 trust/familiarity/intimacy/reciprocity/safety/conflict_tension/repair_progress；更新只由明确关系事件驱动，不能按消息数量线性增长。

## 6. 配置

```yaml
memory:
  recall_top_k: 5
  recall_threshold: 0.30
  max_injected_chars: 5600
  candidate_extraction: rules
  correction_enabled: true
state:
  social_appetite_initial: 0.8
  energy_decay_per_minute: 0.02
```

删除重复 `chat` 配置段；同一键只能出现一次。

## 7. 测试与验收

定向测试：`tests/test_memory.py`, `tests/test_agent.py`, 新增 `tests/test_continuity.py`。

必须覆盖：空库不编造、明确偏好写入、重复去重、纠正版本链、跨重启状态、换卡不污染、低置信措辞、视觉 context 到期不进入长期记忆。

人格循环还必须覆盖：用户框架提取精度、角色核心冲突时保留分歧、共同意义不伪造、关系冲突闭环、框架回用不逐字复述、Persona Brief 预算、旧角色自传隔离。完整验收见 `PERSONA_LOOP_SPEC.md` 第 17 节。

文风学习验收、历史压缩、时间有效性、冲突类型、用户删除和离线 benchmark 见 `docs/memory/MEMORY_SPEC.md` 第 9、13、14、18 节。
