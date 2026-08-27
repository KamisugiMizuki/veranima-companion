# 虚拟日程与生活主动性模块设计规范

> 版本：v1.2
>
> 状态：实现中 v1.2；S0-S2 核心链路和 S5 设置链已实现，S3/S4 的自传、offset/effective_span、完整分享与好奇仍为部分实现。
>
> 范围：Veranima 角色的虚拟日程、日常活动状态、当前活动对回复表现的影响，以及由此产生的主动了解与主动分享候选。
>
> 关联规范：`DESIGN.md`、`PERSONA_LOOP_SPEC.md`、`MEMORY_SPEC.md`、`QQ_PROACTIVE_SPEC.md`、`VISION_SPEC.md`。
>
> 重要边界：本规范设计的是**角色世界中的持续模拟状态**，不是让程序声称在现实世界中真实上课、通勤、购物、见人或完成了外部行动。所有活动、事件和来源都必须携带虚拟模拟的证据类型；它们可以成为角色化表达的素材，但不能伪装成现实行动证据或用户与角色共同发生过的事实。

> v1.1 变更重点：次日计划在前一日日程进入睡眠状态后生成；加入睡眠/起床交互生命周期、昼夜节律基线、作息偏移惯性与渐进回正、活动切换告知，以及独立的日程设置页和角色目录内模板文件。

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

`virtual_life_event` 可以被角色分享，但其 `truth_class` 必须保持 `virtual_simulation`。它不能提供“我在现实中完成了某事”的证据，也不能作为“我们一起做过某事”的依据。虚拟事件可以进入独立的角色自传归档，但不进入普通 `shared_episode` 或用户事实层。

### 3.2 核心对象

- **`ScheduleOutline`**：角色卡定义的长期日程大纲和可变规则。
- **`DayContext`**：某个本地日期的条件，如普通日、休息日、节假日、长假、恢复日或手动覆盖；只描述条件，不包含具体生活文案。
- **`DayPlan`**：由大纲、当天条件和角色状态生成的一份具体虚拟计划。
- **`ScheduleItem`**：计划中的一个时间段活动。
- **`ScheduleEvent`**：活动开始、完成、跳过、偏离、未完成或内部想法等可分享事件。
- **`DayCloseSummary`**：入睡后对当日模拟状态的收尾快照，是次日计划和角色自传归档的输入。
- **`SleepMessageArchive`**：睡眠窗口内收到的消息元数据及受既有策略保护的原始消息引用，用于起床衔接。
- **`ScheduleOffset` / `SleepDebt`**：分别表示作息时间位置和睡眠不足造成的疲劳负担。
- **`EffectiveSpan`**：活动扣除中断后的有效投入跨度。
- **`AutobiographicalContext`**：虚拟事件的可回顾摘要，不等同于共同记忆。
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

### 4.2 角色目录内的模板文件

每个角色的日程模板必须与角色卡一起存放，不能使用项目级共享模板覆盖角色差异：

```text
characters/<role_id>/
├── character.json
├── card.md
└── virtual_schedule.json
```

`character.json` 的 `extensions.veranima.virtual_schedule` 可以作为内嵌兼容格式；当角色目录存在 `virtual_schedule.json` 时，文件作为日程模板的权威来源。两者同时存在且内容不一致时，启动时拒绝日程消费并提示冲突，不静默选择其中一份。模板文件只允许被角色加载器按当前 `role_id` 读取，切换角色必须切换日程作用域。

`virtual_schedule.json` 保存长期模板、昼夜节律、活动块、交互画像和偏离规则，不保存某一天的计划实例。日计划实例进入 SQLite 或独立运行数据目录，不能回写角色设计文件。

### 4.3 昼夜节律是日程基线

模板必须声明角色的作息基线，而不是把“昼伏夜出”当成普通日的临时偏移：

```json
{
  "circadian": {
    "wake_window": {"start": "07:00", "end": "09:00"},
    "sleep_window": {"start": "22:00", "end": "00:00"},
    "preferred_activity_periods": ["day", "evening"],
    "chronotype": "day_aligned",
    "recovery_rate_minutes_per_day": 20
  }
}
```

允许的 `chronotype` 由受控枚举决定：`day_aligned`、`evening_aligned`、`night_aligned`、`irregular`。具体时间仍由模板填写。`night_aligned` 的睡眠窗口可以跨越白天；计划生成器据此计算本地日期边界和活动阶段，不能简单假设睡眠一定发生在午夜前后。

昼夜节律由角色模板定义，日程 LLM 不能在生成次日计划时擅自把白天角色改成夜行角色。短期熬夜、补觉或高强度活动只产生有限的 `schedule_offset`，不改变 `chronotype`。

实现时不要求用户填写原始 JSON。角色卡编辑器应将它拆成结构化表单；在当前没有角色编辑器前，日程专属设置页提供模板状态、作息摘要和运行开关，模板原文只读，不把配置错误伪装成 UI 已支持。

### 4.4 活动块字段规则

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

### 5.2 生成时机：前一日入睡后生成次日计划

次日计划不是在每次启动、每次 tick 或次日第一次对话时随机生成，而是在前一日日程进入 `sleeping` 状态后生成：

```text
当日最后一个可交流活动结束
  → 进入入睡准备/睡眠状态
  → 固化当日收尾摘要与未完成项
  → 以角色模板 + 当日上下文 + 收尾摘要调用日程 LLM
  → 结构化校验与确定性修正
  → 持久化次日 DayPlan(draft)
  → 到达次日起床阶段后转 active
```

睡眠状态是生成触发条件，不是“时间到就默认睡着”。如果角色因熬夜继续保持清醒，次日计划生成推迟；如果进程在应生成时离线，启动恢复时只允许补做一次生成，不允许因为多个 tick 重复调用 LLM。

计划生成输入包括：

- 当前角色模板与模板版本；
- 次日 `DayContext` 与允许的 day profile；
- 当日计划的完成/跳过/中断/偏离摘要；
- 当前虚拟精力、情绪惯性、社交消耗和未完成事项；
- 当前作息偏移量及角色回正速率；
- 角色允许的活动块与偏离操作。

计划 LLM 输出只能从输入中选择和调整活动，不能新增活动类型、现实地点、现实人物或未经模板允许的时间段。输出无效、超时或不可用时，使用同一输入的确定性模板回退；回退仍必须持久化版本和来源。

### 5.3 LLM 结构化输出契约

计划生成使用单次低成本结构化调用，输出只表达“明天怎么安排”，不输出可见台词：

```json
{
  "day_profile": "profile_id_from_template",
  "wake_shift_minutes": 20,
  "items": [
    {
      "rule_id": "template_block_id",
      "activity_key": "template_activity_key",
      "shift_minutes": 10,
      "duration_minutes": 45,
      "operation": "shift|resize|substitute|skip_optional|recovery_mode|none",
      "reason_code": "late_sleep|high_load|day_profile|unfinished|character_preference|none"
    }
  ],
  "expected_deviations": 1
}
```

程序层强制校验：

1. `day_profile` 必须属于模板；
2. `rule_id`、`activity_key` 必须来自模板；
3. 时间偏移、持续时间、偏离次数必须在模板上限内；
4. `required` 活动不能被 `skip_optional` 删除；
5. `reason_code` 必须属于受控枚举；
6. 结构化结果不能包含现实活动声明或来源外的用户事实；
7. 校验失败时整份输出拒绝，不能部分接受后留下半份计划。

LLM 负责有限选择和表达“为什么这样调整”，代码负责时间、状态、作用域、上限、持久化和生命周期。

### 5.4 基础生成顺序

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

### 5.5 稳定种子、LLM 版本与幂等

同一 `role_id + local_date + plan_revision + day_context_digest` 必须得到同一份基础计划。推荐：

```text
seed = SHA256(role_id + local_date + plan_revision + day_context_digest)
```

进程重启、设置页刷新和 QQ/pet 两端读取不能让计划重新抽样。用户主动修改当日条件时创建新的 `plan_revision`，旧计划保留审计但不再作为当前计划。

### 5.6 计划偏移、弹性压缩与渐进回正

计划时间不能只做整日平移。先计算角色从模板基线产生的 `schedule_offset`，再按以下顺序调和：

1. **偏移继承**：前一日入睡/起床相对模板的偏移进入次日输入；
2. **弹性压缩**：可用时间不足时，按角色卡的活动价值排序优先缩短或跳过低优先级、非必需活动；高价值活动保持窗口和最短时长；
3. **延迟传播**：起床推迟会向后传播到受影响活动，直到遇到不可移动窗口；
4. **边界迁移**：跨越睡眠窗口的活动必须迁移到下一个允许窗口，不能与睡眠硬重叠；
5. **渐进回正**：每日最多向模板基线移动 `recovery_rate_minutes_per_day`，由角色模板的自律/回正参数决定；
6. **新偏移叠加**：当日再次熬夜、过早入睡或高负荷活动可增加偏移，但受最大偏移和最大连续天数限制。

偏移状态至少包含：

```text
schedule_offset_minutes
offset_reason: late_sleep | early_sleep | oversleep | high_load |
               recovery | holiday_choice | character_deviation
offset_started_at
target_baseline_at
recovery_rate_minutes_per_day
```

连续恢复日不应每次完全重生成不同作息；同一偏移链要有稳定的 `offset_id` 和前后引用。恢复过程只改变日程时间与交互资源，不永久改变角色的昼夜节律。

### 5.7 生活主题、情绪残留与环境氛围

在日计划之上增加三个轻量层，但都不能绕过模板和真实性边界：

- `LifeTheme`：跨数日的角色生活主题，决定活动池的变体偏好和偏离解释；主题由角色模板、已完成虚拟活动和有限状态生成，不是每轮随机换题。
- `EmotionalResidue`：活动结束后保留有限时长的成就感、疲惫、社交透支、无聊或专注残留，作为下一个活动的软滤镜；不能直接覆盖 `AgentState` 的核心情绪。
- `AmbientContext`：由模板和计划事件定义的虚拟空间/感官氛围，如安静、嘈杂、光线、天气意象或声音类型；不是 GPS、截图或现实传感器事实。

`AmbientContext` 不等于角色当前位置。角色长期生活范围、稳定场所、活动地点选择、移动路线和 `CurrentScene` 的规范性契约见 [`VIRTUAL_SPACE_SPEC.md`](VIRTUAL_SPACE_SPEC.md)。角色卡中的固定 `scenario` 只能作为世界范围和高频空间锚点，不能覆盖有来源的当前地点。

```text
LifeTheme → activity variant / deviation reason
EmotionalResidue → availability / reply tempo / expansion budget
AmbientContext → 可选自我分享素材和联想提示
```

三个层都必须有过期时间。它们只进入 `ScheduleContext` 的最小摘要，不把完整内部标签发送给用户；没有合适素材时不分享。

### 5.8 用户影响与互动期望窗口

用户可以在不修改设置的情况下影响角色的次日日程，但影响必须是**隐性、有限、可解释**的：

- 用户最近明确表达的作息、状态或共同项目时间约束，可以改变角色计划中的“留出交流窗口”或 follow-up 优先级；
- 用户连续深夜来消息，可以提高角色对“是否留出短交流窗口”的考虑，但不能自动把角色改成昼伏夜出；
- 角色卡的关系期许可以定义 `interaction_expectation_windows`，用户未出现只产生低强度情绪残留，不产生责备、惩罚或负面关系记忆；
- 用户信息不足、来源过旧或敏感信息未获同意时，不影响日计划。

用户影响必须记录 `source_message_ids`、影响类型、权重、起止时间和是否被用户纠正。它不能伪造“用户答应了会出现”，也不能作为主动发送许可。
### 5.9 允许的变体操作

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

## 6. 睡眠、起床与不可交流活动的交互生命周期

### 6.1 活动交互影响分级

不是每个活动都需要打断对话。模板中的 `interaction_impact` 受控为：

```text
none / mild / inconvenient / unavailable
```

- `none`：只影响内部氛围，不主动告知。
- `mild`：回复资源轻微下降，用户问及时可简短说明。
- `inconvenient`：进入前允许发送一次状态预告；活动中普通回复缩短或延迟；认真问题仍先给最小可用回答。
- `unavailable`：进入睡眠或明确不可交流状态后，完全停止该角色的普通回复，直到恢复阶段；未发送消息不伪造已读或已回复。

`unavailable` 只能由模板允许的睡眠/明确不可交流活动触发，不能由普通“忙碌”标签直接升级。

### 6.2 睡前告知与延长清醒窗口

当计划进入 `sleep_window` 的准备阶段，且角色尚未进入睡眠，系统可以生成一次用户可见告知：

```text
活动即将进入 unavailable
  → 发送一次“准备休息/即将离开”的状态说明
  → 开启 grace_period
  → 期间最多处理有限轮消息
  → 每轮使用 drowsy interaction profile
  → grace_period 到期或角色无法继续
  → 发送最后告知
  → 强制进入 sleeping
```

`grace_period` 默认 30 分钟，由模板限制在 0～60 分钟；它不是用户无限挽留的许可。用户持续发消息可以在窗口内延长实际入睡时间，但每次延长必须消耗 `sleep_debt`，并受 `max_extension_minutes` 限制。达到上限后，角色必须进入睡眠，不再继续对话。

睡前告知内容由角色和当前渠道生成，但必须明确表达：接下来到起床前不会继续回复。系统不要求固定某一句台词，禁止把示例文本硬编码为模板。

### 6.3 困倦交互画像

`drowsy` 是角色卡定义的交互画像，不是随机错误注入器：

```json
{
  "reply_style": "drowsy",
  "max_sentences": 2,
  "question_budget": 0,
  "expansion_budget": "minimal",
  "latency_range_seconds": [2, 12],
  "allow_typo": true,
  "emotion_range": "muted",
  "max_extension_minutes": 30
}
```

实际表现可以包括回复变慢、句子变短、情感振幅降低、偶发轻微错字或自我修正。错字必须低频、有原因、可恢复，不能影响紧急信息理解；程序不能通过每轮随机插错字来制造“像人”。认真/紧急消息仍优先处理核心内容，不能用困倦状态掩盖安全或重要问题。

### 6.4 sleeping 状态与消息处理

进入 `sleeping` 后：

- QQ 和桌宠的普通输入都不触发 LLM 回复；
- 消息仍可按产品隐私策略记录“睡眠期间收到消息”的元数据，正文是否保存遵循既有消息保留策略；
- 不发送“我看到了”“我正在睡”等自动回复；
- 该状态不产生用户负面记忆、不惩罚关系、不调用主动 Gate。

起床进入 `waking` 后，系统可在第一次处理用户消息时合并两类信息：

1. 角色恢复后的自然回应；
2. 对睡眠期间收到消息的轻量衔接，例如提及“刚醒，看到你睡前后发过几条消息”。

只有消息实际在睡眠窗口内到达，才允许这样衔接；不把消息数量、内容或时间夸张成角色一定“读完了”。未处理内容过多时，按时间顺序截断并保留未处理标记。

### 6.5 其他不便交流活动

学习、专注工作、外出过渡或其他活动是否需要告知，由模板的 `interaction_impact` 决定。进入前预告和结束后说明各自每日最多一次、同一活动只发一次；活动中不重复播报。

活动开始告知必须说明“接下来可能回复较短/较慢”，活动结束可以说明“恢复正常交流”。如果活动被跳过、提前结束或被用户消息打断，状态说明必须跟随实际模拟事件，不能坚持发送过期预告。

---

## 7. 日程结束、收尾摘要与次日生成

### 7.1 入睡是日程边界，不是服务器日期边界

日程日由角色的 `timezone` 和 `sleep_window` 定义。对于跨白天睡眠或跨午夜活动，`local_date` 仍以角色日程周期计算，不按服务器午夜强行切断。进入 `sleeping` 后才执行当日收尾和次日计划生成。

### 7.2 日程收尾摘要

进入睡眠后先固化 `DayCloseSummary`：

```text
completed_items
skipped_items
interrupted_items
expired_unknown_items
schedule_offset_minutes
sleep_debt_minutes
emotional_residue
life_theme_progress
unfinished_thoughts
user_influence_summary
```

摘要只引用当日已生成的虚拟事件、角色状态变化和用户明确消息；不把程序离线期间的活动标成 completed。它作为下一次日程 LLM 的输入，但不直接写入 `shared_episode`。

### 7.3 次日计划生成状态机

```text
awake/active
  → sleep_preparing
  → sleeping
  → day_close_committed
  → next_day_plan_generating
  → next_day_plan_ready
  → next_day_plan_active
```

`next_day_plan_generating` 使用稳定的 `generation_key = role_id + next_local_date + source_day_close_id + template_version` 做幂等锁。LLM 调用超时或失败不重复生成多个版本；转为确定性回退或保留待生成状态，并在恢复后重试一次。

### 7.4 日程 LLM 的结构化输出边界

日程 LLM 可以根据模板和收尾摘要选择活动变体、调整窗口、排序有限偏离，并生成每个调整的 `reason_code`。它不能：

- 自由添加模板没有的活动；
- 把角色卡的昼夜节律改成另一种基线；
- 把模拟活动说成现实行动；
- 把用户消息改写成用户承诺；
- 宣称离线期间完成了未确认活动。

结构校验、范围限制、活动冲突处理和最终持久化仍由程序完成。

---

## 8. 作息偏移、睡眠债务与回正

### 8.1 两个正交状态量

`schedule_offset` 和 `sleep_debt` 不能合并成一个“疲劳/偏移”数：

- `schedule_offset_minutes`：相对于模板作息基线的时间位置偏移，回答“现在处于周期的什么位置”；
- `sleep_debt_minutes`：相对于角色需要睡眠量的累计缺口，回答“当前有多累”。

同样的时间偏移可能来自不同原因，必须保留 `offset_reason`：主动晚睡、被动失眠、早睡、过度活动、补觉或节假日选择。原因不同，次日活动压缩、回复情绪和可分享素材不同。

### 8.2 睡眠债务模型

每个睡眠周期结束后，按模板的 `target_sleep_minutes`、实际模拟睡眠时长和额外清醒延长计算：

```text
sleep_debt_next = clamp(
  sleep_debt_previous
  + target_sleep_minutes - simulated_sleep_minutes
  + sleep_extension_minutes * extension_cost,
  0,
  max_sleep_debt_minutes
)
```

恢复性睡眠、补觉和低负荷日可以减少债务；减少速度由模板的 `debt_recovery_minutes_per_day` 限制，不能一觉把任意高债务清零。`SleepDebt` 影响虚拟精力、活动压缩顺序、`drowsy` 触发阈值和分享内容权重，但不直接覆盖 `AgentState.energy`；两者通过明确的 `schedule_context` 修正项连接。

`early_sleep` 不等于“睡得更好”：如果早睡导致计划提前结束，可减少债务；如果是因高强度活动被迫提前结束，则同时记录 `high_load` 残留。`late_sleep` 与 `insomnia_like` 也必须区分，前者是活动/选择造成的清醒延长，后者是无法入睡的模拟状态，不应套用同一种角色叙事。

### 8.3 偏移传播和回正


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

### 8.4 `ScheduleItem` 状态

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

### 8.5 活动事件与有效投入跨度

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

活动事件还必须维护 `effective_span`，而不是只记录计划开始和结束：

```text
effective_span = max(0, active_wall_time - sum(interruption_spans))
```

`interrupted` 表示活动暂停，期间不计入有效投入；不允许把角色回复用户的 20 分钟自动算作后台继续活动。恢复时创建新的 active span，并通过 `parent_event_id` 关联同一活动。`DayCloseSummary` 至少保存计划时长、有效投入时长、中断次数和中断总时长，供角色形成“今天忙了很久但实际推进有限”的自我评价素材。

以下事件类型必须在事件对象中区分：

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

## 9. 当前活动如何影响对话表现

### 9.1 核心原则

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

### 9.2 `ScheduleContext`

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

### 9.3 交互画像

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

### 9.4 Prompt 接线

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

## 10. 主动了解用户：`UserInfoGap` 与 `CuriosityCandidate`

### 10.1 不是随机问题库

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

### 10.2 询问规则

生成 `CuriosityCandidate` 前按顺序检查：

1. 主题仍为 `open`，且没有近期已知答案。
2. `sensitivity` 不高于当前关系阶段允许的等级。
3. 最近没有问过相同或语义重复的问题。
4. 当前用户没有表达忙碌、低落、睡眠或明确免打扰。
5. 当前活动的 `curiosity_allowed=true`。
6. 当前通道和全局主动 Gate 允许。
7. 该问题可以用一句自然理由解释“为什么现在想到这个”。

默认每个通道每日最多一个主动了解问题；同一主题至少经过一个可配置冷却周期才能再次询问。用户不回答、拒绝或转移话题时，状态改为 `paused` 或降低优先级，不施加关系惩罚、不追问、不把拒绝写成负面人格证据。

### 10.3 用户回答后的更新

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

## 11. 主动分享自己：`ScheduleEvent` 到 `SelfShareCandidate`

### 11.1 可分享素材来源

主动分享的优先级：

1. 当前或刚结束的、`share_policy` 允许的虚拟活动事件；
2. 活动偏离及其原因，例如计划被压缩、替换或暂缓；
3. 角色长期兴趣在当前活动中产生的一个内部想法；
4. 未完成但仍有效的虚拟思路；
5. 角色状态的低负担变化。

所有候选必须能回指 `virtual_life_event` 或角色卡稳定字段。没有来源时，不生成“我刚刚做了某事”的内容；可以不发，不能用模板填空。

### 11.2 分享候选协议

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

### 11.3 防止“生活播报机器人”

- 同一天最多若干条自我分享，默认低于用户消息频率；
- 同一事件在同一通道只分享一次；跨通道是否复用由各通道自己的冷却和重复检查决定；
- 用户连续不回应时降低分享优先级，不连续换模板轰炸；
- 分享允许是一个短片段，不要求以问题结尾；
- 不要把每个活动开始/结束都变成消息；活动多数只改变内部状态。

### 11.4 用户追问时

如果用户主动问角色在做什么，当前 `ScheduleContext` 可以提供真实的虚拟状态和来源锚点。回答应：

- 说明当前模拟活动的自然概括；
- 不泄漏 `plan_id`、`item_id`、内部状态标签；
- 不把模拟活动说成现实世界可验证行动；
- 用户追问精确外部事实时，回到现实边界并诚实说明范围。

---

## 12. 主动候选与已有 QQ/pet Gate 的接线

### 12.1 日程模块只做“内容来源层”

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

### 12.2 QQ 与桌宠独立

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

### 12.3 普通用户回复与主动候选的优先级

用户新消息到达时：

1. 作废尚未发送的同通道主动候选；
2. 先处理普通回复；
3. 不在同轮追加日程分享或问题；
4. 发送成功后再记录用户活动和候选反馈。

这保证“角色有自己的生活”不会变成“角色不听用户说话”。

---

## 13. 持久化模型

日程是长期连续的内在状态，不能只放在进程内存。建议扩展现有 SQLite `MemoryStore`，不把活动文本塞进普通记忆层。

### 13.1 `virtual_day_plans`

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

### 13.2 `virtual_schedule_items`

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

### 13.3 `virtual_life_events` 与自传归档

`virtual_life_events` 是可回顾的角色自身素材，但必须和普通记忆分开。完成的 DayCloseSummary 可以按 `share_policy` 投影为一个或多个 `virtual_life_event`，而不是直接写进 `episodic/shared_episode`。

```text
DayCloseSummary
  → virtual_life_event(truth_class=virtual_simulation)
  → AutobiographicalContext 查询
  → 用户主动追问或有来源的 self-share
```

事件归档至少保存：

```text
role_id
source_plan_id
source_item_id
source_rule_id
truth_class = virtual_simulation
summary
completion_basis
effective_span_minutes
source_event_ids
created_at
expires_at
```

归档可以保留数日或按角色模板 TTL 过期。查询结果必须带来源锚点和虚拟性质，生成器只能据此表达已存在的摘要；如果用户追问的细节不在归档中，必须承认不记得或这是虚拟日程设定，不得补写具体事实。

### 13.4 `sleep_message_archive`

睡眠期间消息需要独立于正常对话内容的“睡眠窗口归档”：

```sql
CREATE TABLE sleep_message_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id TEXT NOT NULL,
    user_scope TEXT NOT NULL,
    sleep_cycle_id TEXT NOT NULL,
    message_id INTEGER,
    received_at TEXT NOT NULL,
    sender_scope TEXT NOT NULL,
    content_retained INTEGER NOT NULL DEFAULT 0,
    processed_at TEXT,
    UNIQUE(role_id, sleep_cycle_id, message_id)
);
```

默认写入到达时间、发送者作用域和原始 `message_id` 引用，不复制正文。若既有消息保留策略允许正文留在 `messages` 表，起床衔接可以按 `message_id` 受控读取少量未处理消息；日程模块不能另存一份正文。没有正文许可时只能说“睡眠期间收到若干条消息”，不能声称知道具体内容。

睡眠归档按角色、用户和睡眠周期隔离，处理后标记 `processed_at`，并按保留策略清理。它不是 `shared_episode`，也不产生关系惩罚。

### 13.6 `user_info_gaps`

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

### 13.7 发送审计

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

### 13.8 角色与用户隔离

- `role_id + local_date` 是计划唯一作用域；切换角色不能复用另一角色的日计划。
- 用户信息缺口按当前用户/关系 profile 作用域保存。
- 日程事件默认只属于角色自身；不进入跨角色共同记忆。
- 删除角色或重置虚拟生活时，只删除对应 `role_id` 的计划、活动和虚拟事件，不删除用户事实与共同事件。

---

## 14. 时间、重启和异常

### 14.1 时间处理

- 所有计划使用 `zoneinfo.ZoneInfo(timezone)`；禁止用无时区 `datetime` 作为持久化真值。
- `local_date` 由角色日程时区计算，不直接使用服务器 UTC 日期。
- 夏令时/跨午夜由时区库处理；活动比较使用带时区的时间戳。
- 测试必须注入 `Clock`，不依赖运行机器当前时间。

### 14.2 重启恢复

启动时：

1. 读取当前角色当天最新 `active` 计划；
2. 校验角色卡 `schedule_schema_version` 和 plan digest；
3. 若计划可用，继续消费，不重新随机；
4. 对跨越的活动执行一次 reconcile；
5. 不能确认执行的活动写 `expired_unknown`，生成内部事件但不自动发送；
6. 计划损坏时创建新 revision，并保留旧计划供审计。

### 14.3 LLM、数据库和时间异常

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

## 15. 日程专属设置页

日程设置必须是独立页面，不依附“在场与主动”或“记忆与人格”。前者控制消息交付，后者控制信息存储，日程页控制角色模板、作息和当前计划；三者混在一起会产生错误的用户预期。

### 15.1 页面与作用域

导航项固定为 `日程与生活`，独立于 `在场与主动`、`记忆与人格`。页面显示当前角色、角色目录内 `virtual_schedule.json` 的版本/读取状态、当前睡眠周期、当前活动、下一活动、计划偏移和未确认活动。

模板文件属于 `characters/<role_id>/virtual_schedule.json`，与 `character.json` 同目录。模板是角色资产，页面只读展示；普通运行设置写入本地配置覆盖，不回写角色模板。角色切换时按 `role_id` 切换计划作用域。

### 15.2 可调整项

| 设置 | 控件 | 作用域 |
|---|---|---|
| 虚拟日程 | 下拉：开启/关闭 | 全局运行 |
| 日程时区 | 受控时区下拉 | 当前角色 |
| 今日 profile | 下拉：自动/模板默认/休息/恢复/自定义 | 当前日 |
| 计划变体强度 | 下拉：稳定/适中/自由 | 当前日 |
| 睡前 grace period | 下拉：0/15/30/45/60 分钟 | 当前角色 |
| 最大挽留延长 | 下拉：0/15/30 分钟 | 当前睡眠周期 |
| 自我分享 | 下拉：关闭/低频/标准 | 日程来源 |
| 主动了解用户 | 下拉：关闭/低频/标准 | 用户信息缺口来源 |
| 每日状态告知上限 | 下拉 | 当前日 |
| 今日计划、偏移和来源 | 只读 | 当前角色和日期 |

不得要求用户手填活动枚举或时区字符串。以后增加模板编辑器时，角色目录必须使用原生目录浏览器并校验角色包路径。

### 15.3 设置链与语义

```text
日程专属页面 → renderer → preload/main IPC
→ PetServer 白名单 → 本地运行配置 → ScheduleEngine 重启恢复
```

关闭日程只停止计划生成、活动推进和日程来源候选，不关闭普通聊天、记忆或已有 QQ/pet 主动来源。关闭自我分享不停止内部日程，也不影响用户主动询问。提前入睡、强制结束 grace period 和删除当日覆盖必须确认并记录原因。

## 16. 实现分期

### Phase S0：契约和只读状态

- 新建 `docs/VIRTUAL_SCHEDULE_SPEC.md`。
- 定义 `ScheduleOutline`、`DayContext`、`DayPlan`、`ScheduleContext`、`ProactiveIntent`。
- 仅提供只读的当前活动计算，不影响发送。
- 用合成角色卡测试，不读取生产日程或用户图片。

### Phase S1：角色目录模板与确定性状态

- 为每个角色加载 `virtual_schedule.json`；内嵌字段仅作兼容。
- 校验模板版本、昼夜节律、活动块、交互画像和偏离规则。
- 实现 `schedule_state`：awake/sleep_preparing/sleeping/waking/unavailable。
- 以角色时区计算日程周期，支持跨午夜和白天睡眠。
- 对进入/离开睡眠及不便交流活动产生一次性状态告知候选。

### Phase S2：睡眠交互与次日计划

- 睡前 grace period、困倦回复画像、最大挽留延长和 sleep debt。
- 睡眠期间不调用 LLM 回复；记录睡眠期间消息的受控元数据。
- 起床后的首条用户消息接入睡眠期间消息回顾。
- 在日程进入睡眠后固化 DayCloseSummary，并通过一次结构化 LLM 调用生成次日计划。
- generation_key 幂等；LLM 失败使用确定性回退，不生成重复计划。

### Phase S3：偏移、调和与内化

- 继承前一日入睡/起床偏移。
- 弹性压缩、不可移动窗口迁移和高价值活动保留。
- 按角色回正速率逐日恢复，不改变 chronotype 基线。
- 引入 LifeTheme、EmotionalResidue、AmbientContext 的最小过期状态。
- 允许有来源的用户影响和互动期望窗口，不产生责备或伪造用户承诺。

### Phase S4：回复与主动性接线

- 将当前 `ScheduleContext` 注入 Agent 回复 prompt。
- 忙碌时减少普通闲聊资源，但保留认真问题的核心回答。
- 由虚拟活动事件产生 `SelfShareCandidate`；由 `UserInfoGap` 产生 `CuriosityCandidate`。
- 两类候选均进入既有 QQ/pet 独立 Gate，发送成功后才记账。

### Phase S5：独立日程设置页

- 新建独立的“日程与生活”设置页，不并入“在场与主动”或“记忆与人格”。
- 展示当前角色目录模板、昼夜节律、当前周期、今日计划、偏移链和未确认活动。
- 运行项全部使用受控下拉；模板文件和目录使用安全的原生浏览流程。
- 完成 DOM → renderer → preload/main → PetServer → 本地配置 → 重启消费的行为测试。

### Phase S6：真实链路验收

- CLI 先用固定时钟验证：计划 → 当前活动 → prompt → Reply。
- 再用临时 SQLite、真实远程 API 做连续对话验收；测试日志不得写 API key、用户图片或内部协议。
- QQ 和桌宠分别验证主动 Gate、发送成功后的反馈和失败回滚。
- 真实 Electron/TTS 行为另列“实机已验证”，不能用 pytest 代替。

---

## 17. 行为级测试契约

### 17.1 日程生成与睡眠生命周期

1. 次日计划只在前一日进入 `sleeping` 后生成；重复 tick 不重复调用 LLM。
2. 生成调用只允许输出模板中的 `rule_id/activity_key`，越界结构整体拒绝并使用可验证回退。
3. 正常日、休息日和长假使用角色模板自己的 profile，不复用别的角色活动。
4. 昼夜节律为 `night_aligned` 的角色可以拥有白天睡眠窗口；服务器午夜不能强行切断周期。
5. 熬夜、早睡和高强度活动会继承为有原因的 `schedule_offset`，并按角色回正速率逐日恢复。
6. 时间不足时低优先级非必需活动先压缩/跳过，高价值活动按模板保留。
7. 进程离线跨过活动时，无法证明完成的项目为 `expired_unknown`，不能自动变成 `completed`。

### 17.2 睡眠与活动交互表现

1. 睡前只发送一次状态预告；预告明确睡眠期间不再回复。
2. grace period 内用户持续聊天会延迟入睡但不超过模板 `max_extension_minutes`，并累计 `sleep_debt`。
3. 困倦阶段回复具有延迟、短文本和低情感振幅；错字不是每轮必现，也不影响认真/紧急消息。
4. 进入 `sleeping` 后普通输入不调用 LLM、不自动回执；起床后的首条用户消息可以基于实际睡眠期间消息生成衔接。
5. 学习/专注等 `inconvenient` 活动按模板告知可能短回/慢回；普通活动不强行播报。

### 17.3 回复接线

1. 当前活动为 `occupied_brief` 时，普通闲聊的 prompt 出现短回资源约束，最终用户可见文本没有协议标记。
2. 同一活动期间，认真问题仍得到核心回答，不能只返回“现在忙”。
3. 当前活动切换为 `available_normal` 后，短回约束消失。
4. `sleep_like` 禁止主动候选，但用户主动发消息仍能正常处理。
5. 日程活动不被每轮自动播报；没有可分享事件时不出现无来源自我叙述。

### 17.4 主动了解

1. 已有明确答案的主题不再生成同一信息缺口。
2. 同一主题在冷却期内不重复提问。
3. 用户明确拒绝后，缺口进入 `paused/declined`，不会惩罚关系或立即换一个相似问题追问。
4. 当前活动或用户状态禁止提问时，候选被抑制。
5. 候选缺少 `source_message_id` 或合法 `reason` 时 fail-closed。

### 17.5 主动分享

1. 没有 `virtual_life_event` 或角色卡稳定锚点时，不生成“我刚刚做了某事”。
2. 分享候选能回指 `event_id → item_id → plan_id → rule_id`。
3. 同一通道同一事件只发送一次；发送失败不提交已发送记录。
4. QQ 成功发送不更新 pet cooldown；pet 成功发送不更新 QQ cooldown。
5. 用户新消息到达时，未发送的同通道候选作废，不与普通回复双连发。
6. 角色分享不写入 `shared_episode`，除非用户后来明确与之形成真实共同对话事件。

### 17.6 真实性与设置页

1. 生产 prompt 可以包含内部日程上下文，但用户可见回复不能包含 `plan_id`、`item_id`、`truth_class`、`source_anchor`、`candidate_id`。
2. 用户追问“你刚刚做的事是否真实发生”时，回复遵守角色身份与现实行动边界，不继续编造证据。
3. 角色切换后不读取旧角色的虚拟日程和虚拟事件。
4. 计划、活动、用户信息缺口和主动反馈重启后仍能正确关联。
5. 日程设置位于独立的“日程与生活”页面，不与其他设置页混淆；保存运行覆盖不修改角色目录模板。

---

## 18. 用户体验验收标准

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

## 19. 与现有设计的关系和状态矩阵

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
- 每晚睡眠后 LLM 生成次日计划：未实现；
- 睡眠/起床/grace period/困倦回复/睡眠债务：未实现；
- 昼夜节律与偏移渐进回正：未实现；
- 当前活动影响回复：未实现；
- 来源可追溯的虚拟活动事件：未实现；
- 主动了解信息缺口：现有 QQ 时机/记忆素材不等于完整实现；
- 主动分享自身：现有问候、心跳、离线思考不等于完整实现；
- QQ/pet 独立 Gate：已有基础，接入日程来源后需补行为测试；
- 独立“日程与生活”设置页：未实现；
- 每个角色目录内的 `virtual_schedule.json`：未实现；
- 本规范本身：v1.1 设计契约，后续按 S0→S6 实施。

结论：本模块的成功标准不是“定时发送更多消息”，而是让角色拥有一个可持续、可解释、可中断、可恢复、不会冒充现实经历的虚拟生活状态，并让这套状态真正影响回复资源分配、主动了解和主动分享的来源链。

---

## 20. 术语索引

按名称索引核心对象，括号内为定义章节：

| 术语 | 定义 |
|---|---|
| `AmbientContext` | 虚拟空间与感官氛围，仅作角色素材（§5.7） |
| `AutobiographicalContext` | 虚拟生活事件的可回顾上下文，不等同于共同记忆（§3.2、§13.3） |
| `CuriosityCandidate` | 由用户信息缺口生成的主动了解候选（§10） |
| `DayCloseSummary` | 入睡后固化的当日模拟收尾摘要（§3.2、§7.2） |
| `DayContext` | 当前日的条件分类和覆盖信息（§3.2、§5.1） |
| `DayPlan` | 某个角色、日期和 revision 的具体虚拟计划（§3.2、§8.1） |
| `EffectiveSpan` | 活动扣除中断后的有效投入时间（§3.2、§8.5） |
| `EmotionalResidue` | 活动结束后短期保留的情绪/注意力残留（§5.7） |
| `LifeTheme` | 跨多日的生活主题，影响活动变体和偏离解释（§5.7） |
| `ScheduleEvent` | 活动或计划变化产生的可追溯虚拟事件（§3.2、§8.5） |
| `ScheduleItem` | DayPlan 中的一个活动时间段（§3.2、§8.4） |
| `ScheduleOffset` | 相对模板作息的时间位置偏移（§3.2、§8.1、§8.3） |
| `ScheduleOutline` | 角色长期日程模板和变体规则（§3.2、§4） |
| `ScheduleContext` | 注入回复编排的当前活动最小上下文（§3.2、§9.2） |
| `SelfShareCandidate` | 由虚拟活动/事件产生的主动分享候选（§11） |
| `SleepDebt` | 相对目标睡眠量的累计缺口（§3.2、§8.2） |
| `SleepMessageArchive` | 睡眠窗口内消息的受控归档（§3.2、§13.4） |
| `UserInfoGap` | 角色希望了解但尚未确认的用户信息缺口（§10.1） |
| `virtual_life_event` | `truth_class=virtual_simulation` 的角色自身事件（§3.1、§13.3） |

---

## 附录 A：反事实与边界条件

以下规则是实现和验收的快速边界表：

| 反事实场景 | 必须发生 | 禁止发生 |
|---|---|---|
| LLM 返回模板外活动 | 整体拒绝，按输入生成确定性回退 | 部分接受外部活动名称 |
| 前一日未进入睡眠 | 推迟次日计划生成 | 按服务器午夜自动生成 |
| 进程离线跨过活动 | 标记 `expired_unknown` | 批量标记 `completed` |
| 睡眠期间收到消息 | 记录受策略控制的元数据/引用 | 自动回复或复制一份正文归档 |
| 起床后无正文保留许可 | 只能说明收到若干条消息 | 声称知道具体内容 |
| grace period 已到上限 | 强制入睡并结束回复 | 无限挽留、继续调用 LLM |
| 活动中被用户消息打断 | 记录 interruption，暂停有效跨度 | 把中断时间计入活动投入 |
| 活动发生短期偏移 | 增加有原因的 `ScheduleOffset`，逐日回正 | 永久改变角色 chronotype |
| 时间不足 | 先压缩/跳过低优先级非必需活动 | 无视 required、产生时间重叠 |
| 角色为 `night_aligned` | 按角色模板使用白天睡眠窗口 | 强制套用白天作息 |
| 用户未按互动期望窗口出现 | 低强度残留或无变化 | 责备、惩罚、编造用户承诺 |
| 虚拟事件用于后续回顾 | 从自传归档带出 `virtual_simulation` 性质 | 写入 `shared_episode` 伪装共同经历 |
| 当前活动为 `inconvenient` | 普通闲聊变短/变慢，认真问题仍回答 | 以“正在忙”为由拒绝重要问题 |
| 当前活动为 `unavailable` | 停止普通 LLM 回复，等恢复阶段 | 发送“已看到”或虚假处理结果 |
| QQ 与桌宠同时有候选 | 各自运行独立 Gate、冷却和额度 | 共享发送计数或互相绕过 Gate |
| 计划/事件来源缺失 | 不生成主动分享或主动提问 | 用 LLM 自由补齐经历 |
| 用户询问活动是否真实 | 明确虚拟模拟边界 | 继续杜撰现实证据 |

这些规则优先于文案自然度。自然表达不能成为绕过来源、授权、睡眠边界、作用域和交付 Gate 的理由。
