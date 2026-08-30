# DeepSeek v4 Pro Ultra：虚拟日程当前轮审计与修复

> 日期：2026-08-27
> 模型：deepseek-v4-pro
> Provider：deepseek
> Reasoning：ultra
> 审计方式：Hermes CLI 独立只读会话
> 审计对象：`cc0b9b5` 之后的当前未提交虚拟日程/README 改动

## 审计阻断项

1. `day_close_summary` 同一 sleep cycle 会重复归档。
2. 跨午夜醒后衔接使用醒来日期查询，找不到原 sleep cycle。
3. 睡眠消息在 LLM 成功前标记 processed，失败后无法重试。
4. 唤醒时清空入睡期间生成的 next plan，使日历/profile/LLM adjustments 失效。
5. 同步日历请求在 QQ/Pet async tick 内阻塞事件循环。
6. self-share/curiosity 设置未消费；特殊候选无事件去重；缺口询问后不关闭；curiosity 可能长期饥饿。
7. 已完成 activity span 被再次 finish，整夜睡眠可能计入有效活动。
8. UserInfoGap 与睡眠归档使用 `qq:default`，多个白名单用户可能混用。
9. Nager.Date 的中国数据不包含官方调休工作日；holiday_like 在无同名 profile 的角色模板中不生效。
10. QQ/Pet 背景新增无条件 `to_snapshot()`，破坏原有 Runtime 协议测试。
11. schedule offset 只记账，不影响计划时间。
12. Agent 异常路径不恢复 activity interruption。
13. calendar base_url 缺少信任边界校验。
14. README 声明和测试数字超过当前代码事实。

## 针对性修复

- `virtual_life_events` 增加 `cycle_key` 和 DB 唯一索引；store 使用 `INSERT OR IGNORE`，同周期 DB 级去重。
- runtime 持久化 `last_sleep_cycle_id`；醒后按原 sleep cycle 查询。
- sleep archive 只在 assistant 回复成功持久化后标记 processed。
- wake 保留已生成的 next plan、profile 和完整 adjustments。
- 新增 `Agent.advance_schedule_async()`；联网日历预取通过 `asyncio.to_thread`，runtime tick 不再同步联网。
- self-share/curiosity 消费设置开关；特殊候选发送前查 proactive feedback；成功后记录 gap asked；按日期轮换候选。
- finished span 直接返回原 summary，不重复计算。
- QQ adapter 在串行边界注入 `qq:<uid>` scope；桌宠使用 `pet:default`，保持原 `Agent.handle` 公共签名兼容。
- 节假日无 `holiday_like` profile 时映射 `rest_like`；README 明确 Nager.Date 不包含中国官方调休工作日。
- 背景推进兼容无 snapshot 的 Runtime 测试桩。
- offset 作为统一 shift 应用到 deterministic 和 LLM plan，并按角色 recovery rate 收敛。
- QQ/Pet adapter 用 finally 恢复 activity interruption。
- calendar base_url 限定 `https://date.nager.at`；设置保存层同步校验 URL 和 country code。
- 设置运行时消费 timezone、enabled、grace/extension、self_share/curiosity、calendar；day_profile 仅在角色存在同名 profile 时覆盖。

## 验证

```text
日程及受影响链：119 passed
全量：930 passed, 1 warning
Node main/preload/settings-renderer --check：通过
真实远程 API 连续对话：20/20，重开 SQLite 后 20 条 assistant 记录
真实日程生命周期：planner 5 items；sleeping 空回复；归档 1 条；恢复 awake；醒后回复 30 字
联网日历：2026-01-01 → 元旦 / holiday_like / online_calendar
```

## 剩余边界

- Nager.Date 只提供公共节假日，不表达中国官方调休工作日；README 已明确。
- 桌宠不是主要通讯端，且不消费日程通知；纯桌宠且 QQ 关闭时不会发送睡前/醒后日程通知，这是当前产品边界。
- `variation` 仍是设置预留项，尚未映射为独立算法参数；不应把它声称为已验证的行为调节器。
