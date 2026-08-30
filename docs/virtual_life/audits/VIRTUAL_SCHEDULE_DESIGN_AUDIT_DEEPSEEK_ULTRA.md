# DeepSeek v4 Pro Ultra：虚拟日程设计审计

> 模型：deepseek-v4-pro
> Provider：deepseek
> Reasoning：ultra
> 审计方式：Hermes CLI 独立只读会话
> 日期：2026-08-27

## 总体结论

设计骨架成熟：`truth_class` 隔离、来源锚点、整体拒绝校验、通道独立 Gate 和确定性回退方向正确。阻断问题集中在睡眠语义冲突、模板契约与实现脱节、核心运行状态无明确持久化，以及状态枚举不一致。

## P0

### 角色睡眠语义冲突

规范 §6.4 和验收要求 sleeping 后不调用 LLM、不回复；§9.3 的 `sleep_like` 又写成用户主动消息仍正常处理。必须统一成：

- `character_sleeping`：角色自身睡眠，阻止普通回复；
- `user_sleeping`：用户表示要睡后的 QQ 主动静默，用户再次发消息可解除。

两者不能共用一个 sleeping 名称或解除规则。

## P1

1. **模板契约缺字段**：顶层 sleep、target/max debt、debt recovery、priority、interaction_impact、label、最大偏移和连续天数未完整进入 §4 字段契约。
2. **状态无持久化定义**：ScheduleOffset、SleepDebt、DayCloseSummary、generation_key、interruption spans、plan digest 无明确存储位置。
3. **状态枚举不一致**：sleeping/waking/unavailable 与 plan generating/ready 混在一条状态链；waking 条件、unavailable 来源、熬夜上限未定义。
4. **睡眠债务公式不闭合**：恢复速率没有进入公式；“消耗债务”方向应为“增加债务”。
5. **effective_span 不完整**：缺 parent_event_id、区间数据、恢复条件、多次中断和跨睡眠边界规则。
6. **user_info_gaps 缺 role_id/user_scope**：与作用域隔离承诺矛盾。
7. **QQ 模糊状态前缀冲突**：日程启用后，QQ_PROACTIVE_SPEC §7 的“刚忙完/刚醒”等前缀必须由 ScheduleContext 接管。

## P2

- 章节编号和标题存在重复/错位；术语索引部分锚点错误。
- v1.2 头部、v1.1 变更说明和状态矩阵版本不一致。
- DESIGN.md 未引用 QQ_PROACTIVE_SPEC / VIRTUAL_SCHEDULE_SPEC；QQ 规范引用了已清空的 design_append。
- seed 只能约束确定性基础计划；LLM 幂等必须依赖 generation_key。
- 计划失败不应使用空计划，应生成 required-only 最小计划。
- 睡眠消息归档需要 TTL、删除联动和原文已删分支；无正文许可时可只保留聚合计数。
- 假日/长假在第一阶段哪些可达、跨日覆盖、补觉是否范围外、偏移记账边界需写清楚。
- 设置页的 variation、自定义 profile、状态告知上限、grace 参数优先级和时区覆盖未定义。
- AutobiographicalContext 与 self_model_chapters 的关系未定义。

## 建议修订顺序

1. 统一 character_sleeping / user_sleeping 语义。
2. 回写角色模板真实字段契约。
3. 增加 virtual_schedule_state 或同等持久化契约。
4. 为 user_info_gaps 增加 role/user scope。
5. 拆分 schedule_state、plan_status 和 generation lock。
6. 修正 SleepDebt 公式。
7. 日程启用后让 QQ 虚拟状态前缀服从 ScheduleContext。
8. 清理章节编号、版本、引用和状态矩阵。

## 明确不实现

- 角色睡眠期间自动回复/已读回执。
- 打断式“必须听我说”的主动插话。
- 白天补觉/多睡眠周期模型（v1 仅主睡眠周期）。
- 联网节假日 API、真实日历和现实活动集成。
- LLM 自由文本生成整日计划。
- virtual_life_event 直接写入 shared_episode/user_fact。
- 睡眠消息正文二次归档。
- 多角色共享模板和跨角色计划复用。
- 把 QQ readiness 模型复制到桌宠。
- 高频意识流 tick、设置页模板编辑器、手填活动枚举。
- schedule_offset/sleep_debt 直接覆盖 AgentState.energy。
- 从 assistant 虚构内容生成 UserInfoGap。
- 把用户举例硬编码进角色模板。
