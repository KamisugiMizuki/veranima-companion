# IMAGE_STICKER_LIFECYCLE_SPEC：用户图片理解、收藏治理与表情回发

> 状态：实现版 v1.0（2026-08-27）；IMG-STK-1～3 已落地并通过项目测试，IMG-STK-4 的真实 NapCat/远程多模态验收仍未完成。
> 审计基线：`41522d8`；当前专项测试与 UI/WS 契约测试以实际 pytest 输出为准。
> 关系：补充 `IMAGE_MESSAGE_SPEC.md` 的持久化边界，并在实现后取代 `QQ_STICKER_SPEC.md` 中较早的自动收藏、发送匹配与生命周期约定。
> 范围：QQ OneBot 私聊、Electron 桌宠聊天图片、`ImagePayload`、`StickerLibrary`、设置页与用户删除权。

## 0. 结论与设计决议

本规范先固定十条决议，后文只展开实现，不再发明第二套系统。

1. **看图不等于收藏。** 所有合规图片可供当前轮多模态理解；只有进入表情候选并完成授权的静态图才成为可复用表情。
2. **新图片默认进入“待审核”，不直接进入可发送库。** 用户可在设置页批准/拒绝；明确选择“自动收藏”后才恢复旧行为。
3. **桌宠历史图片与共享表情库严格分离。** `chat-images/` 只服务聊天历史；不因粘贴图片而自动学习成表情。
4. **表情按 QQ 用户隔离。** 即使以后扩大白名单，也不能把 A 的图片发给 B；角色卡切换默认仍共享同一用户的表情库。
5. **v1 只在用户触发的普通 QQ 回复后回发表情。** 主动问候、提醒、任务结果、错误回复和桌宠 TTS 不附带表情；真实使用证明有需求后再开放。
6. **不新增数据库或服务。** 扩展现有 `data/stickers/index.json` 到 schema v2，继续使用完整 SHA-256 文件名和原子替换。
7. **发送不新增一次 LLM 调用。** 复用结构化 Reply 的 tone、最终回复文本与固定标签映射；失败时宁可不发。
8. **用户可控项全部进入桌宠设置页。** 枚举用下拉框，目录用原生目录选择器，不让用户手填本地路径或内部枚举。
9. **删除是完整级联。** 删除表情必须同时删原图、索引和待审核记录；清空聊天历史不隐式删除共享表情库，界面必须明确解释两者区别。
10. **当前 7 张表情不丢。** 迁移为 `legacy_auto` 活跃条目并显示“历史自动收藏”，由用户批量复核。

## 1. 现状基线与问题

### 1.1 当前生产链

```text
QQ 白名单消息
→ OneBot image/file 段解析（最多 4 张）
→ URL/本地路径/get_image 解析
→ ImagePayload 安全校验
→ Agent 当前轮多模态理解；SQLite 只写 [图片]
→ 当轮结束后后台 LLM 标注
→ 静态 + dHash 未重复 + is_sticker 字面 true
→ data/stickers/<sha256>.<ext> + index.json
→ 后续普通 QQ 回复后按 mood/scenario 检索
→ [CQ:image,file=<absolute path>]
```

桌宠链路独立：

```text
剪贴板图片
→ Electron 整批校验
→ userData/chat-images/<uuid>.<ext>
→ chat.json 保存相对引用
→ 当前轮 data URL 送 Agent
→ 历史淘汰/清空时 GC
```

### 1.2 2026-08-26 实测事实

- `qq.stickers.enabled=true`，当前库 7 张、文件与索引一致、无孤儿文件。
- `core.log` 有 7 次成功收藏与 5 次真实回发记录。
- 当前发送情绪检测只产出“开心/难过”；标注却允许 10 类情绪。
- 7 张中有 3 张不带“开心/难过”，只能依赖极低概率的 scenarios 字面命中。
- `StickerLibrary.delete()` 存在，但没有 CLI、QQ 命令、设置页或其他生产调用者。
- 索引没有来源消息、用户范围、授权方式、状态、最后使用时间或过期时间。
- 桌宠设置页没有 `stickers`、`image_roots`、trusted proxy 等控制。

### 1.3 问题分级

| 优先级 | 问题 | 结果 |
|---|---|---|
| P0 | 普通照片/截图只靠 LLM 的 `is_sticker` 判定 | 误判后会未经逐张确认长期保存 |
| P0 | 没有候选审核和来源追溯 | 无法解释、撤销或按消息/用户删除 |
| P1 | 10 类标注只消费 2 类 | 部分表情“收藏了但几乎不会发送” |
| P1 | 删除方法没有产品入口 | 用户无法行使遗忘权 |
| P1 | 表情和图片安全设置未进入设置页 | 运行行为依赖手改 YAML |
| P1 | 全局库不按用户过滤 | 白名单扩展后可能跨用户回发 |
| P2 | `record_use()` 在 OneBot 发送前调用 | 发送失败也会增加使用次数 |
| P2 | 没有概率、回复间隔和严肃语境抑制 | 连续情绪回复可能每轮都附图 |
| P2 | `StickerLibrary` 自身没有锁 | 并发入库/发送/删除时索引存在竞态 |
| P2 | dHash 对低梯度图片判重过强 | 不同图片可能被当成重复 |
| P2 | 当前 dHash 实测为 63 位，`hamming()` 又用 `zip` 静默忽略长度差 | 算法注释与真实判重契约不一致 |

## 2. 目标与非目标

### 2.1 目标

1. 保留当前多模态理解能力，不让审核流程阻塞首响。
2. 普通图片不进入长期共享表情库；待审核文件有明确 TTL。
3. 每张可发送表情都能回答：谁发的、何时收到、如何授权、为何匹配、发送过几次。
4. 10 类标注都存在确定性的消费路径，但不为匹配额外调用 LLM。
5. 用户能在桌宠设置页完成配置、审核、禁用、删除和迁移目录。
6. 所有路径/SSRF/MIME/大小安全边界保持 fail-closed。
7. QQ 第一条文字回复延迟不因表情标注增加。

### 2.2 非目标（v1 暂缓）

- 不支持群聊表情共享、云同步、跨设备同步或多人协作库。
- 不抓取 QQ 商城表情、face/market 表情二进制；原生 face 继续作为文本语义输入。
- 不把主动消息、三餐提醒、视觉主动或 Hermes 任务结果接入表情发送。
- 不做 OCR、人物识别、NSFW 审查或独立视觉分类模型；项目既有内容边界不在本规范内改变。
- 不做角色卡专属表情库；同一用户换卡后沿用表情事实。
- 不为表情检索引入向量库、数据库表或新依赖。

触发升级条件：真实使用中出现群聊、多用户、跨设备或固定规则无法完成的语义检索需求，再单独立项。

## 3. 三类图片的边界

| 类型 | 原始图保存位置 | 保存条件 | 生命周期 | 可作为表情发送 |
|---|---|---|---|---|
| 当前轮 QQ/桌宠图片 | 内存中的 `ImagePayload` | 通过统一安全校验 | 当轮请求和后台分类结束后释放 | 否 |
| 桌宠历史图片 | Electron `userData/chat-images/` | 用户发送成功前整批校验并落盘 | 500 条历史 GC；清空窗口历史时删除 | 否 |
| 表情候选/活跃表情 | `data/stickers/` | 静态、未重复、分类为表情；按模式审核 | pending TTL 或用户主动删除 | 仅 active 可发送 |

铁律：

- SQLite `messages`、FTS5 和长期记忆只写 `[图片]` 占位，绝不写 base64。
- `chat-images/` 引用不能被 `StickerLibrary` 扫描或自动导入。
- 删除桌宠历史不得删除共享表情；删除共享表情不得破坏历史图片。
- 候选文件即使已落盘也不是“收藏完成”；`status=pending` 永远不参与发送。

## 4. 生命周期状态机

```text
received
  └─ validate fail ───────────────→ dropped
  └─ validate pass ───────────────→ transient
                                      ├─ animated ─────────→ understood_only
                                      ├─ duplicate active ─→ duplicate
                                      ├─ classifier fail ──→ not_collected
                                      ├─ is_sticker=false ─→ not_collected
                                      └─ is_sticker=true
                                           ├─ learning=off ───→ not_collected
                                           ├─ learning=review ─→ pending
                                           ├─ learning=auto ───→ active(auto)
                                           └─ explicit save ───→ active(explicit)

pending ─ approve → active(review_approved)
        ├ reject  → deleted
        └ TTL     → deleted

active ─ disable → disabled ─ enable → active
       └ delete  → deleted
```

### 4.1 不变量

1. 只有 `active` 进入候选检索。
2. `pending/disabled/deleted/broken` 永远不发送。
3. 动图只做当前轮理解，不进入 pending 或 active。
4. 每次状态迁移先校验文件与 scope，再原子写索引。
5. 删除成功后原图和索引中都不得再出现该条目。
6. OneBot 发送成功后才更新 `uses/last_used_at`。
7. 任何异常默认落到“不收藏/不发送”，不能用旧值兼容回填。

## 5. 接收、分类与授权

### 5.1 接收边界（保留现有实现）

继续复用 `ImagePayload`：PNG/JPEG/GIF/WebP、10MB、40MP、magic/MIME 一致、Pillow 完整解码、每轮最多 4 张。QQ 本地路径必须在 `image_roots`；HTTP 关闭重定向、校验公网 IP、固定 Host/SNI；Clash fake-IP 仅对内建 QQ CDN allowlist 开放。

adapter 只负责解析和初检；Agent 当前轮仍二次校验。失败时：

- 有文字：降级为纯文本回复；
- 纯图片：不调用 Agent；
- 不生成表情候选。

### 5.2 分类契约

后台复用当前 Agent LLM lock，使用同一多模态模型，输出固定 JSON：

```json
{
  "is_sticker": true,
  "kind": "sticker",
  "confidence": 0.93,
  "meaning": "表示无奈地求助",
  "moods": ["无奈", "卖萌"],
  "scenario_tags": ["request_help", "comfort"],
  "scenarios": ["遇到困难向对方求助"]
}
```

约束：

- `is_sticker` 必须是字面 boolean `true`；其他类型一律 false。
- `kind` 枚举：`sticker/photo/screenshot/unknown`；只有 `sticker` 可继续。
- `moods` 只能从：开心、难过、生气、无语、惊讶、鼓励、调侃、无奈、敷衍、卖萌。
- `scenario_tags` 只能从：`agreement/praise/affection/teasing/comfort/failure/surprise/refusal/request_help/fatigue/embarrassment/celebration`。
- `confidence` 只用于审核排序和 auto 附加门槛，不能替代用户授权。
- 缺字段、越界枚举、解析失败：不收藏。

`learning_mode=auto` 还必须满足 `confidence >= 0.85`；阈值是内部安全常量，不暴露为调参框。默认 review 不依赖 confidence 自动批准。

### 5.3 授权模式

设置页下拉框：

| 值 | 行为 | 推荐 |
|---|---|---|
| `off` | 只看图，不运行后台表情分类，不落候选 | 隐私优先 |
| `review` | 分类为表情后进入 pending，用户批准才 active | **默认** |
| `auto` | 严格分类通过后直接 active；界面明确提示风险 | 兼容旧行为 |

明确的同图指令可形成 `consent=explicit`，但必须处于“消息携带图片且意图无歧义”的上下文；不允许从过去聊天猜测同意。自然语言不确定时仍进入 pending，不自动扩大授权。

### 5.4 待审核 TTL

pending 默认保存 7 天，设置页提供 1 天 / 7 天 / 30 天下拉选项。启动时和每日整理时删除到期 pending 原图与索引。拒绝操作立即删除，不留图片副本；日志只记录条目短 ID 和动作，不记录图像内容或用户原文。

## 6. 索引 schema v2

继续使用单个 `index.json`，不新增 SQLite 表：

```json
{
  "schema_version": 2,
  "entries": [
    {
      "id": "sha256-hex",
      "sha256": "sha256-hex",
      "dhash": "64-bit-string",
      "dhash_version": 2,
      "file": "<sha256>.png",
      "status": "pending",
      "owner_scope": "qq:<user_id>",
      "source": {
        "channel": "qq",
        "platform_message_id": "12345",
        "received_at": "ISO-8601"
      },
      "consent": "review_pending",
      "meaning": "...",
      "moods": ["无奈"],
      "scenario_tags": ["request_help"],
      "scenarios": ["..."],
      "confidence": 0.93,
      "uses": 0,
      "last_used_at": null,
      "created_at": "ISO-8601",
      "approved_at": null
    }
  ]
}
```

### 6.1 字段规则

- `id/sha256` 使用内容完整 SHA-256；禁止截断命名。
- `dhash` 只做近似判重，不做身份主键。v2 修正为标准 64 位：resize `(size+1, size)` 后做 8×8 横向比较。
- `dhash_version=2` 必填；`hamming()` 必须拒绝不同长度，不能继续让 `zip` 静默截断。
- `owner_scope` 必填；发送前严格等于当前 QQ sender scope。
- source 只保存追溯 ID 和时间，不复制聊天文本、昵称或图片说明。
- `consent` 枚举：`explicit/review_pending/review_approved/auto/legacy_auto`。
- `status` 枚举：`pending/active/disabled`；删除不保留正文 tombstone。
- v1 的 `hash` 仅用于识别旧 schema；v2 从原图重算标准 64 位 `dhash`，旧值只留在迁移备份。
- 索引手工损坏、文件越界或缺失时条目标记为运行时 broken 并跳过，不阻塞聊天。

### 6.2 并发与持久化

在现有 `StickerLibrary` 内增加一个 `threading.RLock`，覆盖 load/add/status/delete/record_use/save。仍使用：

- 文件 `open("xb")` 排他创建；
- index 临时文件 + `fsync` + `os.replace`；
- 状态变更时先写图片（若需要），后写索引；失败删除本次新文件；
- 不引入 Repository/DAO/事件总线等额外抽象。

## 7. 回发选择与抑制

### 7.1 触发范围

v1 只在以下路径选择表情：

```text
用户 QQ 私聊 → Agent 普通回复成功 → render_im 成功
→ build_sticker_query → gate → score → probability
→ OneBot send image 成功 → record_use
```

以下情况直接 suppress：

- 当前不是 QQ 用户触发的即时回复；
- `send_rate=off`；
- 没有同 `owner_scope` 的 active 条目；
- Reply 处于 degraded/error/task/approval/system-notice；
- Hermes 任务确认、审批、失败和结果转述；
- 当前关系冲突处于高张力修复；
- 用户表达严重痛苦、现实风险或要求认真回答；
- 本轮已经发送图片附件；
- 全局回复间隔或单表情冷却未满足。

主动消息和桌宠通道在 v1 固定 suppress，不提供隐藏开关。

### 7.2 不新增 LLM 调用的 query 构造

新增一个纯函数（可放在 `core/stickers.py`，不新建接口层）：

```python
build_sticker_query(reply_obj, rendered_text, user_text) -> {
    "moods": set[str],
    "scenario_tags": set[str],
    "explicit_request": bool,
    "suppress": bool,
}
```

信号优先级：

1. 结构化 Reply segment 的 `tone`；
2. 当前 `ResponsePlan.intent` / 已有关系张力和任务状态；
3. 最终可见回复文本规则；
4. 当前用户文本规则作为情境补充。

完整情绪消费表：

| mood | 最低限度触发信号示例 |
|---|---|
| 开心 | 高兴、开心、太好了、哈哈、庆祝 tone |
| 难过 | 难过、伤心、委屈、低落 tone |
| 生气 | 生气、火大、恼火、不满 tone |
| 无语 | 无语、沉默、离谱、说不出话 tone |
| 惊讶 | 居然、竟然、真的假的、惊讶 tone |
| 鼓励 | 加油、相信你、可以的、support tone |
| 调侃 | 调侃、开玩笑、逗你、teasing tone |
| 无奈 | 没办法、算了、无奈、叹气 tone |
| 敷衍 | 嗯嗯、行吧、知道了且当前表达计划允许 |
| 卖萌 | 撒娇、可爱、亲昵、playful tone |

规则只产固定标签，不直接选择图片；匹配失败即不发。不要复用当前只支持“开心/难过”的 `_detect_emotion()` 作为唯一来源。

### 7.3 候选评分

先做硬过滤：`status=active`、scope 相同、文件存在、静态、未 disabled、冷却满足。

再评分：

| 信号 | 分值 |
|---|---:|
| scenario tag 精确交集 | +4 |
| mood 精确交集 | +3 |
| meaning/scenarios 中固定关键词命中 | +1 |
| 当前从未使用 | +1 |
| 最近 5 次中使用过 | -3 |
| 当前表情刚在全局间隔内发过 | 直接过滤 |

最低发送阈值为 3；同分时按 `uses` 少、`last_used_at` 早、`created_at` 早排序。随机只在完全同分时用于打散，并允许测试注入 RNG。禁止把回复前 120 字与整句 scenarios 做原样包含作为主要匹配。

### 7.4 频率与冷却

设置页下拉：

| send_rate | 匹配后发送概率 |
|---|---:|
| off | 0% |
| low | 15% |
| normal | 30%（默认） |
| frequent | 60% |
| always | 100% |

另设“最少间隔回复数”下拉：1 / 3（默认）/ 5 / 10。显式用户请求可绕过概率和全局间隔，但不能绕过 scope、文件安全和 disabled 状态。

adapter 只在内存中保留最近 5 个成功发送的条目 ID，用于“最近 5 次”降权；进程重启后清空即可，不新增持久化历史。

每轮最多一张。发送 OneBot 图片成功后再增加 `uses` 并写 `last_used_at`；异常时不改计数。

## 8. 用户操作与删除权

### 8.1 设置页：新增“图片与表情”页

所有用户可控项必须形成完整链：

```text
settings.html 控件
→ settings-renderer.js 读写
→ preload IPC
→ main.js WS request
→ PetServer get_config/save_config 或 sticker action
→ config.yaml / StickerLibrary
→ adapter 运行时消费者
```

控制项：

| 控件 | 类型 | 配置/动作 |
|---|---|---|
| 表情学习模式 | 下拉 | off / review / auto |
| 发送频率 | 下拉 | off / low / normal / frequent / always |
| 最少回复间隔 | 下拉 | 1 / 3 / 5 / 10 |
| 待审核保留时间 | 下拉 | 1 天 / 7 天 / 30 天 |
| 表情库目录 | readonly + 原生目录浏览框 | `qq.stickers.dir` |
| NapCat 图片目录 | 多目录列表 + 浏览/移除 | `qq.image_roots` |
| Clash fake-IP | 下拉 | 关闭 / 仅内建 QQ CDN |
| QQ CDN allowlist | 只读说明 | 不允许用户随意输入任意 host |
| 表情库上限 | 下拉 | 50 / 100 / 300 / 不限制 |
| 测试图片链路 | 按钮 | 校验目录权限、加载索引、显示 active/pending/broken 计数 |

枚举不得使用自由文本。本地路径不得手填。保存后复用现有“重启核心”能力使 adapter 重建；不要维护第二份运行时配置真值。

### 8.2 待审核区

每条 pending 显示：

- 本地缩略图；
- 含义、情绪、情境标签；
- 收到时间和来源通道；
- “批准”“拒绝”“重新标注”按钮；
- 情绪与情境使用固定多选，不允许写入任意内部标签。

批准：`status=active`、`consent=review_approved`、写 `approved_at`。
拒绝：删除原图和条目。
重新标注：复用 Agent lock，旧标注在新结果成功前保持；失败不破坏候选。

### 8.3 活跃库管理

活跃条目支持：

- 启用/停用；
- 删除单张；
- 批量删除 pending；
- 清空全部表情（系统确认框必须明确“不影响桌宠聊天图片和 SQLite 对话”）；
- 查看 uses/last_used_at/consent；
- 对 `legacy_auto` 批量批准或删除。

删除操作必须由主进程显示系统确认框；renderer 不能仅凭 DOM 点击直接删文件。

### 8.4 QQ 侧最小操作

v1 只实现有上下文的明确动作：

- 图片同轮明确要求收藏：可记录 `explicit`；
- “不要收藏刚才那张”：仅作用于最近 pending，删除并回执；
- “删除上一张表情”：必须再次确认，不能直接删除。

普通聊天中的“这个挺适合做表情”属于歧义，不作为授权。自然语言操作找不到唯一条目时，引导用户去设置页，不猜。

## 9. 配置 schema

建议配置：

```yaml
qq:
  image_roots: []
  trusted_image_proxy: true
  image_proxy_hosts:
    - multimedia.nt.qq.com
    - multimedia.nt.qq.com.cn
  stickers:
    enabled: true                # 旧兼容总开关；迁移后由下面两项派生
    dir: data/stickers
    learning_mode: review        # off | review | auto
    send_rate: normal            # off | low | normal | frequent | always
    min_reply_gap: 3
    pending_ttl_days: 7
    max_items: 100               # 0 = 不限制
```

约束：

- 保存端拒绝未知枚举、负数和根目录越界；不能依赖 renderer 校验。
- `image_proxy_hosts` v1 使用内建集合，设置页只控制 trusted proxy 开关。
- `max_items` 达上限时不删除 active；停止新增并在设置页提示。pending TTL 清理后可恢复。
- `dir` 变更必须选择现有目录或空目录；若旧库非空，显示“迁移现有库/使用新空库/取消”。迁移用同父目录 staging + 校验 + 原子切换，失败保留旧库。

## 10. 生产代码改造点

遵循现有结构，最少改动以下文件：

### `src/veranima/core/stickers.py`

- schema v1→v2 迁移；
- 内部 `RLock`；
- `list_entries(status, owner_scope)`；
- `add_candidate()/approve()/disable()/delete()/cleanup_pending()`；
- `find_for_query()` 替代只支持两类情绪的检索；
- `record_use()` 接收成功时间；
- 保持 SHA-256 文件和原子 index 写入；把现有 63 位 dHash 修正为带版本的标准 64 位。

不建立 interface、repository 或新数据库表。

### `src/veranima/adapters/qq.py`

- `_collect_images()` 同时保留每张图片的 OneBot source metadata；
- `_schedule_sticker_ingest()` 传 `owner_scope/source/learning_mode`；
- `_ingest_stickers()` 产生 pending/active，而非总是 active；
- `_pick_sticker_for_reply()` 接收 `reply_obj/rendered/user_text/uid`；
- OneBot send 成功后才 `record_use()`；
- 退出仍 shield + 等待后台标注 worker。

### `src/veranima/core/agent.py`

- 扩展标注 JSON 契约并严格枚举；
- 不改变普通 `handle(images=...)` 和 `[图片]` 存储规则；
- 不在 Agent 中持久化表情文件。

### `src/veranima/qq.py`

- 从新配置构造 `StickerLibrary`；
- `learning_mode=off` 且 `send_rate=off` 时不构造库；
- 仍与桌宠核心共享 Agent lock。

### `src/veranima/pet_server.py`、`pet/main.js`、`pet/preload.js`、设置页

- 扩充 config 读写白名单；
- 新增 list/approve/reject/disable/delete/reannotate WS/IPC；
- 图片预览仅允许库根目录内文件；不把全库转换成 base64；
- disconnect/窗口销毁时拒绝 pending IPC promise。

## 11. 迁移与回滚

### 11.1 index v1 → v2

首次加载时：

1. 校验当前 index 和每个文件；
2. 复制为 `index.v1.backup.json`；
3. 逐条迁移：
   - 不直接复制旧 `hash`；从原文件重算标准 64 位 `dhash`，写 `dhash_version=2`；
   - 由文件内容计算 `sha256/id`；
   - `status=active`；
   - `consent=legacy_auto`；
   - 若白名单恰好一个用户，赋该 `owner_scope`；
   - 白名单为 0 或多用户时标为 disabled，要求设置页分配范围后再启用；
   - 保留 meaning/moods/scenarios/uses/created_at；
4. 在临时 index 完成全量校验；
5. 原子替换为 schema v2；
6. 不移动现有 7 张图片。

迁移幂等：schema_version=2 时不重复执行。失败时继续只读加载 v1，但关闭收藏和发送并给设置页错误状态，不能悄悄使用半迁移库。

### 11.2 配置迁移

- 旧 `stickers.enabled=false` → `learning_mode=off, send_rate=off`。
- 旧 `stickers.enabled=true` → `learning_mode=review, send_rate=normal`。
- `enabled` 保留一版兼容读取；新设置页只写新字段。
- 回滚旧版本时可继续读取旧字段和文件；`index.v1.backup.json` 只在用户明确回滚时恢复。

## 12. 安全、隐私与可观测性

### 12.1 隐私边界

- 表情库和候选目录继续受 `.gitignore` 的 `data/` 覆盖。
- 不保存聊天原文、昵称、base64、远端 URL 查询串或 API 凭据。
- source 只存通道、消息 ID、用户 scope 和时间。
- pending TTL 到期、拒绝、删除必须移除原图和索引。
- 同 user scope 才能回发；legacy_global 不发送。
- 清空窗口历史和清空表情库必须是两个独立确认动作。

### 12.2 安全边界

- 继续使用 ImagePayload MIME/magic/Pillow/大小/像素校验。
- 表情预览和发送路径必须 resolve 后直接位于库根目录。
- HTTP SSRF 规则不因表情学习模式放宽。
- index 手工注入越界文件、符号链接或缺失文件时跳过。
- renderer 只传条目 ID；文件删除和状态变更全部在核心/主进程校验。

### 12.3 日志与指标

只记录：

- `image_received/validated/dropped` 数量与原因；
- `sticker_classified_pending/active/rejected/duplicate/expired`；
- `sticker_sent/send_failed`；
- 短条目 ID、scope 摘要、状态；
- 不记录原图、完整路径、用户文本或完整 QQ 号。

设置页展示本地统计：active/pending/disabled/broken、总大小、最近发送时间。统计必须从索引和文件系统即时计算，不能另建计数真值。

## 13. 行为验收

### 13.1 入库与授权

1. 普通照片返回 `is_sticker=false`：不创建文件/索引。
2. 静态表情 + review：创建 pending，但 `find_for_query()` 永远不可见。
3. 批准 pending：成为 active，可被同 scope 检索。
4. 拒绝/TTL/删除：图片与索引同时消失。
5. 动态 GIF/WebP：当前轮 Agent 能看，库中始终为 0。
6. 近似重复：不重复标注和保存；原条目来源与授权不被覆盖。
7. Agent 标注缺字段、字符串布尔、非法 enum：fail-closed。
8. library 满额：不删旧表情，不新增长期文件。
9. v2 dHash 固定 64 位；不同长度的 hamming 输入必须拒绝。

### 13.2 scope 与发送

1. A 用户图片不进入 B 用户候选。
2. 每个 10 类 mood 至少有一条确定性命中测试。
3. “收到，好的”不误发；严肃/任务/错误/高张力场景 suppress。
4. explicit request 可绕过概率但不能跨 scope。
5. 最少回复间隔生效；概率用注入 RNG/固定时钟测试。
6. 每轮最多一张；主动消息和桌宠回复为 0 张。
7. OneBot 发送失败时 `uses/last_used_at` 不变；成功后再持久化。
8. 缺失/越界文件跳过且不阻塞文字回复。

### 13.3 设置链路

每个设置项都必须断言：

```text
DOM id 存在
→ renderer 读写
→ preload/main IPC 存在
→ PetServer save_config 白名单消费
→ YAML 读回一致
→ 新 adapter/StickerLibrary 实际行为变化
```

路径选择使用原生 dialog；学习模式、发送频率、TTL、间隔、上限全部为 select。设置页审核动作使用条目 ID，不能接收任意文件路径。

### 13.4 并发与故障

- 两条并发 QQ 消息、后台标注、发送和设置页删除并发时 index 仍是合法 JSON。
- 在图片写后/index 写前注入失败，不能遗留无索引新文件。
- index 原子替换失败保留旧索引。
- 退出等待已进入 `to_thread` 的标注 worker，不让旧线程越过新 Agent 生命周期。
- v1→v2 迁移任一步失败都能从 backup 恢复。

### 13.5 真实生产验收

最终不能只报 pytest：

1. 使用真实 NapCat 私聊发送：普通照片、截图、静态表情、GIF、重复压缩图、5 张连发。
2. 验证普通照片/截图不进 active；静态表情进入 pending；GIF 不落库；第 5 张丢弃。
3. 桌宠设置页批准、拒绝、删除各一张，并重启核心确认状态持久。
4. 让 Agent 生成 10 类 mood 对应回复，确认候选可达；严肃回复不发。
5. 用错误 OneBot 发送路径模拟失败，确认 uses 不增。
6. 更换角色卡确认同用户库可用；临时加入第二测试 scope 确认隔离。
7. 清空桌宠聊天历史，确认共享表情库不受影响；清空表情库，确认桌宠历史图不受影响。
8. 日志检查不出现完整 QQ、URL 查询串、图片正文或绝对表情路径。

## 14. 分阶段实施与停点

### IMG-STK-1：schema、锁与迁移

- v2 索引、RLock、v1 迁移和删除级联；
- 暂不改变发送规则；
- 迁移当前 7 张并行为测试通过后停点。

### IMG-STK-2：review 模式与设置页

- learning_mode、pending TTL、审核/删除 UI、目录浏览、图片根目录；
- 新图片默认 pending；
- 设置全链行为测试通过后停点。

### IMG-STK-3：完整匹配与频率 Gate

- 10 类 mood、scenario_tags、scope、严肃语境 suppress、概率和间隔；
- 成功发送后计数；
- 定向测试与全量 pytest 后停点。

### IMG-STK-4：生产验收与文档同步

- 真实 NapCat + 当前远程多模态模型跑 §13.5；
- 同步更新 `QQ_STICKER_SPEC.md`、`IMAGE_MESSAGE_SPEC.md`、`config.example.yaml` 和设置页契约；
- 明确标记实机未覆盖项，不以测试通过代替生产验收。

每阶段只改该阶段所需文件；不同时重构 Agent、多模态协议或 Electron 聊天历史。

## 15. 完成定义

只有全部满足才称为完成：

- 新 QQ 图片默认不会未经审核成为 active；
- 当前 7 张无丢失迁移为可审计条目；
- 用户能在桌宠设置页配置、审核、停用、删除和清空；
- 10 类标签有可观察的发送消费路径；
- scope、概率、间隔、严肃语境和每轮一张 Gate 生效；
- 桌宠历史图片与表情库互不污染；
- OneBot 失败不虚增 uses；
- pending TTL 与删除无残留；
- 定向测试、全量 pytest、Node 语法检查、真实 NapCat 验收全部通过；
- 文档状态按“已实现/实机未验证/暂缓”诚实更新。

## 16. 设计依据（仓库内）

- `docs/images/IMAGE_MESSAGE_SPEC.md`：统一 ImagePayload 与 Electron/QQ 图片边界。
- `docs/images/QQ_STICKER_SPEC.md`：现有静态表情库、标注和发送契约。
- `src/veranima/adapters/qq.py`：真实接收、后台标注与回发调用链。
- `src/veranima/core/image_payload.py`：MIME/magic/大小/像素安全边界。
- `src/veranima/core/stickers.py`：dHash、SHA-256 文件、index 原子写入与删除实现。
- `src/veranima/core/agent.py`：当前轮图片输入、`[图片]` 占位和 LLM 标注。
- `pet/main.js` / `src/veranima/pet_server.py`：桌宠 chat-images 与 WS 生命周期。
