# MEMORY_SPEC：人物连续性与记忆系统实现契约

> 状态：R1 记忆专项设计基线（2026-08-19）。
> 产品目标：记忆不是“检索到更多文本”，而是让同一个人物拥有可追溯的过去、能纠正的认知和不会越界的熟悉感。
> 技术约束：继续使用 SQLite + FTS5 + memory_embedding 归一化 blob 表（Python 暴力余弦；2026-08-29 起 vec0 退役，与 sqlite-vec 同为 exact KNN、结果逐位一致，换出的是安卓 Chaquopy 扩展加载兼容性）；不引入 Mem0、Memanto、MemPalace、图数据库或远程记忆服务作为运行时依赖。2026-08-26 选型评估结论：维持 SQLite 栈不变，吸收外部记忆库机制（热度衰减/双时间线/夜间整理/审核收件箱）以增量方式落地，评估与阶段见 `docs/memory/MEMORY_BACKEND_EVAL.md`。
> 唯一实现入口：`src/veranima/memory/`；`Agent` 只编排，不直接写 SQL。
> 人格形成与关系性回用的行为契约见 `docs/persona/PERSONA_LOOP_SPEC.md`。本文只规定其证据、候选、版本、召回、删除和预算，不决定角色是否采纳用户观点。

## 1. 设计原点

用户应能感知到以下连续性：

1. 她记得用户明确说过的偏好、经历、边界和承诺。
2. 她能在相关时机自然提起共同经历，而不是机械复述数据库句子。
3. 新信息与旧信息冲突时，她承认认知发生了变化，不静默覆盖历史。
4. 她会忘记低价值细节，但不会随机忘记身份、边界和未完成承诺。
5. 她能适应用户偏好的互动节奏和文风，但仍然保持角色自己的声音。
6. 用户可以查看、纠正、删除和重置记忆与文风学习结果。

以下不属于成功：存储整段聊天后声称“有记忆”、把所有召回内容塞进 prompt、靠 LLM 自己决定删除数据、随机制造记错、逐字模仿用户。

## 2. 参考系统与取舍

### 2.1 借鉴矩阵

| 来源 | 借鉴 | 不借鉴 |
|---|---|---|
| Sakura | 五层记忆；记忆搜索/新增/更新/忘记四类显式操作；ContextOrchestrator 的 trust/priority/token_budget/sensitivity；每 8 轮整理、0.92/0.78 阈值和写回上限 | mem0 运行时依赖；插件规模；将长期记忆管理暴露给普通对话模型自由调用 |
| AIRI | 历史摘要仍是结构化历史的一部分；压缩时保留最近 turn/reaction 对；runtime context 按 source bucket 管理并记录 replace/append 语义 | 当前长期记忆页面/模块骨架；Vue/Pinia/服务端模块体系 |
| Mem0 v3 | 单次 ADD-only 提取；用户事实与 agent 已确认行为同等进入候选；semantic/BM25/entity/temporal 并行信号；单次召回而非 agentic loop | 托管平台；专有优化；外部 vector backend；默认 OpenAI embedding/LLM；把托管成绩视为 OSS 可复现成绩 |
| Memanto | typed memory；冲突协调；temporal versioning；“现在相信什么”与“当时相信什么”分离；最小 briefing；可导出工作格式 | Moorcheh 闭源信息论搜索引擎；Docker 服务；独立 memory agent；13 类全部照搬 |
| MemPalace | local-first；原文证据保留；检索后端可替换；keyword/temporal/preference boosting；可复现 benchmark 结果文件 | ChromaDB；宫殿隐喻目录；纯 verbatim、不做提取/总结的路线 |

### 2.2 基准声明边界

- Mem0 README（2026-08-19）报告 LoCoMo 92.5、LongMemEval 94.4，但明确说明成绩包含托管平台专有优化，OSS 用户只能期待方向相似，不能直接复现。
- Memanto 论文报告 LongMemEval 89.8%、LoCoMo 87.1%、单查询和低于 90ms 的检索，但核心依赖 Moorcheh 搜索引擎；该结果不能推导出 SQLite 方案具有同等延迟或准确率。
- MemPalace README 报告 LongMemEval retrieval R@5 raw 96.6%、hybrid held-out 98.4%，衡量的是检索召回，不等于最终回答正确率。
- veranima 不以复制这些数字为目标。验收重点是：事实召回、时序有效性、冲突修正、无依据不编造、人物连续性和可解释删除。

参考：

- https://github.com/mem0ai/mem0
- https://github.com/moorcheh-ai/memanto
- https://arxiv.org/abs/2604.22085
- https://github.com/MemPalace/mempalace
- 本地只读：`D:/sakura-v0.9.7-windows-x64`
- 本地只读：`D:/addons/airi-main`

## 3. 四阶段数据流

```text
Raw Evidence
  原始 user/assistant 消息，逐字、带时间和来源
      ↓
Memory Candidate
  规则或 LLM 提取的待校验结构，不可直接召回
      ↓ validate / normalize / conflict / dedupe
Canonical Memory
  有类型、时间、来源、版本和有效状态的规范记忆
      ↓ retrieve / rank / budget / redact
Context Brief
  当轮最小相关片段，带来源与置信度，注入 prompt
```

四阶段必须可区分。不能把原始消息直接当长期事实，也不能让候选绕过程序校验进入 prompt。

## 4. 数据域与所有权

### 4.1 原始消息 `messages`

用途：对话恢复、证据审计、FTS 查询、候选提取输入、历史摘要来源。

规则：

- 用户消息和最终展示给用户的 assistant 文本立即写入；`created_at` 使用带时区的 ISO 时间，并在对话恢复和 LLM prompt 中保留为本地显示时间。
- 图片只写 `[图片]` 占位与可选结构化描述，不写 base64。
- 工具原始 stdout、内部 reasoning、异常堆栈、截图、TTS 音频不写入。
- assistant 的虚构日常只留在消息历史，不自动晋升为用户事实或共同经历。
- 每条长期记忆必须能通过 `source_message_id` 或 provenance 追溯到证据；手工导入使用显式 `manual` 来源。

### 4.2 规范记忆 `memories`

现有五层数据库保留，产品类型使用映射：

| 产品类型 | 旧 layer | 内容 | 默认保留策略 |
|---|---|---|---|
| `identity` | `core_profile` | 角色身份、用户称呼、明确边界、不可遗忘核心档案 | 常驻，不自动衰减；角色卡本体不重复写入 |
| `user_fact` | `semantic` | 用户偏好、经历、长期环境事实 | 可修正、可衰减，反复确认可强化 |
| `shared_episode` | `episodic` | 共同事件及结果、参与方、当时情绪 | 时间敏感，可摘要化，不随机删除重要事件 |
| `commitment` | `procedural` | 用户要求、承诺、协作规则、未完成提醒 | 未完成时不衰减；完成后保留结果摘要 |
| `session` | `session` | 当前任务、临时话题、视觉/工具短期 context | 必须有 TTL，过期不召回，不转长期 |

人格循环复用这些 layer，并通过 `meta.kind` 扩展产品类型：

- semantic：`user_framework`, `character_belief`。
- episodic：`shared_meaning`, `relationship_event`。
- procedural：`interaction_rule`。
- session/core_profile：`persona_reflection`, `self_model_snapshot`。

其中 user_framework 是“用户如何理解世界”的证据化模型，不是普通 user_fact；character_belief 是角色自己的观点，必须经过角色核心兼容检查；shared_meaning 必须区分用户解释、角色解释、共识和分歧。完整字段与晋升规则见 `PERSONA_LOOP_SPEC.md` 第 3-7 节。

情绪不单独建 memory layer。情绪是 episode 元数据和 AgentState 的输入，不形成“用户永远很难过”一类静态结论。

### 4.3 文风画像 `style_profile`

文风画像是派生状态，不是事实记忆，不进入 `memories` 和普通语义检索。第一版继续持久化在 `data/style.json`，后续迁入 SQLite 时保持独立表。

它只描述交流偏好和稳定统计：

```python
@dataclass
class UserStyleProfile:
    sample_count: int
    char_count: int
    avg_message_chars: float
    avg_sentence_chars: float
    question_ratio: float
    newline_ratio: float
    emoji_ratio: float
    exclamation_ratio: float
    ellipsis_ratio: float
    parenthetical_ratio: float
    ascii_ratio: float
    japanese_ratio: float
    formality: float
    directness: float
    detail_preference: float
    confidence: float
    updated_at: str
    source_id: str
    scene_count: int
    reviewed_count: int
    quality_score: float
```

不保存“用户常说过的完整句子”。高频词镜像仅作为低权重临时统计，不能成为人格规则。

## 5. 候选记忆契约

```json
{
  "kind": "user_fact|shared_episode|commitment",
  "content": "用户喜欢下雨天",
  "subject": "user",
  "confidence": 0.78,
  "importance": 0.60,
  "source": "rule_extract|llm_extract|manual|agent_confirmed",
  "source_message_id": 123,
  "event_time": "2026-08-19T10:00:00+08:00",
  "valid_from": null,
  "valid_to": null,
  "expires_at": null,
  "emotion": "期待",
  "status": "active",
  "needs_confirmation": false
}
```

程序校验顺序固定：

1. kind 白名单。
2. content 去首尾空白、压缩重复空格，非空且 <=500 字。
3. source 和 source_message_id 必须存在；manual 可用独立 manual source id。
4. confidence/importance clamp 到 0..1；无法解析则拒绝，不猜默认值。
5. ISO 时间解析；未来过远、反向区间和非法时区拒绝。
6. 敏感信息检测；密码、token、API key、验证码、支付凭据直接拒绝。
7. commitment 问句、转述和假设句不能写成 active 承诺。
8. session 必须有 expires_at。
9. 只有通过校验的候选进入冲突/去重阶段。

LLM 只生成候选 JSON，不生成 SQL、memory id、版本号或删除动作。

## 6. 写入策略

### 6.1 一轮固定顺序

```text
store_message(user)
→ retrieve Context Brief
→ LLM reply
→ store_message(assistant)
→ rule_extract(user)
→ optional single-pass LLM extraction
→ validate candidates
→ normalize subjects/entities/time
→ resolve conflict/dedupe
→ ADD or update_latest
→ update style profile
→ persist AgentState
```

任何记忆步骤失败均不得阻断回复。失败必须记录阶段、候选摘要、拒绝原因，不记录敏感原文。

### 6.2 提取来源

第一阶段按优先级：

1. 明确规则：偏好、边界、共同事件、承诺和纠正。
2. `Reply.memory_candidates`：仅作为低置信候选；仍需程序校验。
3. 可选 LLM 单次 ADD-only 提取：只处理未被规则覆盖的 durable facts，默认关闭。
4. agent 已确认行为：例如“我已把提醒标记完成”可写 commitment 新版本，来源=`agent_confirmed`。

不实现逐条 UPDATE/DELETE 的 LLM memory tool。删除和修正由确定性 API 执行。

### 6.3 什么值得记

写入：

- 明确、稳定、未来有用的偏好和边界。
- 有结果或情绪意义的共同事件。
- 有截止时间、条件或未来动作的承诺。
- 用户明确纠正的事实。
- 多次出现且跨场景稳定的交流偏好。

不写入：

- 寒暄、一次性情绪、纯问句、模型推测。
- assistant 自己生成的态度、口吻和虚构日常。
- 视觉截图内容，除非用户在对话中明确确认并形成共同事件。
- 密钥、密码、验证码、支付信息和私聊原文。
- 外部任务 stdout、日志和完整文件内容。
- 单次文风表现和单次口癖。
- 未经用户确认的“你真正是怎样的人”式心理推断。
- 仅由角色回复自身生成、无用户证据的 user_framework/shared_meaning。

人格循环值得写入的特殊候选：用户明确的定义/比喻/因果模型/价值排序；跨场景重复的私人概念；经双方确认的共同意义；明确的关系边界、冲突和修复结果。普通观点至少需要两次独立证据，或一次陈述加一次用户确认。

## 7. 规范化与实体锚点

借鉴 Mem0 entity linking，但不新增图数据库。实体锚点只存在于 `meta.entities`：

```json
{
  "entities": [
    {"type":"person","canonical":"用户","aliases":["我"]},
    {"type":"project","canonical":"veranima","aliases":[]}
  ]
}
```

第一版只做轻量规则实体：user、character、project、place、date。实体用于：

- 同主题候选分组。
- 冲突检测。
- 查询 boost。
- 避免“用户喜欢 X”和“角色喜欢 X”混为一条。

不建立边表、图遍历、LLM 实体知识图谱或全局 ontology。

## 8. 去重、冲突与版本链

### 8.1 去重

同 kind + subject + entity/topic 范围内比较：

- similarity >=0.92 且无新增时间/状态：忽略重复，更新 `last_confirmed_at` 和 confirmation_count。
- 0.78 <= similarity <0.92 且内容兼容：写新版本或合并为更完整表述，`meta.supersedes=old_id`。
- similarity <0.78：独立记忆。

相似度信号优先级：本地 embedding cosine > 归一化文本相似度。不能用全库 O(n²) 每轮比较；只比较同层、同 subject/entity 的最近候选集，默认最多 50 条。

### 8.2 冲突类型

| 类型 | 例子 | 处理 |
|---|---|---|
| replace | “以前喜欢咖啡，现在不喝了” | 新版本 active，旧版本 valid_to=新版本 valid_from |
| correction | “不是周三，是周四” | 必须新版本，confidence 提升，记录 correction source |
| temporal | “去年住杭州，现在住上海” | 两条均有效于不同时间，不互删 |
| preference drift | “最近不太喜欢长回复” | 新偏好覆盖当前窗口，旧偏好保留历史 |
| ambiguous | “可能吧，我也不确定” | needs_confirmation=true，不进入高置信 brief |
| subject conflict | “我喜欢猫”与“你喜欢猫” | subject 不同，不冲突 |

当前真值与历史真值分离：

```text
recall(current=true) → 版本链最新 active 且 valid_from/to 命中当前时间
recall(as_of=t)      → t 时刻有效版本
history(chain_id)    → 完整版本链，仅审计/解释使用
```

### 8.3 承诺状态

commitment 状态限定：`open|done|cancelled|expired`。

- open 不自动衰减、不被 curator 合并删除。
- done 写新版本并保留完成结果。
- cancelled 必须来自用户或确定性任务取消事件。
- expired 只用于有明确截止时间且已过期的承诺。
- 查询未完成承诺时必须按版本链取最新状态，不能让旧 open 版本重新出现。

## 9. 历史压缩与会话记忆

借鉴 AIRI：摘要是结构化历史的一部分，不是假装成用户或 assistant 的普通消息。

```python
@dataclass
class HistorySummary:
    summary: str
    from_message_id: int
    to_message_id: int
    source_count: int
    created_at: str
    facts: list[str]
    open_threads: list[str]
```

规则：

1. 保留最近完整 user/assistant 对，默认 20 轮。
2. 更旧历史按窗口压缩；摘要与原始 messages 并存，不删除证据。
3. 摘要只记录已出现的信息、未完成话题和关系变化，不生成新事实。
4. 摘要中的 durable fact 仍须经过候选校验才能晋升长期记忆。
5. 压缩失败时退回截断最近历史，不阻断对话。
6. 相同消息区间只能有一个 active 摘要；重新摘要写新版本。

第一版不新增 summary 表时，可使用 `session` layer + `meta.kind=history_summary`，但必须带 source id 范围和 TTL/版本。

## 10. 召回流水线

### 10.1 查询规划

每轮只做一次程序化查询计划，不做 agentic memory loop：

```python
@dataclass(frozen=True)
class MemoryQuery:
    text: str
    kinds: tuple[str, ...]
    subjects: tuple[str, ...]
    entities: tuple[str, ...]
    temporal_intent: str  # current/past/future/as_of/none
    as_of: str | None
    top_k: int
```

规则例子：

- “我现在住哪” → user_fact + current。
- “去年我们聊过什么” → shared_episode + past + 时间范围。
- “你答应我的事” → commitment + current/open。
- 日常闲聊 → semantic/episodic，top_k=5。

### 10.2 候选生成

并行收集：

1. semantic cosine：memory_embedding blob + Python 点积（向量由 embedding provider 生成）。
2. lexical：FTS5；目标应迁移为直接索引规范记忆，而不是仅通过 messages provenance 反查。
3. entity：`meta.entities` exact/alias match。
4. temporal：event_time/valid_from/deadline 与查询意图匹配。
5. procedural：open commitment 和明确用户边界可直接加入候选。

单路故障时用剩余信号归一化，不随机补分。embedding 不可用时 FTS/entity/temporal 仍可工作。

### 10.3 排序

默认融合：

```text
0.35 * semantic_similarity
+ 0.20 * lexical_relevance
+ 0.15 * entity_match
+ 0.15 * temporal_match
+ 0.05 * freshness
+ 0.05 * importance
+ 0.05 * confidence
```

当前实现的 0.45 semantic + 0.20 FTS + 0.15 freshness + 0.10 importance + 0.10 confidence 作为迁移起点；entity/temporal 信号接入后再切换新权重。

硬过滤先于排序：

- 已 superseded 的非当前版本。
- expires_at 已过期。
- 当前查询时间不在 valid_from/to。
- confidence <0.35 且用户未明确追问历史不确定内容。
- sensitive 或 scope 不允许的条目。
- session 过期条目。

可选 reranker 暂缓。只有离线 benchmark 证明 top-k 排序质量不足时，才在 top-20 上增加本地小 reranker；不为默认链路增加一次 LLM 调用。

### 10.4 Context Brief

召回结果不能原样全塞进 prompt。统一构造：

```python
@dataclass(frozen=True)
class MemoryBriefItem:
    memory_id: int
    kind: str
    text: str
    confidence_label: str
    temporal_label: str
    source_label: str
    score: float
    token_budget: int
    sensitivity: str
```

注入顺序：

1. 角色/系统边界（不是数据库重复角色卡）。
2. open commitment 与用户明确边界。
3. 与当前输入相关的 user_fact/shared_episode。
4. history summary/open threads。
5. 风格偏好块。

默认总预算 5600 字符，分配：

| 内容 | 默认预算 |
|---|---:|
| core_profile/用户边界 | 1200 |
| procedural/open commitment | 1000 |
| semantic | 1400 |
| episodic | 1400 |
| session/history summary | 600 |

超预算时按完整 item 删除尾部，不在句子中间硬截断。每条 brief 保留 memory id 到日志，但 prompt 不暴露内部 id。

人格循环实现 P-4 后，Context Brief 上方增加独立 `PersonaBrief`：相关用户框架、角色观点、共同意义、关系上下文、当前内在状态和未解决张力。Persona Brief 每类默认最多 2 条，总计最多 6 条；与 Memory Brief 共享总上下文预算。它负责“如何理解和回应”，Memory Brief 负责“有哪些证据和事实”。

## 11. 记忆表达与不确定性

模型不得把检索分数当事实置信度。最终表达由 confidence、strength、时间有效性和用户是否追问共同决定：

| 条件 | 表达 |
|---|---|
| confidence>=0.85 且 current valid | 自然调用，不必每次说“我记得” |
| 0.60-0.85 | “我印象里……”；关键事实可询问确认 |
| 0.35-0.60 | 只在直接相关时使用，明确不确定 |
| <0.35 | 默认不注入；用户追问时说明记忆模糊 |
| temporal conflict | 明确区分“以前/现在” |
| expired session | 不使用 |

禁止随机张冠李戴和随机模糊化。记错只能来自低置信旧证据或真实冲突，并且必须可被用户纠正和追溯。

## 12. 遗忘、强化与整理

### 12.1 强度衰减

保留 `R=e^(-t/S)`，但按类型应用：

- identity：不自动衰减。
- open commitment：不衰减。
- user_fact：importance 和 confirmation_count 决定稳定度。
- shared_episode：随时间衰减；重要共同事件下限 0.35。
- session：不用强度曲线，按 expires_at 硬过期。
- 已 superseded 版本不参与普通召回，但保留审计。

访问不应自动把错误记忆永久强化。只有用户确认、重复独立证据或承诺兑现才增加 confirmation_count/strength。

### 12.2 Curator

curator 是确定性整理器，不是每 8 轮对全库做 O(n²) LLM 整理：

1. 清理过期 session。
2. 标记低置信孤立候选，不直接删除用户可审计证据。
3. 在同 kind/subject/entity 小集合内去重。
4. 维护版本链 current 状态。
5. 检查 open commitment 到期状态。
6. 重建缺失向量。
7. 输出 created/versioned/expired/ignored/conflict 统计。

LLM curator 仅用于将一组同主题碎片压成自然摘要，且：默认关闭、单次 <=20 条、输出仍走候选校验、不得删除原始证据、单次最多 50 操作。

当前 `curate()` 中“confidence<0.55 直接 erase”和“0.78 后连接字符串并删除双方”不符合本规范，应在实现阶段迁移为标记/版本链，不能直接丢失证据。

## 13. 用户文风学习

### 13.1 定位

文风学习调整的是“她如何靠近用户”，不是“她变成用户”。目标：在不破坏角色恒定性的前提下，适应用户对长度、节奏、正式度、直接度和标点习惯的稳定偏好。

### 13.2 数据源

在线画像只观察 user 角色的自然消息；显式授权的离线语料走 `STYLE_LEARNING_SPEC.md` 的独立 corpus 管线。两者分开持久化为 `profile` 与 `corpus_profile`，不互相覆盖。排除：

- 命令、代码块、日志、粘贴文档、URL、引用他人原文。
- 长度 <4 的消息。
- 单次激烈情绪、辱骂、敏感内容。
- 图片 OCR、语音转录置信度过低文本。
- QQ 系统消息、工具输出。

每条合格样本只更新聚合统计，不保存完整句子到 style profile。

### 13.3 特征

第一版纯规则，不调用 LLM：

| 维度 | 观测 |
|---|---|
| 长度 | 平均消息/句子字符、长消息比例 |
| 节奏 | 单句/多句、换行比例、连续短句比例 |
| 标点 | 问号、感叹号、省略号、括号、顿号密度 |
| 语域 | “请/麻烦/是否”与口语词比例，形成 formality |
| 直接度 | 命令式、结论先行、缓冲语比例 |
| 展开偏好 | 问题复杂度与用户对长/短回复的显式反馈 |
| 混合语言 | ASCII、日文、emoji 比例 |
| 话题跟随 | 用户是否延续上一回复主题 |

不用 jieba/LLM 分词。内容词镜像降级为可选低权重特征；默认不直接注入随机高频词。

### 13.4 稳定性与更新

- 至少 20 条合格样本后才形成 `confidence>0.3` 的画像。
- 使用 EMA，默认 alpha=0.05；单轮任何维度变化 <=0.02。
- 在线画像用 EMA 缓慢更新；离线 corpus 仅在显式重新导入/启用时重算。
- 用户明确说“回复短一点/别太正式”时，写 procedural 明确偏好，其优先级高于统计画像。
- 画像变化需连续多个样本支持；单条消息不改变风格。
- `/reset --style` 清空统计、镜像和显式 style override，但不删其他记忆。

### 13.5 融合优先级

```text
系统边界
> CharacterCard 核心人格与沟通风格
> 通道规则（IM/TTS）
> 用户明确风格偏好（procedural）
> 稳定 UserStyleProfile
> 当前场景/情绪
> 单轮轻量镜像
```

融合限制：

- 画像只能调节输出动作，不能改变角色价值观、立场、背景和事实。
- 角色卡声明“寡言”时，用户长文风最多把回复从短提升到中等，不变成报告腔。
- TTS 不学习用户的文字标点和 emoji；只学习节奏、句长和正式度。
- IM 可适度匹配换行、括号和标点密度，但不得逐字复用用户句式。
- 高频词每个会话最多镜像 1 个、全局最多使用 3 次；默认关闭随机采样，改为仅在语义自然时由模型参考。

### 13.6 Prompt 契约

注入的是稳定画像摘要，不是数值表：

```text
【表达适配】用户交流偏好：
- 通常用中短句，结论先行，不喜欢铺垫过长。
- 日常语气偏随意，技术问题接受较详细说明。
- 常用括号补充，但很少使用 emoji。
请在保持角色自身说话方式的前提下适度适应，不要模仿口癖或复述用户句子。
```

仅当 confidence>=0.3 注入；最多 300 字符。日志保留数值快照用于调试。

### 13.7 与现有实现的迁移

保留：

- `StyleParams(reply_length/formality/humor/topic_follow)`。
- `StyleLearner` 的 EMA、持久化和 reset。
- 显式反馈词和 correction 信号。

修改：

- `LanguageMirror.observe()` 不再把所有中文 2-4 字重叠片段作为高频词；先过滤停用词、重复子串、敏感词和内容实体。
- 新增 `UserStyleProfile.observe(user_text, metadata)`，由纯规则统计稳定特征。
- `StyleLearner` 保留在线 profile，并新增显式启用的离线 `corpus_profile`；feedback 与两类画像分别记录。
- `StyleBrief` 只包含聚合表达控制，按 IM/TTS 分流，并给 `ResponsePlan.desired_length` 提供低优先级默认值。
- `style.json` schema_version=3；v1/v2 缺字段时用默认值迁移。
- `style_corpus.py` 负责 provenance、脱敏片段、弱标注、代表抽样、复核门禁和 corpus 级删除；这些数据不进入 `MemoryStore`。

不做：用户文风微调、LoRA、PPO/RLHF、情感依赖优化、逐用户多租户模型。

## 14. 隐私、删除与审计

### 14.1 敏感分类

`sensitivity`: `public|personal|private|secret`。

- secret 永不写长期记忆：密码、token、验证码、私钥、支付凭据。
- private 默认可存本地证据，但不进入主动消息和视觉联想；用户可配置完全不存。
- personal 可召回，但跨通道注入前检查用途。
- style profile 不记录敏感词和完整原文。

### 14.2 删除

用户说“忘掉 X”时：

1. 检索候选并展示将删除的范围；模糊匹配不得直接批量删除。
2. 用户确认后删除规范记忆、版本链、向量和相关派生摘要。
3. 原始 messages 是否删除单独询问；默认保留聊天历史时必须说明长期记忆已删但聊天原文仍在。
4. 删除 style profile 使用独立 reset，不影响事实记忆。
5. 删除结果记录本地审计事件，不保留被删除内容本身。

当前 `erase()` 只删除 memories/vector，且注释声称关联消息级联但代码没有删除 messages。实现阶段必须修正文档或补齐显式删除策略，不能继续声称已级联。

## 15. 接口边界

保留五个核心原语：

```python
store(candidate_or_entry) -> MemoryEntry
recall(MemoryQuery) -> list[ScoredMemory]
decay(now) -> DecayReport
curate(now) -> CurateReport
erase(DeleteRequest) -> DeleteReport
```

允许的辅助接口：

```python
store_message(...)
build_brief(query, budget) -> MemoryBrief
update_latest(...)
history(chain_id)
export(format="jsonl|markdown")
```

调用方禁止依赖 SQLite 表结构。`Agent`、QQ、桌宠和未来 adapter 只消费 MemoryBrief/MemoryEntry DTO。

## 16. 配置

```yaml
memory:
  db_path: data/veranima.db
  embedding_model: local:data/models/bge-m3
  candidate_extraction: rules
  llm_extraction_enabled: false
  recall_top_k: 5
  recall_candidate_k: 20
  recall_threshold: 0.30
  max_injected_chars: 5600
  core_profile_budget: 1200
  procedural_budget: 1000
  semantic_budget: 1400
  episodic_budget: 1400
  session_budget: 600
  decay_enabled: true
  decay_interval_minutes: 60
  importance_base_s: 2592000
  session_ttl_minutes: 120
  curator_every_turns: 8
  curator_max_ops: 50
  min_candidate_confidence: 0.55
  private_memory_enabled: true

style_learning:
  enabled: true
  min_samples: 20
  active_window: 100
  ema_alpha: 0.05
  max_step_delta: 0.02
  prompt_budget_chars: 300
  lexical_mirroring: false
```

不把未使用配置写进示例文件。上述键按实现切片逐步加入，代码未消费前不落配置。

## 17. 实现顺序

每片只改最少文件并留行为测试：

1. M-1 数据真值：MemoryEntry 元数据、版本 current 过滤、session TTL、删除语义纠正。
2. M-2 写入：候选 schema、规则提取口语变体、显式纠正、commitment 状态。
3. M-3 召回：memory FTS 索引、entity/temporal 信号、ScoredMemory、硬过滤。
4. M-4 Context Brief：统一预算、完整 item 截断、来源/置信度、历史摘要。
5. M-5 Curator：过期、版本维护、低置信标记、向量修复；删除有损 merge。
6. M-6 文风学习：UserStyleProfile、样本过滤、融合、迁移和 reset。
7. M-7 用户控制：查看、纠正、忘记、导出、style reset。
8. M-8 benchmark：离线事实/时序/冲突/承诺/文风回归集。

M-1~M-8 完成后，人格循环按 `PERSONA_LOOP_SPEC.md` 的 P-0~P-9 实现；不得把 PersonaCandidate 绕过现有 validate/version/delete 原语直接写入 prompt。

在 M-1 至 M-4 完成前，不实现 LLM curator、reranker、entity graph 或多角色 memory namespace。

## 18. 测试与验收

### 18.1 单元/集成

必须覆盖：

- 原始消息即时写入，候选失败不阻断回复。
- 偏好口语变体写入；问句不误记承诺。
- >=0.92 去重；0.78-0.92 版本链；明确纠正强制新版本。
- current/as_of 返回正确版本。
- open commitment 不衰减；done 后不重新显示为 open。
- session TTL 到期不可召回。
- embedding 故障时 lexical/entity/temporal 降级可用。
- memory FTS 直接检索规范记忆，不依赖消息巧合命中。
- prompt 总预算和各层预算生效，不截断半条记忆。
- secret 候选拒绝；删除后向量和派生摘要不可召回。
- 空库回答不编造；素材不足时不调用总结 LLM。
- 换角色继承用户事实但不继承旧角色说话风格。
- 视觉 observation 不进入长期记忆。

文风学习必须覆盖：

- 代码/日志/引用文本不计样本。
- 20 条前不注入稳定画像。
- 单轮异常文风不引发突变。
- 显式“短一点”高于统计长文风。
- 用户长文风不能突破角色卡寡言边界。
- TTS 不注入 emoji/文字标点偏好。
- reset 后回到默认，事实记忆不受影响。
- 不保存原始样本句子和敏感词。

### 18.2 离线评测集

项目内维护小型可读 JSONL，不依赖真实 API：

- 事实：偏好、身份、环境。
- 时序：以前/现在/未来计划。
- 冲突：纠正、偏好漂移、同名实体。
- 多跳：共同经历 + 后续结果。
- 承诺：open/done/cancelled/expired。
- 无答案：数据库没有证据时必须拒绝编造。
- 文风：同内容在不同稳定画像下只改变表达动作，不改变事实和角色立场。
- 人格循环：私人定义形成、比喻迁移、角色保留分歧、共同意义、关系修复、诱导回声、公式化升华拦截、证据删除与换卡隔离。

指标：Recall@5、current-version accuracy、temporal accuracy、conflict accuracy、no-answer precision、brief 字符数、P95 本地召回耗时、style drift 上限。

### 18.3 体验验收

1. 跨重启询问明确偏好，回答有真实召回证据。
2. 用户纠正事实后，当前回答使用新版本；询问过去时能解释旧版本。
3. 未完成承诺在相关时机出现，完成后不再催促。
4. 一个月后低价值事件变模糊，但重要共同事件仍可被相关话题唤醒。
5. 用户删除记忆后，普通召回、主动消息和月度回顾都不再出现。
6. 经过足够样本后，回复节奏贴近用户偏好，但仍明显是同一个角色。
7. 切换 IM/TTS 时风格画像按通道适配，不出现文字口癖念进语音。

## 19. 暂缓与触发条件

| 暂缓 | 触发条件 |
|---|---|
| LLM candidate extraction 默认开启 | 规则召回覆盖率评测不足，且远程调用成本可接受 |
| Cross-encoder reranker | top-20 含答案但 top-5 经常排序错误 |
| 图数据库/entity graph | 多跳实体问题成为主要失败类型且 SQLite entity boost 不够 |
| Mem0/Memanto provider | 用户明确需要跨应用共享或现有 SQLite benchmark 显著落后 |
| 多角色独立 namespace | 产品决定不同角色不共享用户事实/关系状态 |
| 文风 LoRA/微调 | 参数层学习经长期评测明确无法满足，且有独立训练数据与显存预算 |
| 云端记忆服务 | 用户主动接受隐私、成本和供应商锁定 |

先建立“证据可追溯、当前真值正确、不会乱记、可以忘记”的记忆，再追求 benchmark 排名。