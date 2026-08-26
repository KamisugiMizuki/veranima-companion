# DeepSeek v4 Pro Ultra：虚拟日程实现审计

> 模型：deepseek-v4-pro
> Provider：deepseek
> Reasoning：ultra
> 审计方式：Hermes CLI 独立只读会话
> 日期：2026-08-27

## P0 阻断问题

1. **没有起床/恢复逻辑**：`ScheduleRuntime` 只会 `awake → sleep_preparing → sleeping`，进入 sleeping 后不会回到 awake。
2. **真实结构化计划输出会被丢弃**：`_plan_schedule_with_llm()` 通过 `_short_task()`，而该函数按普通 IM Reply 解析，日程 JSON 被当成非法结构化输出。
3. **睡眠闸门只覆盖 `Agent.handle()`**：桌宠无缝问候、视觉主动、QQ 主动等路径可能在 sleeping 时继续调用 LLM 或发送。
4. **设置页配置没有运行时消费者**：DOM → YAML 已接通，但 `cfg.virtual_schedule` 未真正控制 ScheduleRuntime。

## P1 问题

- `archive_sleep_message()` 和表存在，但生产消息入口未调用；起床衔接没有数据源。
- 次日计划只在内存，无跨重启幂等持久化；失败可能按背景 tick 重试 LLM。
- 用户消息没有调用 `extend_wakefulness()`，grace 挽留未接线。
- LLM 调整只部分生效：substitute/day_profile 等被丢弃。
- 计划校验使用整个 outline，而不是当前 day profile 的 allowed blocks。
- `agent_state` 单行快照未按 role_id 校验，换角色可能继承上一角色 sleeping/debt。
- 独立 `python -m veranima.qq` 的线程背景循环未推进 runtime。
- `_next_day_plan` 永不清理，D+2 周期会卡死。
- 无睡前告知、最后告知和真正的 drowsy 画像。
- prompt 只有交互资源，没有 activity_key/category，用户问“你在做什么”缺少素材。

## 差距结论

- 模板加载、fail-closed 校验、确定性日计划、部分跨午夜窗口：已实现。
- 后台推进：桌宠/同进程 QQ 已接，独立 QQ 未接。
- sleeping 普通对话拦截：已实现；恢复和所有主动入口拦截未实现。
- 次日 LLM 计划：有调用入口，但真实输出无法消费，不能称完成。
- 设置页：显示和保存已实现，运行时消费未实现。
- 睡眠消息归档、自传归档、offset/debt 回正、主动分享/好奇：未完成。

## 真实 API 20 轮脚本证明边界

脚本证明普通对话在真实远程 API 下：20 轮有可见回复、无 fallback、无内部协议泄漏、持久化一致、重开库可读。它没有验证睡眠、起床、次日计划、日程背景推进或主动性行为，因此不能作为虚拟日程全功能验收。
