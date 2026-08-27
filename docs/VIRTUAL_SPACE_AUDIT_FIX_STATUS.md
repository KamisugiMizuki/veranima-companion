# DeepSeek v4 Pro Ultra：虚拟空间二次审计

> 会话：@session:default/20260827_232302_bdc76e
> 模型：deepseek-v4-pro
> Reasoning：ultra
> 审计对象：9edfacc 后的虚拟空间修复工作树

## 审计结论

### 已修复并复核

- 无路线不瞬移，缺路线保持出发地点并进入 `reconciling`；
- zima/yuki 路线闭合；
- 重启后重新应用 `space_enabled/calendar/profile_override`；
- 损坏时间戳 fail-closed；
- `unknown_after_downtime` 不被普通 `advance()` 伪造成到达；
- 只有显式 `reconcile_after_downtime(arrived=True)` 才完成到达；
- 冷启动落在睡眠窗口时进入准备状态；
- 模板和运行时均校验 `sleep_allowed`；
- 空间关闭保留时间日程、清空地点和环境；
- CurrentScene 字段补充 previous/transition/arrival/confidence；
- 地点问答使用角色化 label，不输出内部 ID；
- 活动变化、地点变化和 transition 在 runtime/Context/prompt 中保持一致；
- 角色模板路线和活动地点实际加载成功。

### 审计发现的剩余边界

1. **DayRoute 与 runtime 仍有两条路径**：DayRoute 可生成并做 transition 时间校验，但常驻 runtime 仍使用轻量 `_route()` 状态推进；完整路线重排尚未实现。
2. **离线 reconcile 没有用户确认入口**：未知状态只能通过显式 API 确认到达，尚未接入 QQ/设置页确认流程。
3. **space_preference / space_detail 仍是运行覆盖字段，当前没有独立算法消费者**；已从“完成”描述降为预留/基础状态。
4. **空间事件使用基础事件类型**，尚未完整区分 transition_completed、transition_interrupted、place_reconciled。
5. **真实空间验收是 Agent/远程 API + prompt 探针，不是 NapCat/Pet 双端联机验收**。
6. `yuki` 模板存在纯格式缩进问题，不影响 JSON 解析，但应在后续模板整理时修正。

## 本轮修复

- 修复 transition 到达后旧活动 context 覆盖 runtime 目标地点，支持连续多次地点迁移；
- runtime snapshot 数值字段损坏时安全回退；
- `CurrentScene`/`ScheduleContext` 保持 unknown 状态并传入 prompt；
- 新增连续地点迁移、脏快照和空间运行时行为测试；
- 完成度文档明确区分基础实现、部分实现和未实机验证。

## 二次复审后的修正

- `unknown_after_downtime` 不再被普通 `advance()` 覆盖；显式 reconcile 才能确认到达；
- `ScheduleContext` 会暴露 unknown 状态；
- 完整路线闭合、无路线不瞬移、空间开关恢复和 `sleep_allowed` 均有回归；
- 脏 snapshot 的 state、scene、place ID、时间和数值字段 fail-closed；
- 空 transition timestamp 降级为 unknown，不永久保持移动状态；
- CurrentScene 补充 previous place、transition/arrival 时间和 confidence；
- 修复 transition 到达后旧 context 覆盖目标地点的问题，支持连续移动；

## 最新验证

```text
空间/日程相关：通过
全量 pytest：967 passed, 1 warning
Node 语法检查：通过
zima DayRoute：3 transitions
 yuki DayRoute：2 transitions
真实空间 Agent/prompt：工作区域 + screen_cool/quiet_keyboard
```

## 严格结论

当前可以声称：空间模板加载、固定地点、活动环境、基础 CurrentScene、基础 transition、离线 unknown、防瞬移、地点问答和 prompt 接线已实现。

当前不能声称：完整 DayRoute 已成为唯一生产调度路径、完整路线重排、空间设置偏好算法、完整空间事件状态机、真实 NapCat/Pet 双端联机验收已完成。
