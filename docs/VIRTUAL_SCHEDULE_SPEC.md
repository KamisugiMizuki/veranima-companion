# 虚拟日程与生活主动性模块设计规范

> 版本：v1.0
>
> 状态：设计稿，尚未声称已实现。
>
> 范围：Veranima 角色的虚拟日程、日常活动状态、当前活动对回复表现的影响，以及由此产生的主动了解与主动分享候选。
>
> 关联规范：`DESIGN.md`、`PERSONA_LOOP_SPEC.md`、`MEMORY_SPEC.md`、`QQ_PROACTIVE_SPEC.md`、`VISION_SPEC.md`。
>
> 重要边界：本规范设计的是**角色世界中的持续模拟状态**，不是让程序声称在现实世界中真实上课、通勤、购物、见人或完成了外部行动。所有活动、事件和来源都必须携带虚拟模拟的证据类型；它们可以成为角色化表达的素材，但不能伪装成现实行动证据或用户与角色共同发生过的事实。

---

## 1. 设计原点

当前主动性问题不是缺少几条问候文案，而是缺少一个可以持续产生行为原因的内部生活层。

现有主动入口已经具备时间问候、节庆检查、QQ 五维 readiness、离线思考、视觉注意力和统一 Gate，但它们主要回答的是：

- 现在是否允许发一条消息；
- 最近是否有一个用户话题可以跟进；
- 某个固定时间段是否到达。

它们没有回答：

- 这个角色今天原本打算怎样度过；
- 今天的计划与角色长期习惯相比发生了什么变化；
- 当前角色正在什么活动阶段，因此为什么此刻回复短、慢、急或更愿意展开；
- 角色现在有什么可以自然分享的内部素材；
- 角色为什么此刻突然想了解用户的某件事，而不是随机盘问。

因此，本模块的核心不是“定时生成生活文案”，而是建立一条可追溯的因果链：

```text
角色日程大纲
  → 当日条件与角色状态
  → 当日虚拟计划
  → 当前活动与活动事件
  → 可分享素材 / 用户信息缺口
  → 主动候选
  → QQ 或桌宠自己的 Gate
  → 发送成功后的反馈与回用
```

**活人感的来源**是持续的状态、可解释的偏离、有限的主动性和跨时间的回用，而不是随机插话、固定问候或每轮都描述动作。

---

## 2. 当前代码基线与本规范的修正范围

### 2.1 当前已有能力

基于当前代码的事实盘点：

| 能力 | 当前代码 | 结论 |
|---|---|---|
| 角色卡中的虚拟生活文字 | `CharacterCard` 读取 `virtual_life`、`daily_state` 等字符串 | 只有静态 prompt 素材，没有结构化日程 |
| 固定时段问候 | `GreetingScheduler` | 能做每日去重，但没有活动状态 |
| 三餐提醒 | `MealReminderScheduler` | 是单一 ritual，不等于完整生活 |
| QQ 主动时机 | `QQProactiveAdvisor`、`QQProactiveEngine` | 有时间、状态、素材和社交资本，但素材主要来自用户时间线 |
| 主动统一 Gate | `ProactiveGate` | 已按 QQ/pet 分桶；应继续作为交付闸门 |
| 场景锁 | `SceneLock` | 反映用户侧 busy/away，不是角色自己的日程 |
| 视觉注意力 | `PetServer._process_attention_event()` | 有独立 pet 来源和 Gate，不应被日程模块改成直接发言 |
| 主动回复 | `Agent.tick_proactive()`、`heartbeat()`、`late_reply()` | 主要是 ritual、scene、shared_episode；没有虚拟日程来源 |
| 当前回复 prompt | `build_system_prompt()` | 有角色、记忆、关系和通道上下文，没有当前活动上下文 |

### 2.2 本规范不做的事情

1. 不把用户提供的具体日程案例复制成默认角色日程。
2. 不在代码中硬编码“学校、课程、三餐、电视剧、旅行”等具体生活内容。
3. 不用一个随机 cron 文案池伪装成有生活。
4. 不让 LLM 自由编造整天的活动和完成结果。
5. 不把日程状态写入 `shared_episode`，不把角色虚拟活动伪装成用户与角色共同回忆。
6. 不让 QQ 和桌宠共享一个主动发送冷却或一个开关。
7. 不因为当前活动是“忙碌”就拒绝用户的紧急、明确或认真消息。
8. 不实现真实日历、外卖、地图、学校系统、媒体播放或其他外部行动集成。
9. 不要求每条回复都提及当前活动；活动上下文优先影响节奏，只有合适时才进入可见内容。

---

## 3. 术语与对象边界

### 3.1 四种不同的“事实”

| 类型 | 含义 | 可否作为共同记忆 |
|---|---|---:|
| `user_fact` | 用户明确表达的事实 | 可以，遵循 `MEMORY_SPEC` |
| `shared_episode` | 用户与角色在产品中真实完成的共同对话/协作事件 | 可以，必须有双方来源 |
| `virtual_life_event` | 角色虚拟世界中的模拟活动或计划变化 | 不可以直接写成共同记忆 |
| `external_fact` | 现实世界可验证事实 | 只能在有来源时使用 |

`virtual_life_event` 可以被角色分享，但其 `truth_class` 必须保持 `virtual_simulation`。它不能提供“我在现实中完成了某事”的证据，也不能作为“我们一起做过某事”的依据。

### 3.2 核心对象

- **`ScheduleOutline`**：角色卡定义的长期日程大纲和可变规则。
- **`DayContext`**：某个本地日期的条件，如普通日、休息日、节假日、长假、恢复日或手动覆盖；只描述条件，不包含具体生活文案。
- **`DayPlan`**：由大纲、当天条件和角色状态生成的一份具体虚拟计划。
- **`ScheduleItem`**：计划中的一个时间段活动。
- **`ScheduleEvent`**：活动开始、完成、跳过、偏离、未完成或内部想法等可分享事件。
- **`ScheduleContext`**：当前时间点提供给回复编排器的最小上下文。
- **`UserInfoGap`**：角色“想了解用户”的具体信息缺口、来源和询问历史。
- **`SelfShareCandidate`**：由日程事件、虚拟状态或角色长期兴趣产生的主动分享候选。
- **`CuriosityCandidate`**：由信息缺口和当前关系情境产生的主动了解候选。
- **`ProactiveIntent`**：解释“为什么现在联系”的统一候选协议。
- **`DeliveryGate`**：QQ/pet 各自已有的主动 Gate；日程模块只生产候选，不绕过交付控制。

---

## 4. 角色卡中的日程大纲

日程大纲属于角色卡的 `extensions.veranima.virtual_schedule`，而不是 Python 源码常量。缺少该字段时，模块处于关闭状态或使用明确的空大纲，不得偷偷套用另一角色的生活。

### 4.1 配置骨架

以下是字段契约，不是应直接复制的角色日程：

```json
{
  "enabled": true,
  "schema_version": 1,
  "timezone": "Asia/Shanghai",
  "default_day_profile": "baseline",
  "day_profiles": {
    "baseline": {
      "allowed_block_ids": ["block_a", "block_b"],
      "profile_bias": {
        "structure": 0.7,
        "novelty": 0.3,
        "social_energy": 0.5
      }
    },
    "rest_like": {
      "allowed_block_ids": ["block_a", "block_c"],
      "profile_bias": {
        "structure": 0.3,
        "novelty": 0.7,
        "social_energy": 0.6
      }
    }
  },
  "blocks": [
    {
      "id": "block_a",
      "category": "role_defined",
      "label": "由角色卡定义的活动",
      "preferred_window": {"start": "07:00", "end": "10:00"},
      "duration_minutes": {"min": 30, "max": 120},
      "required": false,
      "allowed_day_profiles": ["baseline", "rest_like"],
      "activity_pool": ["role_defined_variant_1", "role_defined_variant_2"],
      "share_policy": "low_pressure",
      "interaction_profile": "occupied_brief",
      "deviation_policy": {
        "allow_skip": true,
        "allow_shift": true,
        "allow_extend": false,
        "allow_reorder": false
      }
    }
  ],
  "autonomy": {
    "structure_preference": 0.6,
    "deviation_propensity": 0.3,
    "recovery_need": 0.5,
    "novelty_drive": 0.4,
    "max_deviations_per_day": 2
  },
  "interaction_profiles": {
    "occupied_brief": {
      "reply_style": "short_precise",
      "max_sentences": 3,
      "question_budget": 1,
      "share_allowed": false
    },
    "available_normal": {
      "reply_style": "normal",
      "max_sentences": 0,
      "question_budget": 1,
      "share_allowed": true
    }
  },
  "share_policy": {
    "default_enabled": true,
    "max_self_share_per_day": 2,
    "min_same_event_gap_hours": 12
  }
}
```

实现时不要求用户填写原始 JSON。角色卡编辑器应将它拆成结构化表单；在当前没有角色编辑器前，设置页至少提供只读摘要和启用/停用控制，不能把配置错误伪装成 UI 已支持。

### 4.2 活动块字段规则

- `id`：稳定标识，修改显示名称不能改变历史关联。
- `category`：受控枚举，不允许 LLM 临时发明分类。建议保留 `obligation`、`self_care`、`transition`、`personal_interest`、`social`、`rest`、`sleep_window`、`role_defined` 等通用类别；具体内容由角色卡提供。
- `preferred_window`：允许的时间窗口，不是必须精确命中的时间点。
- `duration_minutes`：范围而不是固定时长，计划生成时取一个受约束值。
- `required`：是否为该日 profile 的结构性活动；必需活动也必须允许被系统标记为 `unknown`，不能在进程离线后伪造完成。
- `activity_pool`：角色卡定义的同类变体；计划器只在池内选择。
- `share_policy`：`never`、`low_pressure`、`normal`、`high_value`，决定是否可以产生分享候选。
- `interaction_profile`：当前活动对回复节奏的默认影响。
- `deviation_policy`：该活动允许哪些偏离操作。

### 4.3 角色自由度不是全局随机数

角色的“自由”应拆成可观察参数：

- `structure_preference`：更倾向按计划还是保留弹性；
- `deviation_propensity`：在允许范围内改变活动的倾向；
- `recovery_need`：精力低时是否优先压缩或取消低优先级活动；
- `novelty_drive`：是否倾向从活动池中选不重复变体；
- `max_deviations_per_day`：每日偏离上限。

这些参数属于角色卡慢变量或用户明确配置，不能由每轮回复随机学习。状态变量只能提供当日短期影响，不能永久改写角色核心。

---

## 5. 当日条件与计划生成

### 5.1 `DayContext` 来源优先级

```text
用户/设置页当日显式覆盖
  > 产品提供的本地日历适配器
  > 角色配置的周周期规则
  > default_day_profile
```

第一阶段只实现本地确定性来源：日期、星期、角色配置和手动覆盖。不接入联网节假日 API。未来接入日历时必须作为独立适配器，返回受控的 `day_type` 与标签，不能将外部文本直接注入 prompt。

建议枚举：

```text
baseline / rest_like / holiday_like / extended_break /
travel_like / recovery_like / custom
```

这些是**条件分类**，不是具体日程内容。每个角色可为它们配置自己的活动块组合。

### 5.2 生成顺序

计划器必须按以下顺序执行，后面的层不能破坏前面的硬约束：

1. 解析角色卡并校验时间、类别、活动池和偏离规则。
2. 确定本地日期、时区和 `DayContext`。
3. 选定日 profile，并加载允许的活动块。
4. 根据活动块的窗口、顺序约束和时长范围生成基础计划。
5. 根据角色的结构偏好、创新偏好和当天状态应用有限变体。
6. 应用精力、情绪、关系张力和未完成事项的短期修正。
7. 检查时间重叠、必需块缺失、偏离次数、活动池越界和分享权限。
8. 生成稳定的 `plan_id`、`seed` 和版本，原子持久化。
9. 只有计划持久化成功后，才允许运行时消费它。

### 5.3 稳定种子与幂等

同一 `role_id + local_date + plan_revision + day_context_digest` 必须得到同一份基础计划。推荐：

```text
seed = SHA256(role_id + local_date + plan_revision + day_context_digest)
```

进程重启、设置页刷新和 QQ/pet 两端读取不能让计划重新抽样。用户主动修改当日条件时创建新的 `plan_revision`，旧计划保留审计但不再作为当前计划。

### 5.4 允许的变体操作

计划器只执行角色卡明确允许的操作：

| 操作 | 含义 | 必须记录 |
|---|---|---|
| `shift` | 在窗口内前后移动 | 原窗口、实际窗口、原因 |
| `resize` | 在时长范围内缩短或延长 | 原时长、实际时长、原因 |
| `substitute` | 从同一活动池替换变体 | 原 block、替代项 |
| `skip_optional` | 跳过非必需块 | 跳过原因、是否可回补 |
| `merge` | 合并两个允许合并的相邻块 | 原 item IDs |
| `reorder` | 调整允许重排的块顺序 | 原序列、新序列 |
| `recovery_mode` | 精力不足时压缩低优先级活动 | 状态来源、被影响 items |
| `late_start` | 推迟当天计划的起始阶段 | 延迟量、影响范围 |

每次偏离最多产生一个 `ScheduleEvent`。不能使用“自由性”作为无来源的万能解释；原因必须来自角色参数、当天状态或显式日条件。

### 5.5 LLM 的职责边界

日计划的结构、时间、状态迁移、偏离是否合法全部由代码决定。LLM 只能承担以下可选工作：

- 在已通过校验的活动池中选择一个角色卡允许的自然变体；
- 为已存在的 `ScheduleEvent` 生成内部摘要或可见表达候选；
- 对多个已存在的用户信息缺口排序并给出低成本解释。

LLM 输出必须是单 JSON，经过 schema、枚举、长度、来源和敏感性校验；失败时使用确定性回退。LLM 不得新增活动块、修改 `required`、生成现实地点/人物/外部行动，或直接写入长期记忆。

---

## 6. 计划与活动生命周期

### 6.1 `DayPlan` 状态

```text
draft → active → closed
          ├── revised → active
          └── expired
```

- `draft`：已生成但尚未到计划日或等待校验。
- `active`：当日当前版本。
- `revised`：被用户/设置页/日条件覆盖替换的旧版本。
- `closed`：当天正常结束。
- `expired`：过期但未能确认完整执行；不能等同于“全部完成”。

### 6.2 `ScheduleItem` 状态

```text
planned → active → completed
    ├──────────────→ skipped
    ├──────────────→ expired_unknown
    └──────────────→ cancelled

active → interrupted → resumed → completed
                         ├────→ skipped
                         └────→ expired_unknown
```

`completed` 需要区分：

- `simulation_clock`：仅因虚拟时钟推进而结束；可以作为角色分享素材，但不是现实行动证据。
- `user_visible_interaction`：用户通过对话确认了角色正在/完成某个虚拟活动；仍然是虚拟活动。
- `manual_override`：用户或设置页明确调整。

进程离线后重新启动，不应把所有跨越的活动批量标记为已完成。默认标记为 `expired_unknown`，由下一次计划修正或角色化表达处理。这是防止“后台没运行却声称做完了”的关键约束。

### 6.3 事件类型

建议事件集合：

```text
activity_started
activity_completed
activity_interrupted
activity_skipped
activity_expired_unknown
plan_deviated
plan_revised
unfinished_thought
preference_revisited
```

事件必须包含：

```text
truth_class = "virtual_simulation"
source_plan_id
source_item_id
source_rule_id
occurred_at
reason
share_policy
```

不得把 `source_item_id`、`source_plan_id` 当作消息 ID 或记忆 ID；协议身份保持独立。

---

## 7. 当前活动如何影响对话表现

### 7.1 核心原则

活动状态首先影响**交互资源分配**，其次才影响内容。它不是一个每轮必说的状态前缀。

优先级：

```text
安全/紧急内容
  > 用户明确问题与纠错
  > 关系修复与既有承诺
  > 当前活动的交互画像
  > 可选的角色自我分享
```

因此“当前正在忙”只能让普通闲聊更短、更碎片化，不能让角色逃避认真问题，也不能使用户觉得消息被系统机械拒绝。

### 7.2 `ScheduleContext`

传给 Agent 的上下文只包含当前需要的最小字段：

```python
@dataclass(frozen=True)
class ScheduleContext:
    plan_id: str
    item_id: str | None
    activity_category: str
    phase: str                 # before / active / ending / gap / sleep_like
    progress: float
    interaction_profile: str
    availability: float       # 0..1
    reply_budget: dict         # sentence/question/expansion constraints
    share_allowed: bool
    curiosity_allowed: bool
    source_anchor: dict
```

`source_anchor` 只供内部审计和 prompt 编排，不得出现在用户可见文本中。

### 7.3 交互画像

交互画像由角色卡定义，不由活动名称硬编码。建议提供以下通用画像：

| 画像 | 普通闲聊 | 认真问题 | 主动提问 | 自我分享 |
|---|---|---|---|---|
| `occupied_brief` | 短句、少展开、略显赶时间 | 必须回答核心结论，可承诺稍后展开 | 最多一个低负担问题 | 默认关闭 |
| `occupied_deferred` | 可以先确认收到 | 先给最小可用回答 | 暂停 | 默认关闭 |
| `available_normal` | 正常展开 | 正常展开 | 允许一个有来源的问题 | 允许 |
| `rest_low_pressure` | 放松、可有停顿 | 不降低认真程度 | 低频、可不答 | 允许低投入分享 |
| `transition_fragmented` | 句子可能不完整但不故作忙碌 | 先回答，再说明稍后整理 | 视素材而定 | 允许短分享 |
| `sleep_like` | 非主动场景；用户主动消息仍正常处理但倾向短 | 处理高优先级内容 | 禁止主动追问 | 禁止主动分享 |

“略显急切”只能由 `reply_style`、句数预算、问题预算和扩展预算共同表达；不能通过强行添加“我在忙”“快被发现了”等固定台词实现。

### 7.4 Prompt 接线

目标调用链：

```text
ScheduleEngine.current_context(now)
  → Agent.handle(channel)
  → build_system_prompt(extra_blocks=[format_schedule_context(...)])
  → LLM 生成 Reply
  → shared reply parser
  → IM/TTS 各自渲染
```

接线规则：

1. 当前活动上下文在用户消息和安全边界之后、普通记忆与表达计划之前或由统一编排器合并，具体位置以最终 prompt 预算为准。
2. 只注入当前活动、交互画像、允许的行为资源，不注入整张日程表。
3. 普通回复不强制提及活动；只有用户问“你在做什么”、活动有 `share_policy`、或主动候选明确引用该事件时才允许可见提及。
4. 结构化 Reply 的 tone/portrait 仍由原协议负责；活动画像不能直接伪造 portrait 或 TTS 状态。
5. 如果解析失败或活动上下文失效，回退到正常通道上下文，不让内部 JSON 或来源标记泄漏。

---

## 8. 主动了解用户：`UserInfoGap` 与 `CuriosityCandidate`

### 8.1 不是随机问题库

“想了解用户”必须表示为一个具体的信息缺口：

```python
@dataclass
class UserInfoGap:
    id: str
    topic: str
    reason: str
    value: float
    confidence: float
    sensitivity: str          # ordinary / personal / sensitive
    status: str               # open / answered / declined / paused / obsolete
    source_message_ids: list[int]
    last_asked_at: str | None
    ask_count: int
    cooldown_until: str | None
```

信息缺口的来源只能是：

- 用户已提到但信息不完整的主题；
- 共同项目或明确承诺中确实需要的偏好；
- 用户主动邀请角色继续了解的方向；
- 角色长期关系目标中尚未获得、且不敏感的一项信息。

不能从角色的猜测、assistant 自己的虚构内容或无来源标签生成缺口。

### 8.2 询问规则

生成 `CuriosityCandidate` 前按顺序检查：

1. 主题仍为 `open`，且没有近期已知答案。
2. `sensitivity` 不高于当前关系阶段允许的等级。
3. 最近没有问过相同或语义重复的问题。
4. 当前用户没有表达忙碌、低落、睡眠或明确免打扰。
5. 当前活动的 `curiosity_allowed=true`。
6. 当前通道和全局主动 Gate 允许。
7. 该问题可以用一句自然理由解释“为什么现在想到这个”。

默认每个通道每日最多一个主动了解问题；同一主题至少经过一个可配置冷却周期才能再次询问。用户不回答、拒绝或转移话题时，状态改为 `paused` 或降低优先级，不施加关系惩罚、不追问、不把拒绝写成负面人格证据。

### 8.3 用户回答后的更新

用户回答后，按正常 `MEMORY_SPEC` 处理：

```text
user message
  → source_message_id
  → memory candidate / explicit confirmation
  → UserInfoGap = answered
  → 后续不再把同一缺口当成未知问题
```

置信度不足时可以保留 `uncertain` 版本，但必须区分“还不确定”与“完全未知”。用户纠正后走现有版本链，不能只更新内存字段。

---

## 9. 主动分享自己：`ScheduleEvent` 到 `SelfShareCandidate`

### 9.1 可分享素材来源

主动分享的优先级：

1. 当前或刚结束的、`share_policy` 允许的虚拟活动事件；
2. 活动偏离及其原因，例如计划被压缩、替换或暂缓；
3. 角色长期兴趣在当前活动中产生的一个内部想法；
4. 未完成但仍有效的虚拟思路；
5. 角色状态的低负担变化。

所有候选必须能回指 `virtual_life_event` 或角色卡稳定字段。没有来源时，不生成“我刚刚做了某事”的内容；可以不发，不能用模板填空。

### 9.2 分享候选协议

```python
@dataclass(frozen=True)
class SelfShareCandidate:
    candidate_id: str
    intent: str              # self_disclosure / schedule_bridge / unfinished_thought
    text_source: str         # event / character_anchor / state_change
    source_event_id: str | None
    source_item_id: str | None
    reason: str
    relevance: float
    share_policy: str
    allowed_channels: tuple[str, ...]
    expires_at: str | None
```

候选只描述素材和意图，不直接携带最终用户可见文案。文案由当前角色和通道生成，并经过现有 Reply 解析和现实边界检查。

### 9.3 防止“生活播报机器人”

- 同一天最多若干条自我分享，默认低于用户消息频率；
- 同一事件在同一通道只分享一次；跨通道是否复用由各通道自己的冷却和重复检查决定；
- 用户连续不回应时降低分享优先级，不连续换模板轰炸；
- 分享允许是一个短片段，不要求以问题结尾；
- 不要把每个活动开始/结束都变成消息；活动多数只改变内部状态。

### 9.4 用户追问时

如果用户主动问角色在做什么，当前 `ScheduleContext` 可以提供真实的虚拟状态和来源锚点。回答应：

- 说明当前模拟活动的自然概括；
- 不泄漏 `plan_id`、`item_id`、内部状态标签；
- 不把模拟活动说成现实世界可验证行动；
- 用户追问精确外部事实时，回到现实边界并诚实说明范围。

---

## 10. 主动候选与已有 QQ/pet Gate 的接线

### 10.1 日程模块只做“内容来源层”

目标结构：

```text
ScheduleEngine / CuriosityEngine
  → ProactiveIntent(source="virtual_schedule" | "user_curiosity")
  → QQProactiveAdvisor 或 pet 主动候选聚合
  → channel-specific ProactiveGate
  → 生成
  → 发送成功
  → channel-specific feedback / delivery log
```

日程模块不得直接调用 `bot.send_private_msg()`、`PetServer.speak()` 或绕过 `ProactiveGate`。

### 10.2 QQ 与桌宠独立

共享：

- 同一个角色卡、关系状态和可追溯事件来源；
- 同一个活动语义和同一个 `candidate_id` 生成规则。

不共享：

- 主动冷却；
- 每日额度；
- 用户回复率；
- 免打扰状态的通道实现；
- 发送失败计数。

`QQ_PROACTIVE_SPEC` 中已有的 QQ 五维 readiness、睡眠/忙碌状态和延迟发送继续有效。虚拟日程只是新增 `virtual_schedule` 与 `user_curiosity` 来源，不替换 QQ Gate。

### 10.3 普通用户回复与主动候选的优先级

用户新消息到达时：

1. 作废尚未发送的同通道主动候选；
2. 先处理普通回复；
3. 不在同轮追加日程分享或问题；
4. 发送成功后再记录用户活动和候选反馈。

这保证“角色有自己的生活”不会变成“角色不听用户说话”。

---

## 11. 持久化模型

日程是长期连续的内在状态，不能只放在进程内存。建议扩展现有 SQLite `MemoryStore`，不把活动文本塞进普通记忆层。

### 11.1 `virtual_day_plans`

```sql
CREATE TABLE virtual_day_plans (
    id TEXT PRIMARY KEY,
    role_id TEXT NOT NULL,
    local_date TEXT NOT NULL,
    timezone TEXT NOT NULL,
    day_type TEXT NOT NULL,
    day_context_json TEXT NOT NULL,
    seed TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(role_id, local_date, revision)
);
```

### 11.2 `virtual_schedule_items`

```sql
CREATE TABLE virtual_schedule_items (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    category TEXT NOT NULL,
    activity_key TEXT NOT NULL,
    label TEXT NOT NULL,
    planned_start TEXT NOT NULL,
    planned_end TEXT NOT NULL,
    actual_start TEXT,
    actual_end TEXT,
    status TEXT NOT NULL,
    completion_basis TEXT,
    required INTEGER NOT NULL DEFAULT 0,
    interaction_profile TEXT NOT NULL,
    share_policy TEXT NOT NULL,
    deviation_json TEXT NOT NULL,
    source_rule_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES virtual_day_plans(id)
);
```

### 11.3 `virtual_life_events`

```sql
CREATE TABLE virtual_life_events (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    item_id TEXT,
    event_kind TEXT NOT NULL,
    truth_class TEXT NOT NULL,
    summary TEXT NOT NULL,
    reason TEXT NOT NULL,
    source_rule_id TEXT,
    occurred_at TEXT NOT NULL,
    share_policy TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES virtual_day_plans(id)
);
```

### 11.4 `user_info_gaps`

```sql
CREATE TABLE user_info_gaps (
    id TEXT PRIMARY KEY,
    topic_key TEXT NOT NULL,
    reason TEXT NOT NULL,
    value REAL NOT NULL,
    confidence REAL NOT NULL,
    sensitivity TEXT NOT NULL,
    status TEXT NOT NULL,
    source_message_ids_json TEXT NOT NULL,
    last_asked_at TEXT,
    ask_count INTEGER NOT NULL DEFAULT 0,
    cooldown_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 11.5 发送审计

已有 `proactive_feedback` 继续作为交付结果记录；需要确保它能区分：

```text
channel
source = virtual_schedule | user_curiosity | ritual | shared_episode | attention
candidate_id
source_event_id
source_message_id
sent_at
responded / dismissed / interrupted
```

`source_event_id` 不能填入 `source_message_id`，`source_memory_id` 不能冒充原始消息 ID。来源链必须能从主动消息回到活动事件或用户消息。

### 11.6 角色与用户隔离

- `role_id + local_date` 是计划唯一作用域；切换角色不能复用另一角色的日计划。
- 用户信息缺口按当前用户/关系 profile 作用域保存。
- 日程事件默认只属于角色自身；不进入跨角色共同记忆。
- 删除角色或重置虚拟生活时，只删除对应 `role_id` 的计划、活动和虚拟事件，不删除用户事实与共同事件。

---

## 12. 时间、重启和异常

### 12.1 时间处理

- 所有计划使用 `zoneinfo.ZoneInfo(timezone)`；禁止用无时区 `datetime` 作为持久化真值。
- `local_date` 由角色日程时区计算，不直接使用服务器 UTC 日期。
- 夏令时/跨午夜由时区库处理；活动比较使用带时区的时间戳。
- 测试必须注入 `Clock`，不依赖运行机器当前时间。

### 12.2 重启恢复

启动时：

1. 读取当前角色当天最新 `active` 计划；
2. 校验角色卡 `schedule_schema_version` 和 plan digest；
3. 若计划可用，继续消费，不重新随机；
4. 对跨越的活动执行一次 reconcile；
5. 不能确认执行的活动写 `expired_unknown`，生成内部事件但不自动发送；
6. 计划损坏时创建新 revision，并保留旧计划供审计。

### 12.3 LLM、数据库和时间异常

| 故障 | 回退 |
|---|---|
| 计划配置非法 | 关闭日程消费，普通对话继续 |
| 当日计划生成失败 | 使用校验通过的上一版；没有则使用空计划 |
| LLM 分享生成失败 | 不分享或使用来源明确的短模板，不影响普通回复 |
| LLM 好奇问题生成失败 | 不提问，不生成无来源问题 |
| 数据库写入失败 | 不提交活动状态和主动额度，记录日志 |
| 时钟/时区异常 | 使用普通通道上下文，禁止主动日程消息 |
| QQ/pet 发送失败 | 不提交 delivery、cooldown、uses 或反馈“已发送” |

---

## 13. 设置页设计

所有用户可调整的运行配置都必须进入桌宠设置页；角色卡结构化内容和运行开关分开。

### 13.1 可调整项

| 设置 | 控件 | 说明 |
|---|---|---|
| 虚拟日程 | 下拉：开启/关闭 | 关闭后不生成计划，但普通对话不受影响 |
| 日程时区 | 受控时区下拉 | 默认跟随系统；不允许自由拼写时区 |
| 当日 profile 覆盖 | 下拉：自动/角色默认/休息类/恢复类/自定义 | 只影响当天或明确的覆盖周期 |
| 计划变体强度 | 下拉：稳定/适中/自由 | 映射到角色允许的偏离预算 |
| 虚拟生活主动分享 | 下拉：关闭/低频/标准 | 独立于 QQ/pet 主动开关 |
| 主动了解用户 | 下拉：关闭/低频/标准 | 独立的问题预算与敏感性 Gate |
| 每日自我分享上限 | 下拉 | 只修改日程来源分享额度 |
| 当日计划状态 | 只读列表 | 显示计划、活动状态、偏离原因和虚拟证据类型 |
| 候选来源 | 只读审计 | 显示主动消息回指的活动事件或用户消息 |

### 13.2 设置链验收

每个字段必须完成：

```text
设置页控件
  → renderer payload
  → preload IPC
  → PetServer save_config 白名单
  → 本地 YAML
  → 重启后 get_config
  → ScheduleEngine 消费
```

只有 UI 显示而后端不保存/不消费，不能判为完成。路径类设置若以后增加角色卡目录，必须使用原生文件夹浏览框；枚举值不能让用户手填。

---

## 14. 实现分期

### Phase S0：契约和只读状态

- 新建 `docs/VIRTUAL_SCHEDULE_SPEC.md`。
- 定义 `ScheduleOutline`、`DayContext`、`DayPlan`、`ScheduleContext`、`ProactiveIntent`。
- 仅提供只读的当前活动计算，不影响发送。
- 用合成角色卡测试，不读取生产日程或用户图片。

### Phase S1：确定性日计划

建议目标文件：

- `src/veranima/core/virtual_schedule.py`
- `src/veranima/memory/schema.py`
- `src/veranima/memory/store.py`
- `src/veranima/core/character.py`
- `tests/test_virtual_schedule.py`

实现：

- 角色卡结构化读取与校验；
- day profile；
- 稳定 seed；
- 计划生成、版本、重启恢复；
- 活动生命周期和 `expired_unknown`；
- SQLite 迁移。

### Phase S2：当前活动到回复节奏

建议目标文件：

- `src/veranima/core/prompts.py`
- `src/veranima/core/agent.py`
- `src/veranima/core/reply.py`（仅在协议需要时）
- `tests/test_schedule_prompt_wiring.py`

实现：

- `ScheduleContext` 注入；
- 交互画像约束；
- 认真问题覆盖忙碌短回；
- prompt 元数据不泄漏；
- QQ/TTS 使用同一语义上下文、不同通道渲染。

### Phase S3：主动分享与主动了解

建议目标文件：

- `src/veranima/core/virtual_schedule.py`
- `src/veranima/core/qq_advisor.py`
- `src/veranima/core/qq_proactive.py`
- `src/veranima/core/ambient.py`
- `src/veranima/core/agent.py`
- `tests/test_proactive_schedule_source.py`

实现：

- `UserInfoGap` 生命周期；
- `SelfShareCandidate` / `CuriosityCandidate`；
- source anchor；
- 与现有 QQ/pet Gate 接线；
- 忽略、不回答和发送失败的降级；
- 不同通道独立 cooldown/feedback。

### Phase S4：设置页与可见审计

建议目标文件：

- `src/veranima/pet_server.py`
- `pet/preload.js`
- `pet/main.js`
- `pet/settings.html`
- `pet/settings-renderer.js`
- `tests/test_pet_schedule_settings.py`

实现：

- 运行开关、day profile、变体强度、时区、分享/好奇预算；
- 今日计划只读查看；
- 活动状态和来源审计；
- 保存后重启恢复验证。

### Phase S5：真实链路验收

- CLI 先用固定时钟验证：计划 → 当前活动 → prompt → Reply。
- 再用临时 SQLite、真实远程 API 做连续对话验收；测试日志不得写 API key、用户图片或内部协议。
- QQ 和桌宠分别验证主动 Gate、发送成功后的反馈和失败回滚。
- 真实 Electron/TTS 行为另列“实机已验证”，不能用 pytest 代替。

---

## 15. 行为级测试契约

### 15.1 计划生成

1. 角色卡没有结构化日程时，模块关闭，不出现默认具体活动。
2. 同一角色、同一天、同一条件、同一 revision 重启后计划完全一致。
3. 不同 day profile 只使用各自允许的活动块。
4. 计划不会产生重叠、越界时长或非法类别。
5. 角色允许偏离时，偏离操作仍受活动块白名单和每日上限约束。
6. 角色不允许偏离时，随机种子变化不能改变计划结构。
7. 进程跨时间恢复时，无法证明完成的活动进入 `expired_unknown`，不能伪造 `completed`。

### 15.2 回复表现

1. 当前活动为 `occupied_brief` 时，普通闲聊的 prompt 出现短回资源约束，最终用户可见文本没有协议标记。
2. 同一活动期间，认真问题仍得到核心回答，不能只返回“现在忙”。
3. 当前活动切换为 `available_normal` 后，短回约束消失。
4. `sleep_like` 禁止主动候选，但用户主动发消息仍能正常处理。
5. 日程活动不被每轮自动播报；没有可分享事件时不出现无来源自我叙述。

### 15.3 主动了解

1. 已有明确答案的主题不再生成同一信息缺口。
2. 同一主题在冷却期内不重复提问。
3. 用户明确拒绝后，缺口进入 `paused/declined`，不会惩罚关系或立即换一个相似问题追问。
4. 当前活动或用户状态禁止提问时，候选被抑制。
5. 候选缺少 `source_message_id` 或合法 `reason` 时 fail-closed。

### 15.4 主动分享

1. 没有 `virtual_life_event` 或角色卡稳定锚点时，不生成“我刚刚做了某事”。
2. 分享候选能回指 `event_id → item_id → plan_id → rule_id`。
3. 同一通道同一事件只发送一次；发送失败不提交已发送记录。
4. QQ 成功发送不更新 pet cooldown；pet 成功发送不更新 QQ cooldown。
5. 用户新消息到达时，未发送的同通道候选作废，不与普通回复双连发。
6. 角色分享不写入 `shared_episode`，除非用户后来明确与之形成真实共同对话事件。

### 15.5 真实性与泄漏

1. 生产 prompt 可以包含内部日程上下文，但用户可见回复不能包含 `plan_id`、`item_id`、`truth_class`、`source_anchor`、`candidate_id`。
2. 用户追问“你刚刚做的事是否真实发生”时，回复遵守角色身份与现实行动边界，不继续编造证据。
3. 角色切换后不读取旧角色的虚拟日程和虚拟事件。
4. 计划、活动、用户信息缺口和主动反馈重启后仍能正确关联。

---

## 16. 用户体验验收标准

实现完成后，不能只看“出现了一个 scheduler 类”。至少应通过以下体验级验收：

- 连续数天观察，角色的日程有稳定骨架，但不是每天复制同一时间表。
- 日程变化能解释当天的不同状态；偏离有原因，不是随机抽风。
- 角色在不同活动阶段的回复节奏有差异：忙时短而明确，空档更容易展开，过渡期不稳定但不表演。
- 角色会自然分享少量自己的虚拟生活，但不会变成定时播报器。
- 角色会因为具体的信息缺口想了解用户，问题有理由、有冷却、被拒绝后会停止施压。
- 用户认真输入时，当前日程不会成为敷衍用户的借口。
- 主动消息可以解释为什么现在发；没有来源时不发。
- QQ 与桌宠都像同一个角色，但两端主动额度、冷却、表达媒介和失败反馈独立。
- 重启、跨日、切换角色、LLM 失败和发送失败不会制造虚假的完成记录或重复主动消息。

---

## 17. 与现有设计的关系和状态矩阵

| 设计项 | 处理方式 |
|---|---|
| `QQ_PROACTIVE_SPEC` 的 QQ readiness / Gate / 通道隔离 | 保留；新增日程和好奇作为来源，不替换 Gate |
| `PERSONA_LOOP_SPEC` 的角色核心与关系状态 | 保留；日程只读取慢变量和短期状态，不覆盖角色核心 |
| `MEMORY_SPEC` 的用户事实与共同事件 | 保留；虚拟生活事件与共同记忆分离 |
| `VISION_SPEC` 的观察与 pet 主动 | 保留；视觉事件不能直接改写日程完成结果 |
| `GreetingScheduler` / `MealReminderScheduler` | 迁移为低优先级 `ritual` 来源；不能继续充当完整生活模块 |
| `SceneLock` | 保留为用户侧场景；与角色自己的 `ScheduleContext` 并列，不混为同一状态 |
| `Agent.heartbeat()` / `late_reply()` | 保留兼容入口，逐步改为消费统一 `ProactiveIntent` |
| 当前 `virtual_life` / `daily_state` 字符串 | 保留兼容；结构化日程优先，旧文字只作角色背景摘要 |

当前实现状态：

- 结构化角色日程：未实现；
- 当日计划生成：未实现；
- 当前活动影响回复：未实现；
- 来源可追溯的虚拟活动事件：未实现；
- 主动了解信息缺口：现有 QQ 时机/记忆素材不等于完整实现；
- 主动分享自身：现有问候、心跳、离线思考不等于完整实现；
- QQ/pet 独立 Gate：已有基础，接入日程来源后需补行为测试；
- 设置页日程控制：未实现；
- 本规范本身：已形成设计契约，后续按 S0→S5 实施。

结论：本模块的成功标准不是“定时发送更多消息”，而是让角色拥有一个可持续、可解释、可中断、可恢复、不会冒充现实经历的虚拟生活状态，并让这套状态真正影响回复资源分配、主动了解和主动分享的来源链。
