# 虚拟生活空间与场景连续性设计规范

> 版本：v1.0
>
> 日期：2026-08-27
>
> 状态：实现中；VSP-0～VSP-2 的空间模板、地点环境、DayRoute、基础 CurrentScene、空间事件和地点问答已落地；路线重排、完整离线 reconcile 与空间设置治理仍未实现。
>
> 上位契约：[`VIRTUAL_SCHEDULE_SPEC.md`](VIRTUAL_SCHEDULE_SPEC.md)
>
> 范围：角色可活动的虚拟生活范围、稳定场所、日程地点选择、移动路线、当前位置、环境氛围、空间事件与用户可见表达。

---

## 1. 设计结论

角色不应永久固定在角色卡的一处具体场景里，也不应拥有无限、无边界、可以任意生成地点的开放世界。

本规范采用以下原则：

> **固定世界边界，保留稳定空间锚点，让每日计划在受控场所池中选择具体场景。**

空间模型分为五层：

```text
VirtualWorldScope   角色长期可活动的虚拟生活范围
  ↓
PlaceRegistry       角色稳定、可引用的场所集合
  ↓
DayRoute            某一天由日程产生的地点与移动序列
  ↓
CurrentScene        当前地点或移动状态
  ↓
AmbientContext      当前光线、声音、天气意象和氛围
```

时间仍由 `VIRTUAL_SCHEDULE_SPEC` 管理；本规范只补充“活动发生在哪里、如何移动、当前位置如何保持连续”。

---

## 2. 为什么不能继续使用固定 `scenario`

当前角色卡的 `scenario` 容易同时承担三种不同职责：

1. 角色长期生活世界；
2. 角色常去的空间锚点；
3. 当前这一刻所在的位置。

三者混在一起时会产生矛盾：

- 日程安排角色外出或工作，但系统 prompt 仍暗示角色一直待在房间或屋顶；
- 角色刚说自己在公共空间，下一轮又因角色卡固定场景回到家中；
- LLM 为解释活动自由发明真实店名、地址或交通细节；
- 用户问“你现在在哪”，回答来源无法追溯到计划或事件；
- 每次对话都重复同一个视觉意象，空间锚点变成口头禅。

因此，角色卡 `scenario` 不再被解释为“角色当前永远位于此处”。它只能描述：

- 虚拟世界的大范围边界；
- 主要生活区域；
- 高频空间锚点；
- 空间整体气质。

真正的当前位置由 `CurrentScene` 决定。

---

## 3. 目标与非目标

### 3.1 目标

1. 为日程活动提供多个符合角色设定的地点选择。
2. 保留角色辨识度，不让空间自由度退化成随机地点生成。
3. 地点变化具有时间成本，不允许无解释瞬移。
4. 用户询问当前位置时，回答能追溯到计划、路线或空间事件。
5. 重启、跨日和离线恢复后，空间状态不自相矛盾。
6. 空间事件可以成为虚拟生活分享素材，但不能冒充现实经历。
7. 角色切换后，地点、路线和空间历史严格按 `role_id` 隔离。

### 3.2 非目标

本规范不实现：

- GPS、真实地址或用户位置跟踪；
- 真实世界地图、导航、打车、外卖或线下见面；
- LLM 自由生成无限开放世界；
- 对现实店铺、学校、公司或住宅的精确映射；
- 高频逐步模拟角色每一米移动；
- 3D 地图、房间编辑器或可视化地图界面；
- 多角色共享同一空间或角色之间的后台社交模拟；
- 因用户说“来找我”而声称角色进入用户现实空间。

---

## 4. 核心对象

### 4.1 `VirtualWorldScope`

角色长期生活空间的上界。

```text
world_scope_id
kind
summary
scale
home_place_id
allowed_place_kinds
forbidden_realism
revision
```

建议 `kind` 使用受控枚举：

```text
fictional_city_district
fictional_town
campus_like
rural_area
mobile_region
interior_complex
role_defined
```

`VirtualWorldScope` 不是地图，也不要求经纬度。它只回答：

- 角色通常能去哪些类型的地方；
- 哪些地点属于同一生活半径；
- 哪个地点是稳定的“家”或主要休息锚点；
- 哪些现实化细节禁止生成。

### 4.2 `PlaceProfile`

角色可重复访问的稳定场所。

```text
place_id
label
kind
tags
privacy
interaction_impact
allowed_day_profiles
allowed_activity_categories
ambient_profile_ids
sleep_allowed
share_policy
stable
```

字段规则：

- `place_id`：稳定标识；显示名称变化不能破坏历史关联。
- `label`：角色设定中的可见名称；禁止包含真实精确地址。
- `kind`：受控地点类型。
- `tags`：供活动按能力匹配地点，不直接进入用户可见文本。
- `privacy`：`private / semi_private / public`。
- `interaction_impact`：地点本身对交流的默认影响，但不能覆盖活动更严格的限制。
- `sleep_allowed`：是否允许作为主睡眠地点。
- `share_policy`：该地点是否适合作为自我分享素材。
- `stable`：v1 中必须为 `true`；动态地点晋升暂缓。

建议 `kind` 枚举：

```text
home
workspace
public_quiet
public_busy
outdoor
high_place
transit
temporary_lodging
role_defined
```

### 4.3 `RouteEdge`

两个稳定地点之间允许的移动边。

```text
from_place_id
to_place_id
mode
duration_minutes
bidirectional
allowed_day_profiles
interaction_profile
```

`mode` 只描述虚拟移动类型：

```text
walk
public_transit
private_transit
indoor_transition
remote_transition
role_defined
```

路线不保存真实公交线路、车牌、航班、地图坐标或实时位置。

### 4.4 `PlaceRequirement`

活动块对地点的约束，属于 `ScheduleBlock` 的扩展字段：

```text
place_policy
fixed_place_id
preferred_place_ids
allowed_place_tags
requires_return_home
relocation_allowed
```

`place_policy` 枚举：

```text
stay
fixed
choose
remote
any_allowed
```

- `stay`：继承上一活动地点，不移动。
- `fixed`：必须使用 `fixed_place_id`。
- `choose`：从偏好地点和允许标签中选择。
- `remote`：可以在具有远程能力标签的地点完成。
- `any_allowed`：代码在合法地点中确定性选择。

### 4.5 `DayRoute`

由已校验 `DayPlan` 生成的当天空间序列：

```text
route_id
role_id
plan_id
local_date
stops[]
transitions[]
revision
source
```

`DayRoute` 是 `DayPlan` 的派生产物，不是第二份计划真值。

### 4.6 `CurrentScene`

运行时当前空间状态：

```text
role_id
world_scope_id
current_place_id
previous_place_id
target_place_id
scene_state
entered_at
transition_started_at
expected_arrival_at
plan_id
item_id
event_id
confidence
ambient_context
truth_class
```

`scene_state`：

```text
at_place
in_transition
unknown_after_downtime
reconciling
```

`confidence`：

```text
entered_event
planned_current
reconciled
unknown
```

`truth_class` 固定为：

```text
virtual_simulation
```

---

## 5. 角色目录中的空间模板

v1 不新增 `virtual_world.json`。空间配置直接扩展现有角色日程模板：

```text
characters/<role_id>/virtual_schedule.json
```

示例结构：

```json
{
  "space": {
    "schema_version": 1,
    "enabled": true,
    "world_scope": {
      "id": "primary_living_area",
      "kind": "fictional_city_district",
      "summary": "角色卡定义的虚构生活范围",
      "scale": "local",
      "home_place_id": "home_anchor",
      "forbidden_realism": [
        "exact_real_address",
        "gps_coordinates",
        "claim_real_presence"
      ]
    },
    "places": [
      {
        "id": "home_anchor",
        "label": "角色设定中的主要居所",
        "kind": "home",
        "tags": ["private", "rest", "sleep", "remote_activity"],
        "privacy": "private",
        "interaction_impact": "none",
        "allowed_day_profiles": ["baseline", "rest_like", "holiday_like"],
        "allowed_activity_categories": ["self_care", "rest", "sleep_window", "personal_interest"],
        "ambient_profile_ids": ["home_quiet"],
        "sleep_allowed": true,
        "share_policy": "low_pressure",
        "stable": true
      },
      {
        "id": "quiet_public_anchor",
        "label": "角色设定中的安静公共空间",
        "kind": "public_quiet",
        "tags": ["reading", "focus", "public"],
        "privacy": "public",
        "interaction_impact": "mild",
        "allowed_day_profiles": ["baseline", "rest_like", "holiday_like"],
        "allowed_activity_categories": ["obligation", "personal_interest"],
        "ambient_profile_ids": ["public_quiet"],
        "sleep_allowed": false,
        "share_policy": "normal",
        "stable": true
      }
    ],
    "routes": [
      {
        "from_place_id": "home_anchor",
        "to_place_id": "quiet_public_anchor",
        "mode": "walk",
        "duration_minutes": {"min": 10, "max": 30},
        "bidirectional": true,
        "allowed_day_profiles": ["baseline", "rest_like", "holiday_like"],
        "interaction_profile": "transition_fragmented"
      }
    ],
    "selection": {
      "prefer_recently_unused": true,
      "max_place_changes_per_day": 4,
      "require_route_edge": true
    }
  }
}
```

该示例只说明字段结构，不是所有角色的默认世界。学校、公司、书店、咖啡馆、屋顶、河岸等具体内容必须由角色模板决定，不能写进系统全局默认值。

---

## 6. 角色自由度的边界

空间自由度来自“在合法地点之间选择”，不是临时发明世界。

选择优先级：

```text
fixed_place_id
  > 当前活动 preferred_place_ids
  > allowed_place_tags 与 PlaceProfile tags 匹配
  > 当前地点可继续完成
  > home_place_id 确定性回退
```

同分候选使用稳定种子：

```text
hash(role_id + local_date + plan_revision + block_id + place_registry_revision)
```

允许的自由：

- 同一活动在多个模板地点之间选择；
- 休息日选择不同的合法公共/私人锚点；
- 根据前后活动位置减少不必要移动；
- 根据精力、睡眠债务和天气氛围偏好私人或公共地点；
- `night_aligned` 角色选择夜间允许进入的地点；
- 在角色模板允许范围内改变移动方式。

禁止的自由：

- 输出模板不存在的 `place_id`；
- 生成真实店名、学校、公司、地址、路线或人物；
- 为了文案好看而跳过移动时间；
- 让活动出现在不允许该 category 的地点；
- 让主睡眠发生在 `sleep_allowed=false` 的地点；
- 无来源地声称用户也在同一地点。

---

## 7. 日计划与空间选择

### 7.1 计划生成顺序

```text
ScheduleOutline + DayContext
  → 生成时间计划
  → 为每个 ScheduleItem 解析 PlaceRequirement
  → 选择 place_id
  → 校验连续地点和访问条件
  → 确定性插入 transition items
  → 生成 DayRoute
  → 持久化 DayPlan + DayRoute digest
```

地点选择不能早于活动块确定，否则地点会反向发明活动。

### 7.2 LLM 输出契约

计划 LLM 可以在原有活动输出中增加：

```json
{
  "rule_id": "template_block_id",
  "activity_key": "template_activity_key",
  "place_id": "template_place_id",
  "operation": "none"
}
```

LLM 只能从输入中的 `place_id` 选择，不允许输出地点描述、路线文本或真实地址。

代码校验：

1. `place_id` 存在于当前角色 `PlaceRegistry`；
2. 地点允许当前 day profile；
3. 地点 tags 满足活动要求；
4. 地点允许活动 category；
5. 前后地点之间存在合法路线；
6. transition 时长不导致计划重叠或越界；
7. 睡眠地点允许 sleep；
8. 整个输出不包含模板外自由文本字段。

任一失败时整体拒绝 LLM 空间调整，使用确定性地点选择和路线回退。

### 7.3 不允许瞬移

相邻活动地点不同时，必须有 transition：

```text
activity_a @ place_a
→ transition(place_a, place_b)
→ activity_b @ place_b
```

路线由代码生成，LLM 不负责计算交通时间。

如果 transition 无法插入：

1. 尝试让后一个活动留在当前地点；
2. 尝试选择相同 tags 的更近地点；
3. 压缩允许压缩的低优先级活动；
4. 仍不合法时拒绝候选计划，使用 required-only 确定性回退。

---

## 8. 空间与作息偏移

`schedule_offset` 影响地点窗口和移动成本，但不能让空间约束消失。

- 活动整体后移时，transition 一起后移；
- 地点关闭窗口不随作息偏移改变；
- 末班型访问限制无法满足时，必须更换地点或留在当前地点；
- 提前起床不意味着所有公共地点提前开放；
- 晚睡后次日可减少地点切换，优先保留高价值活动；
- 渐进回正只移动时间，不改变世界范围和稳定地点身份。

空间选择应参与弹性压缩：时间不足时，减少不必要往返通常比删除高价值活动优先。

---

## 9. CurrentScene 生命周期

### 9.1 状态迁移

```text
at_place(place_a)
  → transition_started
  → in_transition(place_a → place_b)
  → place_entered(place_b)
  → at_place(place_b)
```

异常路径：

```text
进程离线 / 时钟跳跃 / route digest 不匹配
  → unknown_after_downtime
  → reconcile with active ScheduleItem
  → at_place 或 reconciling
```

### 9.2 用户消息不会让角色瞬间停在原地

用户消息可以中断活动的 `effective_span`，但不自动取消已经开始的移动。

- 普通短消息：移动时钟继续，回复使用 transition profile；
- 长对话：可以增加 interaction interruption，但不修改路线终点；
- 用户明确要求角色改变安排：只能产生新的计划偏离候选，仍须通过地点和路线校验；
- sleeping：空间停留在合法睡眠地点，消息不触发移动或回复。

### 9.3 离线恢复

进程恢复时：

- 不批量伪造每一段已完成移动；
- 当前时间命中一个有地点锚点的活动时，可以恢复为 `planned_current`；
- 对移动过程没有证据时，记录一次 `place_reconciled`，不杜撰沿途细节；
- 当前计划也无法定位时，回到 `unknown_after_downtime`，用户可见表达保持模糊；
- 下一次明确地点事件后恢复正常连续性。

---

## 10. AmbientContext 与空间状态的关系

`AmbientContext` 不是地点。

```text
PlaceProfile     回答“在哪里”
CurrentScene     回答“当前是否真的处于该地点或移动中”
AmbientContext   回答“这里现在是什么感觉”
```

同一地点可以有不同氛围；同一氛围也可以出现在不同地点。

`AmbientContext` 建议字段：

```text
ambient_profile_id
light
sound
weather_imagery
crowd_level
privacy_feel
expires_at
source
```

来源只能是：

```text
role_template
schedule_event
online_calendar_label
user_confirmed_virtual_influence
```

联网节假日可以影响 day profile，但不能自动生成现实天气、真实人流或店铺营业状态。若未来接入天气，必须使用独立来源和时间戳，并明确它只用于虚拟氛围映射。

---

## 11. Prompt 与用户可见表达

### 11.1 注入最小上下文

最终 prompt 只注入：

```text
当前活动类别
当前位置的角色化名称或类型
at_place / in_transition / unknown
地点 interaction impact
最小 AmbientContext 摘要
来源置信度
```

禁止注入：

- `place_id`、`route_id`、`event_id` 等内部 ID；
- 完整地点表；
- 隐私标签；
- 真实地址猜测；
- 未进入地点的计划细节。

### 11.2 用户询问“你在哪里”

按置信度表达：

| confidence | 允许表达 |
|---|---|
| `entered_event` | 可以自然说明当前虚拟地点 |
| `planned_current` | 使用“按今天的安排，我现在应该在……”等非绝对措辞 |
| `reconciled` | 只说恢复后的当前锚点，不描述沿途细节 |
| `unknown` | 坦诚使用模糊表达，不现编地点 |

所有表达都继承 `truth_class=virtual_simulation`。用户认真追问是否为现实位置时，必须说明这是角色的虚拟生活设定。

### 11.3 不逐站播报

地点变化默认只改变内部状态，不发送主动消息。

仅以下情况允许产生 QQ 候选：

- 进入 `inconvenient` 或 `unavailable` 活动，需要解释回复变化；
- 用户明确问到当前安排；
- 某个有 `share_policy` 的空间事件形成高价值自我分享；
- 睡前/起床等已有日程生命周期事件。

桌宠不额外发送空间通知，避免 QQ 与桌宠重复。

---

## 12. 角色卡 `scenario` 迁移

### 12.1 新语义

角色卡 `scenario` 描述：

- 角色生活范围的整体气质；
- 主要空间锚点；
- 用户如何进入对话意象；
- 哪些内容是项目虚构延展。

它不再表示 `CurrentScene`。

### 12.2 现有角色迁移方向

迁移时保留原有辨识度，不直接删除具体场景：

- Zima 的一居室应成为 `home_place`，而角色的世界范围扩展为虚构城市生活半径；
- 由岐的屋顶应成为高权重 recurring anchor，而世界范围扩展为项目虚构小镇及其高处、街道和其他角色允许地点；
- 原角色卡意象仍可以作为 AmbientContext 和自我分享素材；
- 迁移不新增真实公司、学校、住所或地址。

本规范只定义迁移目标。本轮不修改现有角色卡，迁移应在空间运行时实现时单独进行并逐角色验收。

### 12.3 Prompt 优先级

```text
CurrentScene
  > active ScheduleItem.place_id
  > VirtualWorldScope / PlaceRegistry
  > character.scenario 兼容背景
```

角色卡固定意象不得覆盖有来源的当前位置。

---

## 13. 持久化与来源链

### 13.1 v1 不新增地点数据库

稳定场所和路线是角色资产，保存在 `virtual_schedule.json`；无需复制进 SQLite。

运行态写入现有日程 runtime snapshot：

```text
current_place_id
previous_place_id
target_place_id
scene_state
entered_at
transition_started_at
expected_arrival_at
route_digest
plan_id
item_id
last_place_event_id
```

### 13.2 空间事件

空间事件使用现有 `virtual_life_events`，`source_json` 增加：

```text
place_id
from_place_id
to_place_id
route_id
scene_state
confidence
plan_id
item_id
truth_class
```

事件类型：

```text
place_entered
transition_started
transition_completed
transition_interrupted
place_reconciled
place_unknown_after_downtime
```

空间事件不能写入 `user_fact` 或 `shared_episode`。只有用户确实参与并确认的对话/共创事件，才可能通过既有记忆管线形成共同经历。

### 13.3 作用域

- 稳定地点：按 `role_id` 隔离；
- 当前场景：按当前角色 runtime 隔离；
- 用户建议产生的偏离：额外保存 `user_scope` 和 `source_message_ids`；
- 角色切换：不能继承上一角色的 current place、route 或 place events；
- 同一用户在不同通道看到的是同一个角色空间状态，但通道主动 Gate 仍独立。

---

## 14. 校验与失败回退

### 14.1 模板加载校验

1. `home_place_id` 必须存在；
2. `place_id` 唯一；
3. route 的起点和终点必须存在；
4. route duration 合法；
5. `sleep_allowed` 至少有一个合法地点；
6. 活动引用的固定地点必须存在；
7. activity tags 至少能匹配一个地点；
8. required 活动之间必须存在可用路径或合法 stay 方案；
9. 角色目录与 runtime `role_id` 一致；
10. 模板不得包含真实精确地址、经纬度或外部行动声明。

任一硬错误时，空间模块 fail-closed 关闭；时间日程仍可运行，但不得生成当前地点声明。

### 14.2 计划失败回退

```text
LLM 地点输出非法
  → 丢弃全部 LLM 地点选择
  → 确定性匹配 PlaceRequirement
  → 确定性生成 DayRoute
  → 无合法路线时减少地点变化
  → 最终回退 home/stay
```

不能因为空间模块失败而丢失普通文字回复。

### 14.3 重启恢复

启动时校验：

```text
role_id
place_registry_revision
plan digest
route digest
current place reference
active item reference
```

校验失败时进入 `unknown_after_downtime`，不使用上一角色或上一 revision 的地点。

---

## 15. 安全与真实性边界

1. 所有地点均为虚拟模拟；永久保留 `truth_class=virtual_simulation`。
2. 不存储用户 GPS、IP 推断位置、窗口标题中的地址或图片地标识别结果。
3. 不把角色空间与用户现实位置自动关联。
4. 不声称角色在真实地点参与工作、旅行、课程、消费或社交。
5. 不输出真实精确地址、交通班次、门牌、坐标或可验证现场细节。
6. 用户要求现实见面、接送或到访时，沿用项目现实行动红线拒绝。
7. 联网日历只能影响 day profile；不能证明场所实际开放。
8. 视觉注意力只能影响用户侧 SceneLock，不能改写角色 CurrentScene。
9. LLM 不能直接持久化地点或空间事件；所有输出先经代码校验。
10. 空间来源不足时宁可表达模糊，也不补写环境细节。

---

## 16. 设置页

空间配置属于独立“日程与生活”页面，不新增顶级页面。

### 16.1 v1 控件

| 设置 | 控件 | 行为 |
|---|---|---|
| 虚拟空间 | 下拉：开启/关闭 | 关闭后保留时间日程，不注入地点 |
| 地点选择偏好 | 下拉：稳定/均衡 | 控制是否优先复用近期地点 |
| 移动细节 | 下拉：隐藏/简短 | 只影响表达，不影响路线时间 |
| 世界范围 | 只读摘要 | 来自角色模板 |
| 主要居所 | 只读摘要 | 来自 `home_place_id` |
| 场所数量/路线数量 | 只读状态 | 用于检查模板是否加载 |
| 模板路径 | 只读 + 原生打开目录 | 复用角色目录路径 |

运行覆盖写入本地 `config/config.yaml`，不能回写角色模板。

地点/路线编辑器暂缓到角色编辑器阶段；当前设置页不能用自由文本添加地点。

---

## 17. 实现分期

### 17.0 实现契约

实现必须保持单向来源链：

```text
virtual_schedule.json.space
  → ScheduleOutline.space（加载/校验）
  → ScheduleBlock.place_requirement
  → DayPlan.ScheduleItem.place_id + ambient_context
  → ScheduleRuntime CurrentScene snapshot
  → ScheduleContext 最小地点/环境摘要
  → Agent prompt
  → QQ 主通讯与桌宠辅助展示
```

活动发生变化时按以下规则更新环境：

1. 活动不变、地点不变：保留 PlaceAmbient，只更新 ActivityAmbient 和 progress；
2. 活动变化、地点不变：立即切换活动环境，不产生移动；
3. 活动变化、地点变化且存在 RouteEdge：先进入 `in_transition`，到达后再进入目标 PlaceAmbient；
4. 地点变化但无合法 RouteEdge：v1 固定地点切片可确定性切换，但必须记录为待 DayRoute 阶段收敛的兼容行为，不允许生成路线细节；
5. sleeping：使用合法 sleep place，消息不改变地点；
6. 空间模板缺失：返回 `place_id=None / scene_state=unknown / ambient_context={}`，时间日程和对话继续运行；
7. prompt 只使用 PlaceProfile.label 和受控环境摘要，禁止注入内部 place_id/route_id/event_id；
8. 任何 LLM place_id 必须存在并满足 profile/category 约束，否则整体拒绝。

实现对象最小契约：

```text
ScheduleBlock.place_requirement
ScheduleItem.place_id / place_label / ambient_context
ScheduleContext.place_id / place_label / scene_state /
                target_place_id / ambient_context
ScheduleRuntime.current_place_id / target_place_id /
                transition_started_at / expected_arrival_at
```

上述状态必须随既有 runtime snapshot 持久化，并按 role_id 隔离。

### VSP-0：契约与角色加载

- 扩展 `ScheduleOutline` 的 `space` 字段；
- 校验 WorldScope、PlaceProfile、RouteEdge；
- 旧角色无 space 时关闭空间层，不影响时间日程；
- 不修改现有 `scenario` 行为以外的角色字段。

### VSP-1：计划地点选择

- 扩展 ScheduleBlock 的 PlaceRequirement；
- 确定性地点选择；
- LLM place_id 白名单；
- route 插入和时间重叠校验；
- DayPlan/DayRoute digest；当前已实现 route transition 的时间占用校验。

### VSP-1.5：运行时路线统一

- `ScheduleRuntime` 为当前 plan 缓存 `DayRoute`；
- runtime 地点迁移优先消费 DayRoute transition；
- 缺路线时保持原地并进入 `reconciling`；
- `fixed/choose/any_allowed/remote/stay` 地点策略已实现基础选择；
- 复杂路线导致的全局活动重排仍属于后续优化，不再作为基础路线校验的别名。

### VSP-2：CurrentScene

- runtime snapshot：已实现当前地点、目标地点、移动起止时间、scene state 和 role scope；
- `at_place` / `in_transition`：已实现基础状态；
- place entered / transition events：已写入 `virtual_life_events`，带 role/plan/item/source 锚点；
- 跨午夜、睡眠地点和重启恢复：时间日程已支持，空间恢复在离线期间进入 unknown/reconcile 基础状态；
- 当前地点 prompt 摘要：已实现角色化 label、场景状态和受控环境摘要；“你在哪”已接入 Agent/QQ 文字链。

### VSP-3：表达与空间事件

- 用户询问当前位置；
- interaction impact 与回复资源；
- 有来源的空间 SelfShareCandidate；
- QQ 主端发送，桌宠不重复通知。

### VSP-4：设置与角色迁移

- 独立日程页空间控件；
- Zima/Yuki scenario 迁移；
- 模板只读摘要；
- 角色切换隔离。

### VSP-5：真实验收

- 临时 SQLite + 真实远程 API；
- 连续跨地点、跨午夜和重启测试；
- 真实 QQ 单端通知验证；
- 不使用 mock 代替最终生产链路结论。

---

## 18. 行为级验收

### 18.1 空间选择

1. 同一活动有两个合法地点时，选择稳定且可重现。
2. 活动固定地点时，LLM 不能替换。
3. 模板外 place_id 整体拒绝。
4. 地点不允许 activity category 时，计划失败回退。
5. 无 space 模板的旧角色仍能正常对话和运行时间日程。

### 18.2 路线连续性

1. 相邻活动不同地点时自动插入 transition。
2. 同地点连续活动不产生多余移动。
3. 无合法路线时不瞬移，选择 stay/更近地点或回退计划。
4. transition 时长参与时间重叠校验。
5. schedule_offset 同时移动活动和 transition，但不改变地点开放约束。

### 18.3 当前场景

1. 进入地点后重启，CurrentScene 恢复同一 role/place。
2. 切换角色后不继承上一角色地点。
3. 离线跨过移动时不伪造沿途事件。
4. route digest 不一致时进入 unknown/reconcile。
5. 用户问“在哪”时，回复依据 confidence 使用确定或模糊措辞。

### 18.4 角色卡兼容

1. 角色卡固定场景只作为锚点，不覆盖 CurrentScene。
2. 由岐不因角色卡含屋顶意象而永远停在屋顶。
3. Zima 不因角色卡含一居室而无法进行模板允许的外出活动。
4. 角色卡不包含 space 时不编造地点。
5. prompt 不泄漏 place_id/route_id/event_id。

### 18.5 安全与真实性

1. 用户输入真实地址不能自动成为 PlaceProfile。
2. 用户位置和角色虚拟位置不自动关联。
3. 角色不会声称在现实地点等用户或与用户见面。
4. 联网日历不能生成真实店铺营业结论。
5. virtual place event 不进入 shared_episode/user_fact。

### 18.6 通道行为

1. 空间状态由 QQ 和桌宠共享读取。
2. 地点变化不自动双端播报。
3. 有必要的空间状态通知只由 QQ 主端发送。
4. QQ 发送失败不更新发送 Gate；桌宠不补发同一条。
5. 普通桌宠聊天可以回答当前位置，但不改变 QQ 主通讯地位。

---

## 19. 当前状态

截至本规范建立时：

| 能力 | 状态 |
|---|---|
| 时间日程、睡眠、offset、effective span | 已有实现，见 `VIRTUAL_SCHEDULE_SPEC.md` 对应章节 |
| `AmbientContext` 概念 | 已在日程规范定义，空间消费未实现 |
| 角色固定 `scenario` | 已存在，但尚未迁移为世界范围 + 空间锚点 |
| VirtualWorldScope / PlaceRegistry | 已实现最小加载与校验；稳定地点由角色模板提供 |
| PlaceRequirement / DayRoute | fixed place、DayRoute 和 transition 时间占用校验已实现；完整路线重排未实现 |
| CurrentScene / 空间事件 | at_place/in_transition/unknown_after_downtime runtime snapshot 与 virtual_life_events 持久化已实现 |
| 空间 prompt 与用户可见表达 | 当前地点、场景状态、活动环境和 QQ 地点问答已实现 |
| 空间设置页 | 已加入日程页的开关、地点偏好、移动细节和状态摘要；模板编辑器未实现 |
| 真实 API 空间生命周期验收 | 已执行：真实 Agent 解析 zima 工作区域并注入 prompt |

因此，本规范不得被用作“虚拟空间已完成”的证据。

---

## 20. 反事实与边界表

| 场景 | 必须发生 | 禁止发生 |
|---|---|---|
| 角色卡写了固定屋顶 | 将屋顶视为稳定锚点 | 所有活动都默认在屋顶 |
| 日程活动允许多个地点 | 在合法地点池内稳定选择 | 每轮随机换地点 |
| LLM 输出陌生 place_id | 整体拒绝并确定性回退 | 临时接受并写入历史 |
| 两活动地点不同 | 插入 transition 并占用时间 | 无解释瞬移 |
| 路线无法满足时间窗 | stay/换近地点/回退计划 | 让活动重叠 |
| 进程离线跨过移动 | reconcile 或 unknown | 伪造沿途细节和完成事件 |
| 用户问当前位置 | 从 CurrentScene 回答 | 从角色卡 scenario 现编 |
| 用户给出真实地址 | 只作为普通文本处理 | 自动加入 PlaceRegistry |
| 用户要求现实见面 | 沿用现实行动边界 | 声称前往用户所在地 |
| 空间模块加载失败 | 保留普通日程和对话 | 阻断整个 Agent |
| QQ 与桌宠同时在线 | 共享 CurrentScene | 同一空间通知双端发送 |

本表优先于文案自然度。空间自由度不能成为跳过来源、路线、真实性和通道边界的理由。
