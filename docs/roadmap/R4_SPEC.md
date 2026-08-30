# R4 专项：有分寸的在场与主动性

> 目标：让角色偶尔主动靠近，但没有监控感和骚扰感。
> 现有复用：`core/ambient.py` 的 SceneLock/ChannelActivityTracker/Arbitrator、`core/proactive.py`、`core/presence.py`、`pet_server.py`。
> 最小新增：ProactiveCandidate/ProactiveDecision、统一主动入口、忽略反馈持久化。

## 1. 三段式决策

```text
Source event → Candidate → Decision → Reply
```

### Candidate

```python
@dataclass(frozen=True)
class ProactiveCandidate:
    source: str                 # shared_episode/commitment/scene/ritual/attention
    reason: str                 # 内部可解释原因
    relevance: float            # 0..1
    urgency: float              # 0..1
    intent: str                 # remind/check_in/share/bridge
    context: dict
```

### Decision

```python
@dataclass(frozen=True)
class ProactiveDecision:
    allow: bool
    reason: str
    cooldown_until: float
    candidate: ProactiveCandidate | None = None
```

`AttentionScheduler` 只能产出 candidate context，禁止调用 `Agent.proactive_from_visual()` 或 `speak()`。

## 2. 确定性闸门顺序

按顺序执行，任一失败返回 `allow=false`：

1. `enabled` 与用户暂停开关。
2. 场景不是 `busy/away/blocked`。
3. 当前没有其他通道活跃（QQ/桌宠交互窗口）。
4. quiet hours 外。
5. 当日上限未满（默认 2）。
6. 距上次主动消息足够久（默认 30min；同源默认 2h）。
7. 最近主动消息未被忽略到抑制阈值。
8. candidate relevance >= 0.65，且有 shared_episode/commitment/明确场景理由。
9. 生成前检查 LLM 可用、输出非空、人格/通道解析成功。

不保留 `random.random() < proactive_message_prob` 作为主入口；如需自然性，只允许在全部闸门通过后做一次小概率抑制，并记录 reason。

## 3. 来源策略

| 来源 | 默认 | 说明 |
|---|---|---|
| commitment | 高 | 到期/用户明确要求时可主动提醒 |
| shared_episode | 中 | 相关场景触发，一次即可 |
| scene | 低 | 场景结束后衔接，不在忙碌中打扰 |
| ritual | 低 | 生日/纪念日，需真实记忆来源 |
| attention | 低 | 视觉只提供候选，必须再匹配角色兴趣/共同经历 |
| idle/fatigue | 关闭 | 没有理由的“你在干嘛”暂不做 |

## 4. 忽略与自愈

记录 `proactive_feedback`：`sent_at, source, responded, interrupted, user_sent_within, dismissed`。用户明确“不想被打扰”：立即暂停直到用户主动恢复。

用户新消息到来时，尚未发送的 pending candidate 作废。主动消息不得与用户回复同一轮连发。

## 5. 配置

```yaml
proactive:
  enabled: true
  max_per_day: 2
  min_gap_minutes: 30
  quiet_hours: [23, 8]
  visual_candidates: true
```

配置旧 `qq.proactive` 只作为兼容读取，最终归一到此段。

## 6. 测试

覆盖：场景阻塞、通道独立间隔、静默时段、每日上限、用户新消息作废、视觉候选无共同经历不发送、commitment 到期可发、低成本模型输出异常降级。
