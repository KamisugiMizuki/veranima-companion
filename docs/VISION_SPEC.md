# VISION_SPEC：视觉注意力实现契约

> 目标：让角色有限、可解释地注意环境，而不是做屏幕监控。
> 现有复用：`core/attention/`、`core/presence.py`、`pet_server.py`。
> 重要边界：本模块不生成角色回复、不调用 speak、不写长期记忆。

## 1. 输入/输出数据结构

```python
@dataclass(frozen=True)
class AttentionInput:
    foreground_app: str
    foreground_category: str
    cursor_pos: tuple[int, int] | None
    idle_seconds: float
    locked: bool
    frame: object | None
    captured_at: float

@dataclass(frozen=True)
class AttentionEvent:
    event_id: str
    kind: str  # window_switch/focus_shift/habituated/away
    region: tuple[float, float, float, float]
    confidence: float
    source: str # foreground/cursor/saliency/manual
    reason: str
    expires_at: float

@dataclass(frozen=True)
class Observation:
    event_id: str
    summary: str
    category: str
    confidence: float
    sensitive_redacted: bool
    expires_at: float
```

旧 `AttentionEvent(kind, region, tag, note, ts)` 保留兼容属性；新增字段逐步接入。

## 2. 功能栈与调用边界

| 层 | 实现 | 允许 | 禁止 |
|---|---|---|---|
| L0 | ctypes Win32 | 前台/鼠标/空闲/锁屏 | 读屏幕内容 |
| L1 | Pillow + numpy | 降采样帧差/结构显著 | LLM 调用 |
| L2 | Pillow crop + 窗口元数据 | 生成低成本观察输入 | 自动发言 |
| L3 | `LLMClient.observe_image()` | 输出结构化 Observation | 写 memories/调用 speak |
| policy | `VisibilityPolicy` 纯函数 | 敏感分类、暂停、过期 | 依赖 UI |
| consumer | `ProactiveEngine` | 将 Observation 变候选并仲裁 | 绕过 M4 闸门 |

不引入 OpenCV、本地视觉模型或眼动 SDK。现有 Pillow/numpy 足够完成 MVP。

## 3. 状态机与确定性规则

```text
away --activity--> orienting --stable 2s--> fixating
fixating --no novelty 60s--> habituated
habituated --new window/novelty--> orienting
任何状态 --locked/privacy_pause--> away
```

- 鼠标位置停留 >=2s 才成为焦点证据。
- 鼠标快速移动只更新位置，不产生事件。
- 前台窗口变化立即产出 `window_switch`，但不会直接观察或发言。
- 同一窗口/区域 60s 无新内容进入 habituated。
- 用户 30min 无输入进入 away；恢复后重新 orienting。
- 同一事件不得重复发出；使用 `event_id` 和 TTL 去重。

## 4. 敏感窗口策略

默认分类关键词：密码、支付、银行、私聊、会议、锁屏、安全、验证码。命中时：

1. 不截取/不发送图像。
2. `foreground_app` 只保存 `sensitive` 类别。
3. 产出 `away/privacy_block` 事件。
4. 用户可通过 `attention.paused=true` 全局暂停。

## 5. 观察调用策略

- 新窗口：先使用 app/category 元数据，不自动发图。
- 新奇显著区域：允许一次裁剪观察；区域最大为屏幕短边 30%。
- 同一 `window_category + region_key` 在 `observe_cache_ttl_sec=120` 内不重复 L3。
- `observe_daily_budget` 默认 120 次；超出只保留 L0/L1。
- 观察结果 TTL 默认 10min；过期不注入对话。
- 原始截图不落盘；只在内存中存在到 L3 请求结束。

## 6. Observer JSON 契约

提示模型只输出：

```json
{
  "summary":"不超过80字的事实描述",
  "category":"coding|browser|game|video|meeting|private|unknown",
  "notable":["最多3项"],
  "confidence":0.0,
  "sensitive_redacted":false
}
```

程序侧：去 fence、JSON 解析、字段白名单、长度截断、confidence clamp、category fallback=`unknown`。任何失败返回 `Observation(summary="", category="unknown", confidence=0)`，不抛给主循环。

## 7. 与主动性连接

```text
Observation
 + CharacterCard.interests
 + MemoryStore.recall(category/summary)
 + SceneLock
 + ChannelActivityTracker
 + social_appetite
 → ProactiveCandidate
 → R4 ProactiveDecision
```

只有 `shared_episode` 或 `commitment` 相关性 >=0.65，且所有闸门通过，才允许生成一条主动消息。窗口切换本身永远不是发言理由。

## 8. 配置

```yaml
attention:
  enabled: true
  paused: false
  global_scan_sec: 5
  mouse_focus_stay_sec: 2
  habituation_sec: 60
  observe_cache_ttl_sec: 120
  observe_daily_budget: 120
  crop_ratio: 0.30
  save_raw_images: false
  sensitive_categories: [password, payment, private, meeting, lockscreen]
```

## 9. 验收与日志

日志字段固定：`event_id, state, source, reason, confidence, action, suppressed_reason`。验收：窗口切换不直接发言、连续打字不持续 L3、鼠标抖动不产生事件风暴、敏感窗口不发图、暂停即时生效、观察过期、被忽略后主动性收敛。

## 10. 暂缓

眼动追踪、真正扫视物理仿真、本地视觉模型、自动兴趣锚点学习、跨屏精确注视。先做“少犯监控感错误”。
