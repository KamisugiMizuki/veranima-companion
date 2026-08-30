# 虚拟日程实现完成度复核

> 日期：2026-08-27
> 基线：当前工作树，DeepSeek v4 Pro Ultra 两份审计修正后
> 自动化：919 passed, 1 warning；Node 三文件语法检查通过
> 真实远程 API：普通连续对话 20/20；日程生命周期专项通过

## 已实现并进入生产调用链

| 设计点 | 状态 | 行为证据 |
|---|---|---|
| 角色目录独立模板 | 已实现 | `characters/<role>/virtual_schedule.json`；实际 zima/yuki 均加载成功 |
| 模板严格校验 | 已实现 | schema、枚举、block/profile 引用、窗口、sleep/circadian fail-closed |
| 确定性日计划 | 已实现 | role/date/profile/revision 稳定 plan_id，跨午夜窗口测试 |
| 结构化 LLM 调整 | 已实现 | 直接 `llm.chat` 原始 JSON；模板外活动整体回退；day_profile/activity_key/shift/resize/skip 生效 |
| 当前活动 prompt | 已实现 | `Agent.handle()` 注入 category/activity_key/profile/reply_budget；可见回复不含内部 ID |
| 睡眠状态机 | 已实现 | awake→sleep_preparing→sleeping→awake；grace 上限、sleep debt、wake 轮换 |
| 睡眠普通消息拦截 | 已实现 | shared Agent.handle 在 sleeping 时不调 LLM、不回复 |
| 睡眠消息元数据归档 | 已实现 | messages 正文沿既有策略保留；sleep archive 只保存引用/元数据 |
| 跨重启状态 | 已实现 | runtime snapshot 随 agent_state.relationship 保存；role_id 不匹配不恢复 |
| 后台推进 | 已实现 | 同进程 QQ async、独立 QQ thread、PetServer presence loop 均 advance runtime |
| 主动睡眠 Gate | 已实现 | ProactiveGate 统一读取 character_sleeping，QQ/pet 主动候选均受阻 |
| 睡前/起床状态告知 | 已实现 | runtime 单次 notice；QQ/Pet 后台消费；LLM 角色化文案 |
| 独立设置页 | 已实现 | 独立“日程与生活”页；DOM→renderer→main/preload→PetServer→YAML→Agent runtime |
| QQ/pet 冷却隔离 | 已实现 | 复用已有 channel gate，schedule/curiosity 来源要求锚点 |

## 部分实现

| 设计点 | 当前范围 | 缺口 |
|---|---|---|
| 睡眠债务 | grace 延长、目标睡眠、恢复速率 | 未实现多日高负荷综合公式；v1 只支持主睡眠周期 |
| schedule_offset | 模板 shift 与 LLM shift 可调整活动 | 未实现持久化 offset 链和逐日回正历史 |
| DayCloseSummary | 次日计划由当前 runtime 状态触发 | 未形成独立持久化摘要/虚拟自传归档 |
| effective_span | 设计与事件字段已有 | 未接入实际活动中断/恢复计时器 |
| 起床衔接 | woke notice + 睡眠消息归档 | 已按元数据告知恢复；未将正文摘要合并到醒后首轮 |
| 日程计划持久化 | next plan 日期/source 随 runtime snapshot 恢复 | 完整 LLM adjustments 未单独持久化，重启按模板重建同日计划 |
| 日程自我分享 | 状态 notice 与 virtual_schedule Gate 已接 | 普通 virtual_life_event → SelfShareCandidate 生产者未完整实现 |
| 主动了解用户 | Gate 要求 source_message_id | UserInfoGap 数据模型与生产者未完整实现 |

## 已修复的 DeepSeek Ultra 阻断项

- sleeping 达到目标睡眠时长后恢复 awake，轮换 sleep cycle 并清理 next plan；
- planner 使用 `llm.chat` 原始 JSON，不经过 IM Reply parser；空输出不再伪标 LLM 计划；
- ProactiveGate 统一阻断角色 sleeping 状态下的 QQ/pet 主动；
- 设置页 enabled/grace/extension 已由 Agent runtime 消费；
- 睡眠消息元数据归档接入 Agent 生产入口；
- grace 用户消息延长接入共享 handle；
- runtime snapshot 带 role_id，换角色不恢复上一角色 sleeping/debt；
- 独立 QQ thread 与桌宠 background 均推进 runtime；
- day_profile 和 activity_key 在当前 profile 作用域内校验并应用；
- next plan date/source 随 runtime snapshot 恢复；
- prompt 注入 activity category/key；
- 睡前/起床通知经 channel-specific Gate 后发送成功才 commit。

## 明确未实现/暂缓

- 多睡眠周期和白天补觉模型；
- 联网节假日 API、真实日历、地图、媒体播放等现实集成；
- 设置页模板编辑器；
- LLM 自由文本生成整日计划；
- virtual_life_event 直接进入 shared_episode/user_fact；
- 多角色共享模板和计划；
- 高频后台意识流；
- 完整 AutobiographicalContext / self_model_chapters 投影；
- 睡眠消息正文二次归档。

## 真实 API 证据

### 普通连续对话

- 20/20 轮可见回复；
- 20 条 assistant 记录重开 SQLite 后可读；
- 无 fallback、无 internal reply、无 plan_id/item_id/source_anchor/truth_class/candidate_id 泄漏；
- 回复长度 20～129 字。

### 日程生命周期专项

- 真实 planner 返回结构化 JSON，5 个模板内 items；
- sleeping 时用户消息不产生回复；
- sleep_message_archive 记录 1 条元数据；
- 达到目标睡眠时长后恢复 awake；
- awake 后真实远程 API 回复成功（36 字）。

当前复测未再出现 planner `finish_reason=length`；仍保留确定性回退，任何截断/空/越界结构都不会应用到计划。
