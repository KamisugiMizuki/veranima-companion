# 微信式多角色界面 + 好友动态（Moments）设计

> 状态：设计定稿（v2，§7 七项已全部经用户裁决，可动工）
> 日期：2026-09-01
> 来源：用户点子 + DeepSeek 分享稿 kk11j6dze9w54o04m（动态七型 D01-D07、角色独立设置五组）
> 范围：安卓端为主战场；PC/QQ 端不受本次改动影响

## 0. 一句话

底栏三页（聊天 / 好友动态 / 设置），聊天页=角色列表（各角色独立会话），
好友动态=角色虚拟生活的自然溢出（可赞可评），每个角色独立设置主动/动态行为。
架构从「单角色皮肤轮播」升级为「多角色共存、各自经营与你的关系」。

## 1. 现状盘点（本设计落点，全部实测存在）

| 已有件 | 与新版关系 |
|---|---|
| roster（agent_state.relationship.roster，按卡名记七维+依恋） | 直接当「角色×用户关系表」用，不重建 |
| user_profile 表（角色无关画像） | 即设计稿的 Global User Profile，保留共享 |
| user_nicknames 表（role_id 隔离称呼账） | 隔离方向已验证正确，沿用 |
| schedule_runtime 快照（relationship.virtual_schedule_runtime 单槽） | 需挪进 roster 条目（每角色各一份作息/睡眠态） |
| messages 表（无 role 维度，全角色同一条时间线） | 加 role_id 列——会话历史按角色隔离的核心迁移 |
| virtual_life_events（日终摘要，带 role_id） | D01 日程衍生动态的直接数据源 |
| proactive tick 引擎 + RITUAL_SOURCES + 待织池 + 合并窗口 | 动态生成器复刻同款结构（收集→织→入库），主动消息管线零改动 |
| CompanionService 轮询 drain_pending | 未读角标搭车；MVP 只轮询活跃角色（省电） |
| Galaxy UI（黑白极简 + Settings NavHost + 详情页组件库） | 三 tab 骨架与全部视觉语言复用 |

## 2. 数据架构：什么隔离、什么共享

**原则**：与你有关的客观事实共享（画像、你曾说过的经历）；每角色与你的
关系私产隔离（历史、七维、作息、称呼、承诺、动态）。

```
共享层（现状保留）
├── user_profile            用户画像（角色无关）
├── memories 记忆库         单脑语义/情节记忆，跨角色可召回（裁决 Q1：共享，凛能想起你和由岐说过的事）
├── messages(role_id=NULL→'shared'?)  ※见迁移策略 §3.1
└── llm/embedding/search    API 配置

角色私产层（隔离）
├── messages.role_id        会话历史：凛的聊天窗只出现凛说的
├── roster[角色]            七维+依恋（已有）
├── roster[角色].schedule_runtime   作息/睡眠态/偏移（从单槽挪入）
├── user_nicknames          称呼账（已有）
├── promise（meta 加 role_id）    承诺账本按角色记账（裁决 Q2：谁听见谁持有谁提醒）
└── moments / role_settings / role_reads   新增三表
```

新增表：

```sql
moments(            -- 动态本体（裁决 Q3：MVP 仅角色发布，无用户动态入口）
  id, role_id,      -- 'user'=用户动态（若开放）
  content, kind,    -- D01..D07 类型标记
  source_ref,       -- 溯源（event_id/memory_id/活动 id），可空
  likes INTEGER DEFAULT 0, created_at,
  visible INTEGER DEFAULT 1          -- 角色设置关→旧动态仍可见；删=另论
);
moment_interactions(                 -- 赞/评流水（幂等键防重复赞）
  id, moment_id, actor,              -- 'user' 或 role_id
  kind,                              -- like/comment/reply
  content, created_at,
  UNIQUE(moment_id, actor, kind)     -- like 去重用；comment 多条→kind 带序号 ※实现时定
);
role_settings(
  role_id PRIMARY KEY, config TEXT   -- JSON：见 §5 设置 schema
);
role_reads(
  role_id PRIMARY KEY, last_read_at TEXT   -- 未读角标记账点
);
```

## 3. 多角色运行时：bridge 注册表

一个进程内 **N 个 Agent 实例共享同一个 MemoryStore**（Agent 本体只是
prompt 组装+对话状态，内存占用可忽略；embedding/LLM 客户端复用）。

```
bridge.agents = {role_id: Agent}      # 懒加载：打开某角色会话才 boot
bridge.chat(role_id, text)            # 消息落 messages(role_id=...)
bridge.history(role_id)               # 按角色过滤（现状全局读→加 WHERE role_id）
bridge.tick/drain_pending             # 活跃角色实时驱动；其余每日凌晨批量补（裁决 Q4）
```

### 3.1 迁移策略（旧库）

- messages 加列 `role_id TEXT NOT NULL DEFAULT ''`；存量行全部标为
  **凛的 role_id**（裁决 Q7：旧世界=凛时代，干净利落）。
- roster 单槽 `virtual_schedule_runtime` 读到时若挂在旧位置→原位继续读，
  下一次 persist 写进 roster 条目（软迁移，无 schema 变更）。
- 旧库升级失败路径全部静默回退单角色行为，不炸启动（同款 owner 兼容哲学）。

## 4. 好友动态引擎（核心）

### 4.1 类型体系 → 项目数据源映射（先对表，缺源的进 §7 裁决）

| 型 | 设计稿定义 | 本项目数据源 | 状态 |
|---|---|---|---|
| D01 日程衍生 | 当天日程事件引发的感受 | virtual_life_events 日终摘要（已有，含有效分钟/中断） | ✅ 有源 |
| D02 环境感知 | 天气/季节感叹 | 虚拟天气：卡内城市+日期 确定性伪随机（§4.4） | ✅ 裁决 Q5 |
| D03 情绪宣泄 | 内部情绪波动 | AgentState PAD（valence/arousal 变化 24h 内阈值） | ✅ 有源 |
| D04 记忆闪回 | 共同记忆联想 | `_dig_old_memory`（置信度采样，联想 C 类同款） | ✅ 有源 |
| D05 碎碎念 | 日常琐事吐槽 | 日程 activity_pool 当前活动 + autonomy 偏差记录 | ✅ 有源 |
| D06 未来预告 | 期待/紧张 | 明日计划（generate_next_day 已含 notable 活动） | ✅ 有源 |
| D07 关系表达 | 指向用户的模糊告白 | 亲密度阈值 + 里程碑（total_messages/纪念日） | ✅ 有源 |

### 4.2 生成流程（复刻主动引擎三段式，换出口）

```
tick_moments(now)                        # 与 tick_proactive 同频轮询
  └ 全表收集「今日可发素材」（各源求值：D01 有未发摘要？PAD 波动？
    明日计划有 notable？…）→ 进待织池（moment 版，独立于消息池）
  └ 发送闸：role_settings.moments.enabled + 频率（low/med/high→每日上限
    + 距上次 ≥6h）+ 类型轮换（同型连续 ≤2）
  └ LLM 单发织文（≤100 字、口语碎片、注入最近 5 条动态防重复、
    禁直接呼唤用户「你在吗」式喊话；mention_user 设置=indirect/no 生效）
  └ 入库 moments（不推通知、不发私聊——出现在信息流里就是它的存在方式）
```

质量线（继承项目纪律）：织文失败降级=素材原文分条入库（零丢动态）；
每次生成带 `source_ref`，动态详情页可溯源；可见回复通道零协议泄漏不变。

### 4.3 互动

- 用户点赞：`toggle like`（幂等），likes+1；**默认不私聊回应**
  （dm_after_like 开关留给 Phase 3+）。
- 用户评论：入库即触发一次同步 LLM 短回复（≤30 字，`comment_response_style:
  character/minimal/none`），回复作为 actor=role_id 的 interaction 行——
  评论区变成两人的小对话，不占聊天未读。
- 用户发布动态：MVP 不做（裁决 Q3），信息流只读角色生活。
- 角色之间互相点赞评论：**不做**（多智能体互演，破坏「他们各自眼里只有你」）。

## 5. 角色独立设置（role_settings JSON，UI 全走 Galaxy 开关组）

按「设计稿全量、分期实装」列（勾=Phase）：

```
主动行为                          [P2]
├ proactive.enabled 总开关        [P2]（gate 消费，关=该角色永不主动发消息）
├ frequency low/med/high          [P2]（映射每日上限 1/2/3）
└ 类型多选（问候/牵挂/闪回/饭点…） [P3]（RITUAL_SOURCES 名单 × 设置过滤）
好友动态                          [P2]
├ moments.enabled / frequency     [P2]
├ allowed_types D01-D07 多选      [P3]
└ mention_user yes/indirect/no    [P2]（默认 indirect）
互动                              [P3]
├ comment_response_style          [P3]（P2 先固定 character）
└ dm_after_like                   [P3]
记忆与关系                        [P3]
├ 重置此角色记忆 / 重置亲密        [P3]（二次确认；导出复用现有 export）
└ 从其他角色导入                  [不做]（Q1 裁决记忆库共享 → 迁移语义已无必要）
称呼与表达                        [P3]
├ fixed_nickname 锁定当前称呼     [P3]
├ sensitive_topics_extra          [P3]
└ expressiveness cold/natural/warm[P3]
```

默认值原则同设计稿：全开+medium+indirect，用户不碰也有好体验。
免打扰时段=全局项（设置页通用组）；凌晨批量补（§5.1）遵守同一免打扰窗。

## 6. UI（Galaxy 黑白规范内生长）

```
底栏（三 tab，icons：chat/feed/settings — material icons 现有依赖内选）
├ 聊天 = 角色列表页
│   条目：首字母几何圆头像（roster 已有此风）+ 角色名 + 最后一条预览
│   + 时间 + 未读红点（role_reads 差值计数）；长按=删除会话/导出
│   ↓ 点入 = 现有聊天页复用（立绘背景/气泡/输入栏全保留，顶栏=角色名+返回）
├ 好友动态 = 信息流页
│   分组按日；条目=头像+角色名+时间+正文（≤100 字无折截断）+
│   底部 [♡ n][评论] 两枚描边胶囊；点开=评论列表 + 输入框
│   角色发布节奏可视化：空白日自然留白，不做"暂无动态"占位轰炸
└ 设置 = 现 SettingsScreen 平移
    新增组：角色管理（每角色一行 → 角色设置页[GalaxyNavRow 复用] +
    导入角色包入口现有已在本页）
顶栏应用名 = Veranima（裁决 Q6）
```

设置页内嵌 NavHost 再加路由：`moments_feed`（其实归底栏）、`role_settings/{role_id}`。

## 7. 裁决记录（2026-09-01 用户逐项拍板）

| # | 问题 | 裁决 |
|---|---|---|
| Q1 | 记忆隔离深度 | **折中**：会话历史+关系+作息+称呼+承诺按角色隔离；记忆库共享（凛能召回你和由岐说过的用户事实） |
| Q2 | 承诺归属 | **谁听见谁持有**：承诺按记录角色隔离，凛的提醒只从凛处发 |
| Q3 | 用户发动态 | **MVP 不做**：动态=纯粹的角色生活窗口 |
| Q4 | 后台调度 | **混合**：活跃角色实时跑 tick/动态；其余角色每日凌晨一次批量补生成（打开时若跨过凌晨点则现场追补） |
| Q5 | D02 天气源 | **虚拟天气**：按 角色城市+日期 做确定性伪随机（哈希种子，同城同天全源一致），角色自己「看窗外」，零 API——与「不声称参与现实活动」红线天然兼容 |
| Q6 | 应用名 | **Veranima** 直接当顶栏名 |
| Q7 | 旧时间线 | **全部归凛**的会话窗（存量 558+ 条=凛时代） |

### 4.4 虚拟天气（Q5 定稿）
角色卡 city（许眠=工作城市）+ 本地日期 → 哈希 → 天气档 {晴/多云/雨/降温/大风}
+ 季节权重（12 月降温和大风概率↑）。同一天该角色所有消费者（D02 动态、聊天
prompt 的「你看窗外」、联想素材）读同一结果——一致性靠纯函数，不存储。

### 5.1 凌晨批量补（Q4 定稿）
bridge 每日首次 tick 跨过 04:00 或角色被首次打开时距上次补生成 >20h →
对该角色跑一次「快进补生成」：只看昨日日程摘要/情绪落点，最多补 1 条动态；
主动消息不补（过期问候不如不说——合并窗口哲学同源）。

## 8. 分期

| Phase | 内容 | 验收 |
|---|---|---|
| P1 骨架 | messages.role_id 迁移；bridge Agent 注册表；三 tab UI；角色列表+分会话聊天；未读角标 | 两角色各聊各的历史互不可见；roster/作息随角色走（真机） |
| P2 动态+设置 | moments 引擎（D01/D03/D05/D06 四源起步）；信息流+详情+点赞；role_settings 最小集上线 | 真 DeepSeek 生成带溯源动态连发 20 条无重复主题；关开关即停发 |
| P3 互动+全量设置 | 评论回复、D04/D07、类型过滤/称呼锁定/记忆重置、消息类型多选 | 评论→角色 30 字内回复链闭环；设置逐项行为断言 |
| P4 润色 | 输入中状态、角色回访赞评（若 Q3 开）、记忆导入迁移（若 Q1 乙/丙） | 随裁决 |

## 9. 红线（不变项）

- 动态=独白不是喊话；协议字段/思考过程零泄漏纪律不变。
- 主动消息合并窗口/待织池逻辑不动，动态走独立管线独立池。
- PC 端（QQ/桌宠）零改动：role_id 列默认值迁移、bridge 协议向后兼容。
- 分发原则：不加新依赖（icons 用现有 material-icons 集，无 Retrofit/无 Flutter）。
