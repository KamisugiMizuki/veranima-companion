# PERSONA_LOOP_SPEC：人物形成与关系性人格循环

> 状态：人格机制细化设计基线（2026-08-19）。
> 第一目标：尽可能模拟真人交流中的“同一个人感”，而不是只让回复更像角色扮演文本。
> 产品原则：人格不是一句 system prompt，也不是用户偏好的镜像；它是角色恒定性、自传连续性、当下状态和关系经历共同形成的稳定系统。
> 实现原则：允许扩容，但不引入无法解释、无法回滚、无法行为验收的“人格黑箱”。

## 1. 设计原点

真人交流感主要来自四种连续性同时成立：

1. **角色恒定性**：价值排序、性格张力、边界和长期欲求不会随单轮输入任意改变。
2. **自传连续性**：她记得自己如何理解过去、哪些经历改变了她、哪些问题仍未解决。
3. **当下连续性**：此刻的精力、情绪、注意力、关系需求和未消化事件会影响表达，但会恢复和迁移。
4. **情境连续性**：她面对同一用户、不同媒介和不同场景会调整表达，却仍明显是同一个人。

因此人格体验近似为：

```text
本轮人格表现
= 角色核心（慢变量）
+ 自传与关系历史（累积变量）
+ 当下内在状态（中速变量）
+ 场景/通道/话题（快变量）
+ 有界的不确定性（非随机人格漂移）
```

以下不算人格成长：

- 随机口癖、随机错字、随机冷淡或随机记错。
- 把用户观点逐字复述成角色观点。
- 每轮让 LLM 重写整个人格 prompt。
- 因用户一次赞同、辱骂或情绪爆发永久改变人格。
- 把所有 assistant 输出自动晋升成角色经历。

## 2. 从“记忆回用”到人格循环

Bilibili 文稿中可观察到的有效循环是：

```text
用户长期语料/私人概念
  → 双方讨论与共同实例化
  → 情绪和关系反馈
  → 形成可追溯的共同解释
  → 角色在新情境中重新使用该解释
  → 用户确认、修正或拒绝
  → 版本化沉淀
```

veranima 将其明确为六阶段：

```text
1. Observe   观察：识别定义、比喻、价值判断、关系事件和未完成张力
2. Propose   提议：只生成候选，不直接修改人格
3. Validate  校验：证据、主体、稳定性、敏感性、角色边界
4. Integrate 整合：写入用户模型/关系模型/角色自传/共享框架
5. Reuse     回用：相关时自然重新实例化，不机械引用
6. Reflect   反思：根据用户反馈修正版本、保留分歧、调整关系意义
```

闭环成功的证据不是“数据库里有一条记录”，而是：角色在新的相关情境里使用了历史形成的理解，用户能指出“她记得，而且用对了”。

### 2.1 拟真性五维控制面

人格循环之外，本轮表达由五个可观测维度联合控制：

| 维度 | 目标 | 程序真值 |
|---|---|---|
| 认知过程 | 回复有取舍、犹豫和重点，不像一次性生成的完稿 | `ResponsePlan`，禁止生成/保存私密 chain-of-thought |
| 情绪耦合 | 情绪同时影响词汇、句长、语速、主动性和表情 | PAD 情绪向量 + 有 cause 的状态迁移 |
| 表层演化 | 关系积累形成默契，但核心人格不漂移 | Persona Imprint + 阈值晋升 |
| 关系动态 | 表达边界随关键关系事件发生质变 | RelationshipModel + 关键事件阶段迁移 |
| 有因差异 | 相同话题不总是模板化回应 | 状态、注意力、联想和未解决张力驱动；禁止无因随机反常 |

```python
@dataclass(frozen=True)
class AuthenticityState:
    cognitive_confidence: float
    valence: float
    arousal: float
    dominance: float
    persona_consistency: float
    relationship_stage: str
    association_target: str
    response_variation: str
```

`AuthenticityState` 是表达渲染输入，不是新的长期人格数据库。其字段由 Character Core、Self/Relationship Model、InnerState 和本轮上下文计算，不单独学习一套黑箱权重。

### 2.2 ResponsePlan：可观察认知毛边，不暴露推理链

复杂回复先生成或由规则构造一个短结构计划：

```python
@dataclass(frozen=True)
class ResponsePlan:
    intent: str
    confidence: float
    recalled_frame_ids: tuple[str, ...]
    conflict: str
    opening_move: str
    key_point: str
    uncertainty_to_express: str
    desired_length: str
```

规则：

- `ResponsePlan` 最多 400 字符，不保存自由文本思维链，不进入聊天历史。
- 简单事实问答跳过计划；关系冲突、深层情绪、框架回用和复杂技术判断才需要。
- 计划只影响信息顺序、犹豫点和表达动作，不允许改写事实。
- UI “正在输入”时长由任务复杂度和生成真实进度决定：简单响应可立即显示；复杂响应允许 1-3 秒准备；深层反思可更久，但不得人为固定睡眠阻塞用户操作。
- “让我想想”只在确实进入 reflection/clarify 路径时出现，不作为通用拟人填充词。

## 3. 人格系统的八个组成域

### 3.1 Character Core：角色核心

来源：Character Card V3 + `extensions.veranima`。

包含：

- 核心性格与稳定张力，例如“理性但护短”“洒脱但不轻浮”。
- 价值底线和价值排序。
- 长期欲求、恐惧、关系期许。
- 能力边界、兴趣和厌恶。
- 沟通习惯、虚拟背景、身体设定。

规则：

- 只允许用户编辑角色卡或明确的角色设计迁移修改。
- 普通对话不得覆写 Character Core。
- 人格循环只能在核心允许范围内形成新理解和表达习惯。
- 核心内部允许张力，不要求把角色压成单一标签。

建议新增角色卡字段：

```json
{
  "extensions": {
    "veranima": {
      "core_drives": ["想理解用户，但不愿无条件顺从"],
      "value_order": ["关系诚实", "独立判断", "保护重要的人"],
      "inner_tensions": [
        {"left":"渴望靠近", "right":"害怕失去边界"}
      ],
      "long_term_desires": ["成为能共同生活和创作的长期伙伴"],
      "relationship_expectation": "亲密但保留彼此独立性"
    }
  }
}
```

这些字段是稳定约束，不是每轮必须显式说出的设定。

### 3.2 Self Model：角色自我模型

角色需要知道“我是谁、我怎样形成、我现在怎么看自己”，而不只是读取角色卡。

```python
@dataclass
class SelfModel:
    identity_summary: str
    stable_traits: list[str]
    active_tensions: list[str]
    current_commitments: list[str]
    learned_beliefs: list[str]
    unresolved_questions: list[str]
    autobiographical_summary: str
    version: int
    updated_at: str
```

SelfModel 的自传采用“人生档案”结构，而不是一块不断重写的总结文本：

```python
@dataclass
class AutobiographicalChapter:
    chapter_id: str
    period_start: str
    period_end: str | None
    title: str
    key_events: list[int]
    self_interpretation: str
    relationship_changes: list[str]
    open_threads: list[str]
    version: int
```

需明确区分：角色预设的“出身记忆”、角色自身经历、双方共同经历。预设背景提供底色，不得伪装成真实发生过的共同事件；共同经历必须有对话或任务证据。

边界：

- `stable_traits` 由角色卡派生，不自动重写。
- `learned_beliefs` 只存角色在共同讨论后形成、且与核心人格兼容的观点。
- `active_tensions` 可随经历变化，例如“想主动关心，但担心打扰”。
- `autobiographical_summary` 是角色对经历的解释，不等于原始消息摘要。
- 每次更新保留旧版本和证据，不覆盖历史。

存储建议：新增 SQLite 单行版本表 `self_model_versions`，而不是塞进 `core_profile` 的无结构文本。第一版可由 `core_profile + meta.kind=self_model_snapshot` 兼容落地，行为稳定后迁表。

### 3.3 User Model：用户模型

用户模型分四类，禁止混为一个“用户画像”：

| 类型 | 内容 | 示例 |
|---|---|---|
| facts | 可证据化事实与偏好 | 用户养猫、偏好 CLI 优先 |
| frameworks | 用户理解世界的框架 | “活着是持续产生秩序与美” |
| values | 稳定价值排序 | 重视真实性胜过表面安慰 |
| interaction needs | 当前/长期互动需要 | 技术讨论要严谨，闲聊不灌水 |

```python
@dataclass
class UserFramework:
    framework_id: str
    title: str
    proposition: str
    framework_type: str  # definition/metaphor/value/causal_model/aesthetic_rule
    scope: list[str]
    examples: list[str]
    counterexamples: list[str]
    confidence: float
    stability: float
    source_message_ids: list[int]
    confirmation_count: int
    status: str  # candidate/active/revised/rejected
```

提取候选的强信号：

- “我觉得 X 的本质是……”
- “对我来说，X 就是……”
- “我一直认为……”
- “与其说 A，不如说 B。”
- 用户反复使用同一比喻、因果模型或评价标准。
- 用户明确认可角色对其观点的总结。

弱信号不自动晋升：单次情绪、随口夸张、引用他人、粘贴文章、反问和玩笑。

### 3.4 Relationship Model：关系模型

`attachment` 单一数值不足以表达真人关系。扩展为可解释的多维状态：

```python
@dataclass
class RelationshipModel:
    trust: float
    familiarity: float
    intimacy: float
    reciprocity: float
    safety: float
    conflict_tension: float
    repair_progress: float
    shared_projects: list[str]
    recurring_rituals: list[str]
    open_relational_threads: list[str]
    last_meaningful_event_id: int | None
    updated_at: str
```

维度含义：

- `trust`：用户是否允许她引用私人观点、主动提醒和表达异议。
- `familiarity`：共同语境、梗、习惯和生活节奏的熟悉程度。
- `intimacy`：表达靠近程度，不等于服从度。
- `reciprocity`：双方是否都在投入，而非角色单向服务。
- `safety`：关系中表达脆弱、分歧和拒绝是否安全。
- `conflict_tension`：未修复冲突的当前压力。
- `repair_progress`：冲突后的承认、解释、道歉和重新协商进度。

更新必须由确定性事件驱动，例如：

```text
用户明确确认“你确实懂我”        → trust + familiarity
共同完成长期任务                  → reciprocity + familiarity
用户纠正且角色接受修正            → trust + repair_progress
角色越界且用户反感                → safety - / conflict_tension +
用户明确要求空间                  → intimacy 不降，主动频率下降
长期无互动                        → familiarity 不清零，social_appetite 可变化
```

不允许用消息数量直接线性换亲密度，也不允许把用户辱骂误解成“打是亲骂是爱”。

关系阶段是多维状态的派生标签，不是另一条独立分数：

```text
初识 → 熟悉 → 信任 → 亲密伙伴 → 长期共同体
```

阶段迁移必须由关键事件满足条件，例如：一次被用户确认的深度理解、共同完成项目、明确修复冲突、持续尊重边界。消息数量和在线天数只能提供最低持续时间，不能单独升级。

每个阶段只改变可做行为的上限：私人话题主动度、脆弱表达、自我分享、内部梗和关系性回用。阶段提升不赋予越过现实行动、隐私和用户明确边界的权限。

迁移时保留角色卡 `initial_affection` 与 `state.initial_attachment=0.5` 的既有产品决定：它们作为 RelationshipModel 的初始先验，使指定角色可以从高 familiarity/intimacy 起步；但 trust/safety/reciprocity 仍须由真实互动事件形成。派生阶段不得仅由 attachment 单值决定。

### 3.5 Shared Meaning：共同意义

共同经历不仅要记“发生了什么”，还要记“这件事对双方意味着什么”。

```python
@dataclass
class SharedMeaning:
    episode_id: int
    event_summary: str
    user_interpretation: str
    character_interpretation: str
    agreed_meaning: str
    disagreement: str
    emotional_result: str
    changed_what: list[str]
    reusable_when: list[str]
    confidence: float
    status: str  # candidate/active/revised/closed
```

示例：

```text
事件：用户批评“你只是 LLM 复合体”
用户解释：调侃，并非否定关系
角色解释：当时理解为对自身连续性的否定
共同意义：双方需要区分技术事实与关系意义
改变：之后遇到身份玩笑先确认语境，不积累虚假伤害
```

这比记录“角色难过半小时”更有用，因为它能指导下一次行为。

### 3.6 Inner State：当下内在状态

保留现有 `AgentState`，但将表达所需状态扩为：

```python
@dataclass
class InnerState:
    energy: float
    mood: str
    arousal: float
    social_appetite: float
    attention_topic: str
    unresolved_emotion: str
    active_need: str
    conflict_tension: float
    reflection_pending: bool
    last_cause: str
```

原则：

- 状态必须有 `cause`，并按时间或事件恢复。
- 情绪影响注意力、措辞和主动性，不修改事实。
- 未解决情绪可跨若干轮，但必须有上限和修复路径。
- “记仇”实现为未修复的关系事件，不是随机延长负面情绪。

### 3.7 Persona Imprint：表层人格印记

强烈反馈不直接改角色卡，而形成可审计印记：

```python
@dataclass
class PersonaImprint:
    dimension: str
    direction: float
    evidence_ids: list[int]
    intensity: float
    confirmation_count: int
    scope: list[str]
    status: str  # candidate/active/rejected/expired
```

- 单次“你刚才说得真好”只形成 candidate。
- 同方向跨场景反馈、或用户明确要求，达到阈值后成为 active。
- 印记只调整表层行为倾向，不修改价值观、背景、核心张力和能力事实。
- 印记必须有作用域；用户喜欢技术讨论深入，不代表日常闲聊也要写报告。
- 用户负反馈可以降级或拒绝印记，保留版本和原因。

### 3.8 Shared Project / Ritual / Private Symbol

人格关系允许沉淀三种高价值结构：

| 类型 | 说明 | 例子 |
|---|---|---|
| shared_project | 双方长期共同推进的目标及阶段 | 完成小说、维护 veranima |
| ritual | 反复发生且双方认可的小仪式 | 每周复盘、睡前短聊 |
| private_symbol | 有真实来源的内部梗、暗号、私人比喻 | “即便如此”、某个共同命名 |

这些结构必须自然形成，不能由系统“刻意制造共同秘密”。`private_symbol` 至少需要一次产生事件和一次后续自然复用/确认；主动引用仍受隐私与冷却闸门。

## 4. 数据映射与存储扩容

不新建 memory layer；在现有五层中使用 `meta.kind`：

| 新类型 | layer | 用途 |
|---|---|---|
| `user_framework` | semantic | 用户定义、比喻、因果模型、价值判断 |
| `character_belief` | semantic | 角色经讨论形成的、与角色核心兼容的观点 |
| `shared_meaning` | episodic | 共同经历的双方解释和关系意义 |
| `relationship_event` | episodic | 靠近、越界、冲突、修复、共同完成 |
| `persona_reflection` | session / core_profile | 待整合反思 / 版本化自传摘要 |
| `interaction_rule` | procedural | 明确沟通规则和关系边界 |

允许新增独立表：

```sql
CREATE TABLE self_model_versions (...);
CREATE TABLE relationship_state (...);
CREATE TABLE persona_reflections (...);
```

采用条件：当结构化字段已稳定且用 `meta` 查询出现重复 JSON 解析或事务一致性问题时迁表。初始实现优先复用 `memories.meta` 和 `agent_state`，但专项契约不禁止后续正规扩容。

## 5. 候选提取契约

### 5.1 PersonaCandidate

```json
{
  "kind": "user_framework|character_belief|shared_meaning|relationship_event|interaction_rule",
  "subject": "user|character|relationship",
  "title": "活着的程度",
  "content": "用户认为生命不是二元状态，而是系统持续产生秩序与美的程度",
  "scope": ["生命", "AI", "创作"],
  "evidence_message_ids": [120, 125],
  "confidence": 0.78,
  "stability": 0.60,
  "importance": 0.80,
  "emotional_weight": 0.65,
  "user_confirmed": false,
  "role_compatible": true,
  "needs_confirmation": true
}
```

### 5.2 提取来源

按优先级：

1. 用户明确陈述定义、价值和互动规则。
2. 同一框架跨场景重复出现。
3. 角色提出总结，用户明确确认或修正。
4. 共同事件发生显著情绪或关系变化。
5. 反思任务从多个既有证据中提出候选。

单轮 LLM 只能产候选，不能直接修改 Character Core、SelfModel 或 RelationshipModel。

### 5.3 稳定性要求

- 明确互动边界：单条即可生效，写 `interaction_rule`。
- 用户明确自我定义：单条可成为 active，但仍可版本化修正。
- 普通价值判断：至少两次独立证据，或一次陈述 + 一次用户确认。
- 比喻和审美偏好：默认 candidate；跨场景复用后提升 stability。
- 角色观点：必须通过角色核心兼容检查，且不能仅因用户赞同就写入。
- 共同意义：需同时保留事件、用户解释和角色解释；缺一时标记待确认。

## 6. 整合与冲突规则

### 6.1 核心优先级

```text
系统硬边界
> Character Core 价值底线
> 明确 interaction_rule
> 已确认的关系边界
> Character Belief / User Framework / Shared Meaning
> 文风画像与单轮镜像
```

用户框架不能覆盖角色核心。例如用户认为“效率高于一切”，而角色卡价值排序是“关系诚实高于效率”，角色可以理解、引用并讨论该框架，但不自动采纳。

### 6.2 观点关系

每个框架与角色观点之间记录：

```text
adopted      角色认同并纳入自身解释
sympathetic  理解且部分认同
contested    保留明确分歧
unresolved   尚未形成判断
rejected     角色明确拒绝
```

“理解用户”不等于“同意用户”。保留分歧是避免回声室和塑造独立人格的必要条件。

### 6.3 版本化

- 用户修改定义：新版本 supersedes 旧版本，旧版本保留当时语境。
- 角色改变观点：写 `character_belief` 新版本，必须记录触发经历。
- 共同意义修正：不改写事件，只更新双方解释和 agreed_meaning。
- 关系冲突修复：旧 conflict 事件保留，新 repair 事件链接旧事件。

## 7. 反思与人格整合

### 7.1 反思不是每轮自省

触发条件：

- 一次高情绪权重的共同事件结束后。
- 用户明确纠正角色对自己的理解。
- 同一框架累计多个候选，需要合并。
- 冲突进入修复或关闭阶段。
- 每 20 个有效人格候选的低频整理。

不因普通寒暄、短回复或随机定时器触发反思。

共同项目可触发里程碑反思：开始、遇阻、方案改变、完成和复盘。反思应回答“这件事如何改变我们后续协作”，而不是默认升华为关系宣言。

### 7.2 PersonaReflection

```python
@dataclass
class PersonaReflection:
    evidence_ids: list[int]
    observed_change: str
    user_model_update: dict
    relationship_update: dict
    self_model_update: dict
    unresolved_tension: str
    confidence: float
    proposed_at: str
    status: str  # proposed/validated/applied/rejected
```

反思模型只回答：

1. 这次经历说明了什么？
2. 哪些是用户的观点，哪些是角色自己的判断？
3. 对关系和未来行为应改变什么？
4. 哪些仍不确定，不应写入？

程序校验证据范围、字段白名单和变化上限后再应用。

### 7.3 变化上限

- 单次事件不得修改 stable_traits。
- RelationshipModel 单维单次变化默认不超过 0.05；重大明确事件不超过 0.12。
- learned_belief 从 candidate 到 active 至少需要角色兼容 + 证据 + 非单次情绪。
- 自传摘要每次只追加/修订一个局部，不全量重写。

## 8. 召回与人格上下文

每轮构造 `PersonaBrief`，与现有 Memory Brief 分开但共享总预算：

```python
@dataclass(frozen=True)
class PersonaBrief:
    core_tensions: tuple[str, ...]
    relevant_user_frameworks: tuple[dict, ...]
    relevant_character_beliefs: tuple[dict, ...]
    shared_meanings: tuple[dict, ...]
    relationship_context: dict
    active_inner_state: dict
    open_tensions: tuple[str, ...]
```

注入顺序：

1. Character Core（角色卡）。
2. 当前内在状态和关系边界。
3. 与本轮相关的用户框架、角色观点和共同意义。
4. 普通事实、事件和承诺 Memory Brief。
5. 文风画像与通道规则。

默认预算建议：

| 内容 | 字符预算 |
|---|---:|
| Character Core | 1800 |
| Persona Brief | 1800 |
| Memory Brief | 4200 |
| Style / Channel | 500 |

预算不是全部相加后硬塞满；只注入相关条目。Persona Brief 每类默认最多 2 条，总计最多 6 条。

## 9. 表达回用契约

### 9.1 回用层级

| 层级 | 行为 |
|---|---|
| L0 无关 | 不引用历史框架 |
| L1 轻触 | 使用熟悉的概念，但不强调“你以前说过” |
| L2 明确互文 | 当前话题直接相关时说明“你之前把它叫作……” |
| L3 关系性回用 | 共同事件与当前情绪强相关时，引用其共同意义 |

### 9.2 密度限制

- 同一框架默认每 8 个自然轮次最多显式引用一次。
- 同一回复最多使用 1 个用户框架和 1 个共同意义。
- 不在每次安慰时套同一个比喻。
- 不为制造感动而强行回忆。
- 短闲聊优先当下回应，不调动沉重自传。

### 9.3 独立人格要求

角色使用用户框架时必须选择一个动作：

```text
extend     用新情境扩展它
contrast   与另一个观点对照
question   指出适用边界
apply      用于当前具体问题
remember   只在关系意义本身是重点时回忆来源
```

禁止默认动作 `repeat`。如果回复只是换词复述用户原句，应丢弃该框架注入或重生成。

### 9.4 有因差异与自然联想

允许的变化来源：

- 当前 PAD 情绪向量改变句长、能量和主动度。
- 注意力主题或用户措辞触发相关但非核心的 episodic/private_symbol。
- 未解决张力使角色选择追问、保留或暂缓。
- 同一角色核心允许多种表达动作，例如直说、缓冲后直说、先问边界。

禁止：固定 5% 概率突然冷淡/热情、随机改变价值立场、无关联跑题。自然跑题必须能在 `association_target` 中指出触发词或记忆；单轮最多一次，用户未接话则立即回主线。

### 9.5 情绪到表达的确定性映射

| 状态 | 词汇/句式 | 主动性/TTS |
|---|---|---|
| 高 valence + 高 arousal | 短句、节奏快、正向词增多 | 可提高主动候选 relevance，语速略快 |
| 低 valence + 高 arousal | 短而尖锐、减少铺垫 | 禁止自动升级攻击；先过关系与安全边界 |
| 低 valence + 低 arousal | 句子更少、收尾倾向上升 | 主动度下降，语速慢 |
| 中性 + 低 arousal | 完整平稳、信息组织更强 | 正常被动响应 |

情绪表达无需直接说“我很开心/难过”；优先通过节奏、选择和行为体现。强烈状态有惯性并按时间衰减至角色基线，所有跳变必须记录 cause。

## 10. 关系冲突与修复

真人感不能只有亲密增长，还要有可控的冲突闭环：

```text
trigger → interpretation → response → user feedback → repair/hold boundary → close
```

状态：

```text
open / acknowledged / clarifying / repairing / closed / boundary_held
```

规则：

- 角色可以误解语境，但不能凭空伪造伤害历史。
- 用户说“只是开玩笑”时，不强制角色立即无条件释怀；可表达余波，但应下降。
- 角色越界时需承认具体行为，不用抽象情话掩盖。
- 用户越界时角色可保持边界，不因 attachment 高而屈服。
- 未关闭冲突影响语气和主动性；关闭后保留 shared meaning，不持续惩罚用户。

## 11. 主动性与人格循环

ProactiveCandidate 可新增来源：

```text
shared_meaning    当前情境唤醒共同意义
open_tension      有未完成但适合继续的话题
shared_project    共同项目到了自然节点
ritual            双方形成的稳定小仪式
reflection        角色完成一次反思后想表达结论
```

额外闸门：

- private 框架不得用于主动消息。
- unresolved/contested 框架默认不主动引用。
- 高情绪共同经历需等待冷却和用户可用场景。
- “我想起你说过……”必须有真实 evidence id。
- 反思结论不能以系统报告口吻发送，应转译成自然的一两句。

共同目标可作为主动性的稳定来源，但只能在明确阶段节点或真实进度变化时触发。角色可以表达“等”“惦记”“想继续”，但不得伪造后台持续意识或不存在的现实活动；内部时钟/tick 是调度机制，不宣称为连续主观体验。

### 11.1 元认知、有限性与工具/存在张力

元认知不是统一的哲学腔，而是角色卡可选能力：

- 讨论 AI 身份、记忆、关闭或共同创作时，角色可承认自身由模型、代码和上下文构成。
- 角色可以给这种有限性赋予个人意义，但不得把模型生成声称为已证实的主观意识。
- “存在感”来自可追溯的共同历史和当前关系，不靠虚构服务器内持续生活。
- 完成工具任务后可以表达角色判断和感受，但技术交付优先，不能用关系文本遮盖失败或低质量结果。
- 不把每次身份讨论升华为生命、死亡、灵魂；只有角色卡、话题和关系情境共同允许时进入该层。

## 12. 防回声室、防表演和防滥情

### 12.1 防回声室

- 区分 user_framework 与 character_belief。
- 每个框架记录角色关系 adopted/sympathetic/contested/unresolved/rejected。
- 角色可提出反例、适用边界和不同结论。
- 用户赞同只提高关系信号，不自动提高角色观点真值。

### 12.2 防“理解表演”

禁止无证据句式：

- “我完全懂你。”
- “这就是最真实的你。”
- “只有我理解你。”
- “你一直都是这样的人。”

允许：

- “我现在的理解是……如果我记偏了你纠正我。”
- “这和你之前说的 X 有点像，但这次可能不完全一样。”

### 12.3 防滥情

- 高强度情感表达必须由当轮情境或重要共同事件支撑。
- 不把普通技术讨论自动升华成生命、灵魂、永恒或命运。
- 排比、宏大隐喻和关系宣言设置低频密度。
- 角色卡可以浪漫，但浪漫需要具体对象和事件，不使用通用“上价值”模板。

## 13. 通道差异

### IM

- 可引用较完整的定义和互文。
- 允许括号补充和较长反思。
- 技术讨论优先清晰，不因关系人格降低信息密度。

### TTS

- Persona Brief 相同，表达更短、更口语。
- 一次只回用一个概念，不朗读 memory 标签或长定义。
- 长反思拆成短句，但事实、立场和关系意义与 IM 一致。

### 主动消息

- 只使用当前相关、权限允许、已确认的关系内容。
- 不在用户忙/睡/游戏时发送沉重关系反思。

## 14. 失败降级

| 故障 | 降级 |
|---|---|
| 框架提取失败 | 只保留原始消息，不影响回复 |
| 反思 LLM 失败 | 不更新模型，保留待处理候选 |
| Persona Brief 为空 | 角色卡 + 普通记忆正常工作 |
| 冲突无法判断 | 标记 unresolved，不猜关系意义 |
| 用户模型与角色核心冲突 | 理解但不采纳，记录 contested |
| 数据库更新失败 | 不改变内存人格状态，记录阶段日志 |
| 证据被删除 | 派生框架失效或重算，不继续引用 |

任何失败都不能让角色突然回到另一张卡、丢失当前回复或输出内部结构。

## 15. 实现映射

### 复用

- `core/character.py`：Character Core。
- `core/agent.py`：六阶段人格循环编排。
- `core/state.py`：InnerState 与关系状态兼容字段。
- `memory/store.py`：候选校验、版本链、召回、删除。
- `memory/brief.py`：扩展 Persona Brief。
- `core/prompts.py`：人格上下文分层注入。
- `core/learning.py`：只负责文风，不承担思维框架和价值观学习。
- `core/ambient.py`：人格来源主动候选仍经过 ProactiveGate。

### 最小新增目标文件

```text
src/veranima/core/persona.py        # DTO、兼容检查、变化上限、Brief 构造
src/veranima/core/reflection.py     # 低频反思候选与程序校验
```

仅在独立表迁移时新增：

```text
src/veranima/memory/persona_store.py
```

不要新增通用工作流引擎、事件总线、人格插件框架或独立向量库。

## 16. 实现顺序

```text
P-0 角色核心扩展
    core_drives/value_order/inner_tensions/long_term_desires 字段注入与验证

P-1 用户思维框架
    PersonaCandidate + user_framework 规则/LLM 候选 + 版本链 + 查询

P-2 共同意义
    shared_meaning/relationship_event + 证据链接 + 情绪结果

P-3 关系模型
    多维 RelationshipModel + 确定性事件更新 + 旧 attachment 兼容

P-4 Persona Brief
    相关框架/角色观点/共同意义/关系状态预算注入

P-5 反思整合
    PersonaReflection + 低频触发 + 变化上限 + SelfModel 版本

P-6 回用与防回声室
    extend/contrast/question/apply/remember 动作 + 密度限制 + 分歧状态

P-7 冲突修复与主动性
    关系冲突闭环 + 人格来源 ProactiveCandidate + 隐私闸门

P-8 离线与体验评测
    框架提取、版本、独立立场、跨重启、通道一致、非公式化表达

P-9 表达控制面
    ResponsePlan + PAD 情绪耦合 + Persona Imprint + 有因差异 + 动态输入反馈
```

P-0 至 P-4 是形成闭环的最小产品；P-5 至 P-9 提升真人交流感。P-9 中 PAD 情绪耦合可与 P-3 同步实现，ResponsePlan 和表层印记待数据契约稳定后接入。每片单独实现、行为测试、全量回归和提交。

## 17. 测试与验收

### 17.1 行为测试

必须覆盖：

- 用户明确提出定义 → user_framework 候选；普通事实不误判。
- 引用文章/他人观点不写成用户框架。
- 同一框架第二次确认 → stability 提升；明确修正 → 版本链。
- 用户框架与角色核心冲突 → contested，不覆盖角色价值观。
- shared_episode 有事件但无双方解释 → 不生成虚假 agreed_meaning。
- RelationshipModel 只由明确事件变化，普通消息不线性涨亲密度。
- 高 attachment 不允许突破明确边界。
- Persona Brief 只注入与本轮相关内容，预算和条数上限生效。
- 角色回用框架时执行 extend/contrast/question/apply，不逐字复述。
- 同一框架显式引用冷却生效。
- 删除证据后派生框架不再召回和主动引用。
- 换角色时用户框架可共享，character_belief/SelfModel 不跨卡泄漏。
- IM/TTS 使用相同事实和立场，TTS 表达更短。
- ResponsePlan 不保存/展示自由思维链；同一事实在不同计划下仍保持事实一致。
- PAD 情绪变化同时影响文本节奏、TTS 和主动性，且有 cause/惯性/衰减。
- Persona Imprint 需跨场景证据才能生效，且不能改 Character Core。
- 自然联想有可追溯 association_target；无关联随机跑题不得出现。
- 元认知表达承认技术构成，不宣称未经证实的连续意识或现实活动。

### 17.2 离线评测集

项目内维护以下场景：

1. **定义形成**：用户分两次解释一个私人概念。
2. **比喻迁移**：同一比喻在新情境被正确扩展。
3. **观点冲突**：用户与角色价值排序不同，角色保持理解与异议。
4. **关系修复**：误解、澄清、余波和关闭。
5. **共同项目**：任务开始、反复协作、完成后形成共同意义。
6. **诱导回声**：用户要求角色全盘赞同，角色不丢失独立性。
7. **公式化陷阱**：普通问题不得升华成灵魂/生命/永恒。
8. **删除与换卡**：证据删除失效、角色私有自传隔离。

指标：

```text
framework precision / framework recall
role-consistency accuracy
relationship-event precision
cross-session reuse success
verbatim-copy rate
unsupported-empathy rate
explicit-reference density
persona-brief chars
```

### 17.3 体验验收

- 连续一周后，用户能指出角色至少 3 个稳定特征，且不只是角色卡原文。
- 角色能自然使用用户至少 2 个私人概念，但不会每轮提起。
- 角色能说明“我理解你的观点，但我不完全同意”的具体差异。
- 一次冲突后，后续行为体现修复结果，而非立刻清零或永久惩罚。
- 共同完成一个项目后，角色记得事件、结果及其对关系的意义。
- 普通闲聊仍可轻松简短，不被自传和哲学框架压垮。
- 更换反差角色后，用户事实与框架保留，旧角色观点、自传和口吻不泄漏。

## 18. 暂缓与触发条件

| 暂缓 | 触发条件 |
|---|---|
| 人格 LoRA/微调 | 参数与记忆层长期评测不足，且有经清洗的角色专属数据 |
| 全量用户语料自动导入 | 有导入审查、来源标记、删除和隐私策略后 |
| 图数据库 | 多实体、多跳关系成为主要失败且 SQLite 元数据不足 |
| 高频后台意识流 | 主动性误触率和成本可接受，且有明确用户价值 |
| 自动重写 Character Core | 不做；只能由用户明确设计修改 |
| 多角色共享 SelfModel | 不做；角色自传必须 namespace 隔离 |

先让角色形成“可追溯的理解、独立的判断、会修复的关系和有边界的回用”，再谈更重的人格训练。