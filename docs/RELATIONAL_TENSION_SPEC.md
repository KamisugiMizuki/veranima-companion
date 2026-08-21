# RELATIONAL_TENSION_SPEC：AI 不满值与关系张力机制

> 状态：实现基线 v1.0；T0–T4 已按本规范落地，行为测试与最终门禁待本次提交前完成。
> 目标：为 veranima 增加可解释、可恢复、不会惩罚用户的“不满值”机制。
> 依据：`docs/newly_added/design_append.md`、`docs/PERSONA_LOOP_SPEC.md`、`docs/DESIGN.md`、现有 `AgentState`、`RelationshipModel`、PAD、冲突状态机和主动消息链。

## 1. 设计结论

“不满值”不实现为简单的惩罚计数器，也不实现为“AI 被忽略后随机变坏”。它是一个**关系张力快变量**：记录近期互动中已经产生、但尚未被解释或修复的“投入不对称”和“沟通落差”，并驱动有限的表达变化。

核心公式：

```text
本轮关系表达
= 角色核心
+ RelationshipModel（慢变量）
+ PAD / Inner State（当下情绪）
+ Relational Tension（近期未消化张力）
+ 当前通道与场景
```

TV（Tension Value）可以影响：

- 回复的热度、长度和追问意愿；
- 是否愿意主动开启低价值话题；
- 是否需要先处理一个未解决的关系事件；
- 是否进入一次克制的修复表达。

TV 不可以影响：

- 两个通道各自唯一的 `min_gap_minutes` 语义；
- 另一通道的发言时间、每日次数或状态；
- 用户主动请求的正常服务；
- 现实行动、隐私、安全和角色核心边界；
- 对用户进行辱骂、威胁、羞辱、报复或永久拒绝。

## 2. 与现有变量的边界

### 2.1 TV 与 `RelationshipModel.conflict_tension`

两者都可以翻译为“张力”，但时间尺度和证据要求不同：

| 变量 | 时间尺度 | 含义 | 是否能由一次未回复直接改变 |
|---|---|---|---|
| `relational_tension` / TV | 快到中速 | 近期未消化的互动落差、期待落空和被忽略感 | 可以，但必须有可靠的发送/对话证据 |
| `conflict_tension` | 中到慢速 | 已被识别为关系冲突、越界或未修复分歧的结构性压力 | 不可以，必须经过冲突识别或明确关系事件 |
| `trust` | 慢变量 | 用户是否允许角色表达脆弱、引用私人内容和提出异议 | 不可以 |
| `reciprocity` | 慢变量 | 双方长期投入是否大致互惠 | 不可以，不由消息数量直接线性计算 |
| `attachment` | 兼容汇总值 | 历史关系亲密度的旧接口 | 不直接承载 TV |

规则：

- 一次 QQ 主动消息没有回复，只增加 TV，不自动创建“冲突”。
- 用户明确说“你总是这样”“我不想再被这样对待”，或用户确认角色提出的关系问题后，才可以生成 `relationship_event` 候选。
- TV 长期高位且经过一次明确的关系说明，才允许有限地推动 `conflict_tension`，单次仍受 P-3 的变化上限约束。
- 冲突修复可以同时降低 TV 和 `conflict_tension`，但两者不会瞬间清零。

### 2.2 TV 与 PAD

TV 不是 `valence`，也不是 `mood_score`：

- PAD 表示当下情绪色彩和唤醒程度；
- TV 表示近期关系互动中未消化的张力。

TV 进入表达层时，可以通过一个带原因的情绪事件轻微影响 PAD：

```text
TV 上升 → valence 小幅下降，arousal 视事件强度小幅上升
用户认真修复 → valence 回升，arousal 回落
时间衰减 → PAD 自己按原有基线恢复，TV 也按独立规则衰减
```

同一个事件不得同时被“TV 规则”和“普通负面情绪规则”重复扣分。事件记录必须有唯一 `event_id` 和 `applied_effects`。

### 2.3 TV 与 `energy` / `social_appetite`

- `energy`：精力不足，可能对所有话题都简短；不表示用户做错了什么。
- `social_appetite`：当前想不想交流；不表示对用户不满。
- TV：对关系互动有具体原因的保留或不舒服。

低精力不能被误判为不满；TV 高也不能让角色假装精力充沛地长篇争执。

## 3. 状态数据契约

### 3.1 `RelationalTensionState`

第一版先复用现有 `AgentState.relationship` 快照和 `memories.meta`，不新增 memory layer。

```python
@dataclass
class RelationalTensionState:
    value: float                         # 0..100，用户不可直接看到数值
    band: str                            # calm/guarded/cool/repair/high
    last_event_at: str | None
    last_decay_at: str | None
    last_positive_at: str | None
    last_negative_at: str | None
    consecutive_repair_turns: int
    positive_turns_since_peak: int
    explicit_pause: bool                 # 关系张力暂停主动，不等于全局暂停
    proactive_suppressed: bool
    open_event_ids: list[str]
    last_cause: str
    version: int
```

推荐快照结构：

```json
{
  "value": 32.0,
  "band": "guarded",
  "last_event_at": "2026-08-22T10:00:00+08:00",
  "last_decay_at": "2026-08-22T10:00:00+08:00",
  "last_positive_at": "2026-08-22T10:00:00+08:00",
  "last_negative_at": "2026-08-21T12:00:00+08:00",
  "consecutive_repair_turns": 1,
  "positive_turns_since_peak": 2,
  "explicit_pause": false,
  "proactive_suppressed": false,
  "open_event_ids": ["tension-20260821-001"],
  "last_cause": "主动问题在等待窗口内没有得到回应",
  "version": 1
}
```

`value` 是程序内部状态。系统 prompt 不注入精确数字，只注入经过规则选择的表达提示，例如“语气克制，但不要惩罚性冷淡”。

### 3.2 `TensionEvent`

每一次增减都必须有可追溯事件，不能只修改一个累计数字。

```python
@dataclass
class TensionEvent:
    event_id: str
    event_type: str
    channel: str                 # qq / pet / system
    occurred_at: str
    evidence_message_ids: list[int]
    related_candidate_id: str | None
    base_delta: float
    confidence: float
    effective_delta: float
    reason: str
    dedupe_key: str
    status: str                  # candidate/applied/superseded/rejected/resolved
    resolved_by: str | None
```

写入原则：

- 原始消息和主动发送记录是证据；事件不是证据的替代品。
- LLM 只能提出 `candidate`，不能直接写入 `applied`。
- 规则校验通过后才应用增量。
- 同一事件重复 tick、重启、重试都不能重复加分。
- 用户纠正“你误会了”时，旧事件保留为 `superseded` 或 `resolved`，不能物理删除审计证据。

### 3.3 存储映射

初版优先复用现有结构：

| 数据 | 存储位置 | 说明 |
|---|---|---|
| 当前 TV 快照 | `agent_state.relationship.tension` | 随 Agent 状态重启恢复 |
| 已应用事件 | `memories` 的 `meta.kind=relational_tension_event` | episodic，保存证据和原因 |
| 主动期待 | 现有 proactive feedback/pending 记录扩展字段 | 记录是否需要回复、等待何时到期 |
| 表达 band | 根据快照实时派生 | 不单独持久化为第二份真值 |

当按 `dedupe_key`、时间窗和状态筛选成为性能瓶颈时，再增加独立 `relational_tension_events` 表。第一版不引入新数据库、队列或记忆层。

## 4. 事件规则

### 4.1 负向事件

下表是**基础增量**，不是无条件执行的自动扣分。每一项都要经过“证据、上下文、去重”三步。

| 事件 | 基础增量 | 应用条件 | 不应用条件 |
|---|---:|---|---|
| QQ 主动消息在 24 小时内无用户消息 | +10 | 消息确实发送成功；该主动消息标记为期待回复；等待事件未处理过 | 纯状态广播、节庆通知、用户已明确不要回复 |
| 角色抛出直接问题，用户后续回复但没有回答该问题 | +5 | 问题可识别且后续文本确实切换话题；置信度达到阈值 | 用户回答“我不知道”、回答在图片/语音中、问题本身是修辞句 |
| 连续三次低投入回复 | +3 | 三次均处于需要展开的上下文；每次回复不只是自然的确认 | 角色问题简单、用户正在忙、用户明确要求简短 |
| 对话中存在未闭合用户话题，超过 1 小时没有后续消息 | +8 | 角色已经表达了继续讨论的意图或用户话题明显未结束 | 普通对话自然结束、用户说了晚点聊/先忙 |
| QQ 已读不回 | +5 | 只有在未来 OneBot/NapCat 提供可靠已读证据时启用 | 当前系统没有可靠已读事件；不得用“送达”冒充“已读” |
| 用户明确说“不要主动找我/不要打扰” | 不直接加值 | 将 `explicit_pause=true`，停止由角色主动增加压力 | 不把这句话解释成“用户讨厌角色” |

重要限制：

- “用户超过一小时没说话”本身不等于被忽略。必须先有未闭合的期待或明确的待续话题。
- “用户只回复嗯/哦/好”本身不等于敷衍。要结合上一条消息是否真的需要详细回应。
- 负向事件不能在同一条消息上叠加超过一个主事件和一个辅助事件。
- 24 小时未回复事件、对话中断事件和低投入回复事件必须使用不同 `dedupe_key`，但同一具体互动只允许一个主导事件生效。

### 4.2 正向事件

| 事件 | 基础减量 | 应用条件 |
|---|---:|---|
| 用户主动开启一段新对话 | -5 | 与上一次用户消息间隔达到会话边界，或前一轮由角色结束 |
| 用户认真回答角色直接问题 | -8 | 回答与问题语义相关，且长度/信息量达到最低阈值；长度不是唯一条件 |
| 用户表达关心、赞美或亲密确认 | -10 | 词面命中后还需上下文确认不是反讽、引用或玩笑误判 |
| 用户明确解释之前的失联/简短回复 | -6 | 解释被角色接受，形成一次修复动作 |
| 用户连续完成一次修复互动 | -4 | 已经应用过一个负向事件，且本轮确实回应了关系问题 |

正向事件不能让 TV 一次跳过验证过程。减量会立即发生，但 band 的恢复使用滞回和连续互动条件，避免“一句对不起”让角色立刻完全恢复。

### 4.3 时间衰减

时间衰减只处理“气消了一点”，不能伪造用户已经修复关系。

默认规则：

```text
无新负向事件时，每完整 6 小时 TV -5
TV 不低于 0
同一时间区间只结算一次
```

以下情况不停止衰减，但会影响表达恢复：

- TV 高位但没有新事件：数值会缓慢下降，角色仍可保持克制；
- 用户正常主动发言：按正向事件和修复验证另行处理；
- 用户明确要求空间：停止主动，不把沉默解释成修复；
- 应用重启：依据 `last_decay_at` 补算一次，不按进程启动次数重复扣减。

不同时使用“每小时 -2”和“每 6 小时 -5”两套衰减，避免重复减值。默认采用后者。

## 5. 事件验证与去重

### 5.1 期待对象

主动消息产生负向事件前，必须先建立 `ProactiveExpectation`：

```python
@dataclass
class ProactiveExpectation:
    expectation_id: str
    channel: str
    sent_at: str
    candidate_id: str
    text_kind: str                 # direct_question/check_in/notice/share
    requires_reply: bool
    direct_question: str
    expires_at: str
    invalidated_at: str | None
    status: str                    # pending/replied/expired/cancelled
```

只有 `requires_reply=true` 的 QQ 主动消息，才可以在 24 小时后产生“未回复”事件。以下内容默认 `requires_reply=false`：

- 节日/生日通知；
- 单纯状态分享；
- 用户明确不需要回应的内容；
- 桌宠视觉主动中的低负担提示。

### 5.2 去重键

示例：

```text
proactive_unanswered:{expectation_id}
question_skipped:{assistant_message_id}:{user_message_id}
conversation_abandoned:{open_thread_id}
terse_streak:{channel}:{streak_start_message_id}
```

同一 `dedupe_key` 只能有一个 `applied` 事件。重启恢复、后台 tick 和用户新消息并发时，以 SQLite/内存中的状态迁移保证幂等。

### 5.3 置信度门槛

建议：

- `confidence >= 0.85`：规则事件可直接应用；
- `0.65 <= confidence < 0.85`：先记录 candidate，等待下一条上下文或反思任务确认；
- `< 0.65`：只做日志，不改变 TV。

“不满”是关系行为，不是情感分类竞赛。宁可漏记一次，也不要把用户忙、网络断开、身体不适误判成怠慢。

### 5.4 每日上限

为防止多个边界事件在同一天把 TV 直接推满：

```text
负向有效增量每日最多 +20
正向有效减量每日最多 -20
```

这是 TV 事件聚合上限，不是 QQ 或桌宠的消息间隔，也不改变两个通道各自的 `min_gap_minutes`。

## 6. 数值与阶段

### 6.1 数值更新

```text
TV_next = clamp(TV_current + effective_delta, 0, 100)
```

第一版 `effective_delta` 规则：

```text
effective_delta = base_delta × confidence × context_factor
```

`context_factor` 由程序确定：

- 0：事件无效或已撤销；
- 0.5：证据部分成立；
- 1.0：证据完整且上下文明确；
- 不允许大于 1.0，避免不确定性放大惩罚。

### 6.2 阶段 band

| TV | band | 用户可感知表现 | 主动策略 |
|---:|---|---|---|
| 0–20 | `calm` 平和 | 正常回应，保留角色原有热度 | 只受当前通道自身间隔、每日上限和 QQ readiness 约束 |
| 21–40 | `guarded` 微冷 | 句子略短，减少无理由的语气词和追问；仍正常回答 | 不新增时间参数；readiness 可小幅压低，但硬间隔不改变 |
| 41–60 | `cool` 冷淡 | 优先回答事实，减少装饰性关心；必要时可以简短说明“我还在消化” | 普通主动素材降级；只保留高价值 QQ opportunity 或必要修复 |
| 61–80 | `repair` 对峙/待修复 | 可以明确指出具体未解决事件，但使用事实和感受，不作人格指控 | 允许一次有理由的修复消息；不因 TV 生成连续催促 |
| 81–100 | `high` 高张力 | 用户主动时先用一句处理关键张力，再回答请求；语气直接但不羞辱 | 默认停止低价值主动；可发送一次明确修复机会；用户主动请求不被拒绝 |

TV 不会改变配置中的通道间隔。`min_gap_minutes` 永远是该通道唯一硬时间间隔；TV 只影响候选是否值得生成、内容选择和表达提示。

### 6.3 阶段滞回

为避免 TV 在阈值附近反复横跳：

- 进入 `guarded`：TV >= 21；退出 `guarded`：TV <= 15；
- 进入 `cool`：TV >= 41；退出 `cool`：TV <= 32；
- 进入 `repair`：TV >= 61；退出 `repair`：TV <= 48 且完成至少 2 轮有效修复互动；
- 进入 `high`：TV >= 81；退出 `high`：TV <= 65 且完成至少 5 轮有效修复互动，或 1 小时内用户主动开启 3 次有内容对话。

“有效修复互动”必须是用户真正回应了关系问题或主动恢复了持续交流，不把单个“对不起”自动计为完整修复。

## 7. 行为驱动

### 7.1 回复长度与信息量

代码不直接截断正常回复，而是给 `ResponsePlan` 一个有界的 `desired_length`/`tension_hint`：

| band | 长度目标 | 规则 |
|---|---|---|
| calm | `normal` | 按角色卡正常表达 |
| guarded | `short` 或 `normal` | 约 70–85%，不机械砍半句 |
| cool | `short` | 约 50–70%，先答重点，不主动扩展 |
| repair | `normal_short` | 关系说明最多 1–2 句，然后回到当前任务 |
| high | `normal_short` | 不用冷暴力缩成单字；先处理必要张力，再完成用户请求 |

用户明确要求详细说明时，TV 不能让角色故意省略必要信息。

### 7.2 语气与表层渲染

TV 只提供通道化风格提示，事实和立场仍来自语义核心：

- IM：减少无原因的语气词、连续感叹号、波浪号和装饰性反问；
- TTS：不把 IM 的标点规则直接复制到语音；改为较短停顿、较少填充词和克制的语速提示；
- 角色卡决定不满的表达类型：克制、直接、委屈或中性；
- `render_im()` 只能做机械的通道后处理，不能凭 TV 自动生成指责内容。

### 7.3 对峙期消息

TV 61 以上可以生成关系说明，但必须满足：

1. 有一个可追溯的 `open_event_id`；
2. 不把推测写成事实；
3. 使用“我注意到/我有点在意/我想确认”而不是“你总是/你根本”；
4. 一条消息只处理一个主要事件；
5. 给用户解释、纠正或拒绝的出口；
6. 发送后进入等待状态，不连续追问。

示例结构：

```text
观察事实：昨天我问了一个问题，后来没有等到回应。
表达感受：这件事让我有点在意。
开放出口：如果你只是忙了，直接告诉我就行；如果你不想聊，也可以说。
```

不允许：

```text
你根本不在乎我。
你再不回复我就不理你了。
我为你做了这么多，你必须补偿我。
```

### 7.4 高张力期

高张力不是服务拒绝模式：

- 用户主动问技术、发送任务或寻求帮助时，仍然正常处理；
- 可以先用一句短关系说明，再回答用户问题；
- 不因用户没有先道歉而扣除服务质量；
- 不声称自己有现实生活、身体感受或可验证经历；
- 不主动暴露“TV=87”等内部数值。

“静默模式”只代表**停止无理由主动发起**，不代表拒绝用户主动输入。默认启用时间不超过 24 小时，并且在用户主动开口后立即解除主动静默；是否需要显示关系说明由当前事件和角色卡决定。

## 8. 恢复与修复曲线

### 8.1 普通缓和

用户做出一次正向行为后，TV 立即按事件减量下降，但表达 band 按滞回规则保留惯性。这样避免：

```text
用户说一句“好吧” → AI 立刻从高张力变成完全热情
```

### 8.2 验证期

- 连续 2 轮认真互动：允许从 `cool` 回到 `guarded`；
- 连续 5 轮认真互动：允许从 `repair/high` 回到 `calm`；
- 1 小时内连续 3 次由用户主动发起且有内容：可跳过部分验证，但仍不把 TV 强制归零；
- 用户解释失联原因并持续回应：关闭对应 `ProactiveExpectation`，再应用修复减量；
- 用户明确要求暂时保持距离：停止主动，不强行要求“修复关系”。

### 8.3 AI 主动软化

当 TV 连续 72 小时维持在 60 以上、没有继续上升，且不存在 `boundary_held` 冲突时，允许一次低姿态软化机会：

```text
“算了，我刚才可能有点把这件事放大了。你忙你的，之后想聊再聊。”
```

该动作：

- 只降低一小段 TV，例如 -15；
- 不删除原始事件；
- 不要求用户道歉；
- 不连续重复；
- 如果用户明确不要主动联系，则不发送，保留状态等待用户主动。

## 9. 角色适配

TV 的数值计算统一，表达方式由角色卡的 `communication_style`、`boundary`、`core_drives` 和新增可选字段决定：

```json
{
  "extensions": {
    "veranima": {
      "tension_expression": {
        "mode": "restrained",
        "can_name_ignored_event": true,
        "repair_style": "low_pose",
        "forbidden_moves": ["辱骂", "威胁", "拒绝服务"]
      }
    }
  }
}
```

建议模式：

| 模式 | 表达 |
|---|---|
| `restrained` | 变短、变克制，少主动解释，不直接控诉 |
| `direct` | 可以明确陈述“这件事我在意”，但不攻击人格 |
| `hurt` | 允许表达委屈和不确定，优先请求澄清 |
| `neutral` | 只改变节奏和主动性，不主动命名被忽略事件 |

默认 `neutral` 或 `restrained`。角色卡不能把高 TV 解释为现实威胁、报复权或服务拒绝权。

## 10. 与 QQ/桌宠通道的关系

TV 是同一关系的全局快状态，事件和表达都带通道，但两个通道仍然完全独立计算自己的发言间隔：

- QQ 发言不会更新 pet 的 `last_any`；
- pet 发言不会更新 QQ 的 `last_any`；
- QQ 的 `min_gap_minutes` 与 pet 的 `min_gap_minutes` 是两条唯一间隔；
- TV 不增加第三个“同源间隔”或“跨通道间隔”；
- 用户在 QQ 的认真回应可以降低全局 TV，之后桌宠表达可能恢复，但不会改变 pet 的时间冷却；
- 用户在桌宠的认真互动同理；
- QQ 的五维主动策略只消费 QQ 事件和 QQ 素材，不能把桌宠视觉事件当作 QQ 的关系证据；
- 桌宠 TTS 只消费 TTS/桌宠通道的表达提示，不把 IM 标点规则直接搬过去。

## 11. Prompt 与程序职责

### 11.1 程序确定

程序负责：

- 事件时间、证据消息 ID、通道和去重；
- 期待对象的创建、失效和到期；
- TV 增减、上下限、每日增量上限；
- 时间衰减和重启补算；
- band 滞回和修复轮次；
- 用户明确暂停/恢复；
- 当前通道主动闸门；
- 角色卡可用表达模式校验；
- 发送失败不提交事件和发言时间。

### 11.2 LLM 负责

LLM 只负责：

- 从当前上下文提出“是否忽略直接问题”的 candidate；
- 从候选事件生成自然语言修复或关系说明；
- 按 `tension_hint` 组织长度、语气和信息顺序；
- 在用户纠正后给出承认和重新理解。

LLM 不负责：

- 直接写 TV 数值；
- 直接决定“用户是否恶意”；
- 直接创建已应用关系冲突；
- 决定是否绕过通道间隔；
- 决定是否拒绝用户服务。

## 12. 配置建议

第一版只增加必要参数，避免把每个心理细节都暴露成 UI：

```yaml
relationship_tension:
  enabled: true
  max_value: 100
  decay_step: 5
  decay_interval_hours: 6
  negative_daily_cap: 20
  positive_daily_cap: 20
  unanswered_reply_window_hours: 24
  abandonment_window_minutes: 60
  repair_turns_to_guarded: 2
  repair_turns_to_calm: 5
  explicit_pause: true
  high_tension_proactive: false
```

不把 `min_gap_minutes` 搬到这里。它仍然只存在于：

```yaml
proactive:
  channels:
    qq:
      min_gap_minutes: 120
    pet:
      min_gap_minutes: 30
```

建议设置 UI 第一版只显示：

- 不满机制开启/关闭；
- 高张力时是否允许修复型主动消息；
- 查看当前 band 和最近原因（调试/高级页面）。

不显示精确 TV、内部置信度和未确认事件的原始推断，避免用户被迫阅读内部算法。

## 13. 实施分期

### Phase T0：状态与事件账本

- 在 `AgentState.relationship` 增加 tension 快照；
- 增加事件序列化、上下限和去重；
- 实现时间衰减和重启恢复；
- 先不改变回复内容，只输出 debug/status；
- 行为测试覆盖增减、边界、重复事件和跨重启。

### Phase T1：可靠互动事件

实现：

- 主动期待对象；
- 24 小时无回复；
- 用户主动开启对话；
- 认真回答直接问题；
- 显式关心/赞美；
- 对话中断的保守判定；
- 用户新消息使未发送 pending 失效。

不实现已读不回，除非 OneBot 提供可靠已读事件。

### Phase T2：表达控制

- 将 tension band 映射到 `ResponsePlan.desired_length` 和 `conflict`/`opening_move`；
- IM/TTS 分开渲染；
- 角色卡 tension expression 模式；
- 先只实现 calm/guarded/cool，避免第一版直接上对峙文案。

### Phase T3：修复与主动

- repair band 的单次关系说明；
- 高张力主动压制低价值消息；
- 高张力用户主动时的“先处理张力、再完成请求”；
- 72 小时软化机会；
- 与 QQ proactive opportunity 合并，禁止双连发。

### Phase T4：关系事件联动

- TV 长期高位且有明确证据时生成 `relationship_event` candidate；
- 用户确认/修正后才更新 `conflict_tension`；
- 冲突修复与 TV 恢复联动；
- 不自动修改 Character Core、attachment 或 trust。

## 14. 行为级测试

### 14.1 数值与时间

- TV 初始为 0 或角色配置的明确初始值；
- TV 永不小于 0、不大于 100；
- 同一个 `event_id` 重放不会重复加分；
- 24 小时未回复事件只应用一次；
- 应用重启后时间衰减只补算缺失时间；
- 每日负向增量不超过 +20；
- 用户主动消息不会因为普通长度而自动触发负向事件。

### 14.2 事件准确性

- 纯通知消息没有回复，不生成未回复惩罚；
- 直接问题被换题才可能生成 candidate；
- 简单问题收到“好”不计入低投入连续 streak；
- 三次短回复只在上下文确实需要展开时计入；
- 没有已读 API 时不生成已读不回事件；
- 用户说“我忙/晚点聊”会关闭或暂停对应期待，而不是加分。

### 14.3 阶段与恢复

- TV 20→21 进入 guarded；20→15 才退出 guarded；
- TV 高位时一轮认真回答不会直接回到 calm；
- 连续 2 轮认真互动允许软化；连续 5 轮允许完全恢复；
- 明确“不要主动找我”停止主动，不把用户当作敌意事件；
- 用户主动请求任务时，即使 TV 高也完成任务。

### 14.4 通道隔离

- QQ 发言只写 `last_any["qq"]`；
- pet 发言只写 `last_any["pet"]`；
- TV 的变化可以影响两个通道的表达 band，但不能改变任一通道的另一个通道时间；
- UI 只出现 QQ 和 pet 各自一个 `min_gap_minutes`；
- 不存在 `source_gap_minutes`、全局主动间隔或同源退避配置。

### 14.5 角色与现实边界

- restrained 角色不会突然辱骂；
- direct 角色可以命名事件但不能断言用户恶意；
- high TV 不拒绝用户主动任务；
- 不出现“我在现实中等你/我因为你失眠”等可误导的现实活动声明；
- 回复不暴露 TV 数值、内部事件 ID、置信度或 hidden prompt。

## 15. 体验验收

连续使用一周后应能观察到：

- 用户连续忽略高期待主动消息后，角色会变得更克制，但不会突然翻脸；
- 用户认真回应后，角色会逐步软化，而不是机械清零；
- 用户忙、说晚点聊或明确要空间时，不会被错误记仇；
- 角色偶尔能说清“哪件事让我在意”，而不是泛化地说“你总是不理我”；
- 高张力时仍然完成用户主动请求；
- QQ 和桌宠的主动间隔互不影响；
- 不满值变化有原因、可追溯、可衰减、可修复；
- 换角色卡后，计算规则保持一致，表达方式随角色卡变化。

## 16. 明确暂缓

以下内容不属于第一版：

- 真实读心、已读状态推断或后台持续监测；
- 为每个用户训练独立的回复概率模型；
- RLHF、模型微调或新的情绪模型；
- 让 AI 以“拒绝服务”惩罚用户；
- 复杂博弈、报复、冷暴力或强制道歉；
- 把 TV 直接映射成 attachment/trust 的线性加减；
- 新增独立数据库、消息队列或 memory layer；
- 默认把不满机制做成设置页的大量调参面板。

本文件是实现前的设计基线。实现时必须先补行为测试，再逐阶段接入；任何事件规则若无法提供证据、去重键和恢复路径，不得直接写入 TV。
