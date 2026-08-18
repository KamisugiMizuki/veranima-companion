# 视觉注意力模块专项设计 v2

> 本模块是 `docs/DESIGN.md` R4 的实现规范。它解决“角色如何有限地注意环境”，不解决“如何让角色主动说话”；后者属于主动性仲裁。

## 1. 核心原则

- 注意到 ≠ 理解了 ≠ 想评论 ≠ 可以打扰。
- 鼠标位置/前台窗口是注意力证据，不是真实注意力。
- 观察默认是短期情境，不是长期记忆。
- 视觉能力的成功不是“看得更多”，而是“少犯监控感错误”。

## 2. 输入与输出

输入：`foreground_window`, `cursor_pos`, `idle_seconds`, `screen_lock`, `low_cost_frame`。

输出：

```python
AttentionEvent(
    kind="window_switch|focus_shift|habituated|away",
    region=(x0, y0, x1, y1),
    confidence=0.0,
    source="foreground|cursor|saliency|manual",
)
```

观察器另行输出：

```python
Observation(
    summary, category, confidence, sensitive_redacted,
    source_event_id, expires_at,
)
```

观察结果不直接调用 `speak`，必须交给主动性仲裁器。

## 3. 状态机

```text
away ── user activity ──> orienting
orienting ── stable focus ──> fixating
fixating ── no novelty ──> habituated
habituated ── novelty/window switch ──> orienting
任意状态 ── lock/busy/privacy block ──> away
```

鼠标停留至少 2 秒才可作为 focus evidence；鼠标高速移动不驱动连续扫视。前台窗口切换是高价值事件，但仍受隐私分类和主动性策略约束。

## 4. 感知层

- L0：Win32 `GetForegroundWindow`, `GetWindowTextW`, `GetCursorPos`, idle/lock。
- L1：Pillow 降采样帧差、对比和结构显著度。
- L2：当前焦点区域裁剪；只在新窗口/新奇显著事件或低置信度需要时调用多模态 LLM。
- 不实现从屏幕自动学习永久兴趣锚点；兴趣来自角色卡和共同经历。

## 5. 观察预算

- 感知事件不受 LLM 冷却影响。
- 同一窗口/区域在短期缓存有效期内不重复理解。
- 新窗口先用标题/进程元数据；图像理解按事件价值提升。
- 理解冷却与主动发言冷却分离；主动发言更严格。
- 所有观察有 `reason`, `confidence`, `expires_at`，用于调试和过期。

## 6. 主动性接口

```text
Observation + CharacterInterest + SharedEpisode + Presence + SocialAppetite
  → ProactiveDecision(allow, reason, message_intent)
```

`allow=false` 是正常结果，必须可解释。视觉模块不包含角色口吻生成，不包含发送动作，不包含长期记忆写入。

## 7. 隐私策略

默认敏感分类：密码管理器、支付、私人聊天、视频会议、锁屏、系统安全界面。命中后不发送图像；窗口标题也只保留分类，不保留完整文本。用户暂停开关优先级最高。

## 8. 验收

1. 前台窗口切换产生事件，但不自动产生发言。
2. 鼠标停留能影响 focus，快速移动不会造成抖动事件风暴。
3. 连续打字不持续调用视觉 LLM。
4. 同一区域习惯化后不重复观察。
5. 观察结果过期后不再注入对话。
6. 用户忙碌、拒绝或忽略后，主动性仲裁能抑制发送。
7. 敏感窗口不发送图片，暂停开关即时停止观察。
8. 日志可回答：看到了什么信号、为什么理解、为什么发言或没发言。

## 9. 暂缓

眼动追踪、真正的扫视物理仿真、本地视觉小模型、自动锚点学习、跨屏精确注视。当前目标是一个有分寸的注意力系统，不是视觉研究项目。
