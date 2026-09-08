# 连发合并与不阻塞输入（安卓）

2026-09-08 用户裁决：静默窗口 W=2s、生成中不打断、安卓先行（core 只加一个参数）。
上游问题：发送一条消息后「正在酝酿…」+ 发送键转圈，整轮期间发不出第二条；
且 1 输入 : 1 输出被硬绑定，而真人会用一条消息回应语义连续的几条，
回复对应的也可能是几条之前的输入。

## 1. 现状（改动前）

`ChatScreen.busy` 一个标志同时承担三件事：发送闸（`if (busy.value) return`）、
指示器（`ThinkingParticles` / 发送键换转圈）、回执等待（协程等 `bridge.chat` 返回）。
一次发送 = 一次 `bridge.chat` = 一次 `agent.handle` = 一条回复。

`agent.handle` 同步有状态、无锁，串行化一直由调用方负责：QQ 用 `asyncio.Lock`，
桌宠用 `_agent_lock`，安卓就是这个 `busy`。所以放开输入不能只删 busy。

## 2. 语义（定稿）

- **发送永不阻塞**：点发送 = 立刻落库 + 立刻上屏（乐观气泡）；输入框、发送键、
  附件按钮常驻。`busy` 不再是发送闸。
- **每角色一个 worker（进程级）**：
  - 空闲收到消息 → 等静默窗口 W=2s；窗口内又来消息并入同一批（起点不重置），
    总等待封顶 CAP=8s（一直连发也不能无限等）。
  - 窗口关闭 → 队列内全部消息合成**一轮**：一次 LLM 调用、一条回复。
  - worker 忙时新消息只入队、不打断；当前轮结束立刻开下一轮（不再等窗口，
    用户已经等过了）。
- **回复落点**：turn 在跑时新消息归下一轮，回复自然晚于输入——「回复对的是几条
  之前的消息」由既有 history（`chat.history_max_messages`，50 条）承载，
  不需要额外机制。
- **落库逐条**：N 条消息 N 行（顺序/引用/搜索/重载一致）；合并只发生在
  「本轮喂给 LLM 的文本」与「本轮只出一条回复」。

## 3. 状态机（每角色）

```
idle ──enqueue──> window(等 W，≤CAP，期间新消息并入)
                      │
                      ▼
                   running（bridge.chat_batch：逐条落库 + 一次 handle）
                      │
        队列空 ────────┴──────── 队列非空（turn 期间来的）→ 立刻再 running（不等窗口）
                      │
                      ▼
                    idle（thinking=false）
```

`thinking` 在 window 期间即为 true——「正在酝酿」覆盖「在听/在说」两态。

## 4. 接口

- **core**：`Agent.handle(..., pre_stored_msg_id: int | None = None)`
  调用方已把用户消息落库时传**末条** user 行 id；handle 不再重复 store，
  副作用（tension 记账 / info gap / 睡眠归档）沿用该 id。
  默认 `None` = 旧行为，QQ / 桌宠 / 既有测试不受影响。
- **bridge**：`chat_batch(batch_json, role)`，`batch_json = [{"text", "images": [路径]}]`
  1. 逐条 `store_message("user", ...)`（各自 `[图片]` 占位 + 各自 attachments）
  2. 合并文本 `"\n".join(texts)` → `handle(images=全部图片, pre_stored_msg_id=末条 id)`
  3. 返回 `{ok, reply, portrait, tone, energy, ids[]}`
- **Kotlin**：`TurnQueue.of(role).enqueue(Item(text, images))`；
  `thinking` / `revision` / `error` 三条 StateFlow（指示器 / 重载历史 / 错误行）。

## 5. 边界

- **进程被杀**：消息在入队时即落库；未跑完的轮由既有 `catch_up_replies`
  （2–40 分钟窗口）补回。
- **附件**：按消息各自归属落库（attachments 写各自行）；合并轮把所有图片
  作为本轮 `images` 传给多模态模型。
- **睡眠分支**：`pre_stored_msg_id` 时跳过重复 store，`archive_sleep_message`
  沿用该 id（多条连发在睡梦中合并成一句困意确认）。
- **通知**：本轮不做——回复不推通知，用户离开页面由 ON_RESUME / revision 重载。
- **指示器**：`ThinkingParticles` 由 `worker.thinking` 驱动；发送键常驻。

## 6. 验收

- `tests/test_turn_merge_20260908.py`
  1. `pre_stored_msg_id` 不重复落库 + 回复正常落 assistant；
  2. `chat_batch` 逐条落库 N 行、`handle` 只调一次、合并文本 / 图片 / 末条 id 正确。
- MuMu #2 活体：连发 3 条 → 库里 3 行 user + 1 行 assistant；生成期间输入框可用。
