# veranima 设计总纲 v2.2

> 状态：人物中心扩容设计基线（2026-08-19）；R0-R5 与人格循环 P-0~P-9 第一版运行时闭环已按专项契约落地；SelfModel 编辑器、LoRA 等暂缓项仍未实现。
> 产品原则：**不追求更像 AI，追求更像「某个人」**。
> 本文规定模块边界、功能栈、实现顺序和完成定义；详细契约见 R0-R5、`PERSONA_LOOP_SPEC.md`、`MEMORY_SPEC.md`、`GUI_SPEC.md` 与 `VISION_SPEC.md`。

## 1. 产品与非目标

veranima 是一个具有稳定人格核心、可追溯自传、关系历史和当下内在状态，能通过 QQ 与桌宠陪伴用户的虚拟人物。QQ 与桌宠是同一 Agent 的两种媒介，不是两个角色。

人格不是 system prompt 的静态表演，也不是对用户文风和观点的复制。人物中心模型固定为：

```text
本轮人格表现
= Character Core（角色恒定性）
+ Self Model / Relationship Model（自传与关系连续性）
+ Inner State（当下状态）
+ Scene / Channel / Topic（情境化表达）
```

人格形成与关系性循环的唯一契约见 `docs/PERSONA_LOOP_SPEC.md`：观察用户框架与共同事件 → 候选校验 → 整合为用户模型/共同意义/角色自传 → 在相关新情境中有边界地回用 → 根据反馈修正版本和关系解释。理解用户不等于同意用户；角色必须保留独立判断和分歧。

拟真表达由五维控制面协同：认知过程、情绪耦合、表层人格演化、关系动态和有因差异。认知过程只输出短结构 `ResponsePlan`，不生成或暴露私密思维链；情绪使用连续 PAD（愉悦度/唤醒度/支配度）并具有 cause、惯性和衰减；差异性必须来自状态、关系、注意力或可追溯联想，禁止无因随机反常。

成功不是“回答更聪明”，而是用户在一周后能说出：她是谁、她记得什么、她今天为何不同、她为何现在来找我或没有打扰。

非目标：通用 AI 助手、自动化平台、视觉监控器、随机错误模拟器、Live2D 展示 demo、插件市场。

## 2. 技术栈冻结

| 用途 | 决定 | 当前代码/实现 |
|---|---|---|
| 核心运行时 | Python 3.11 + `src/veranima` | 复用现有包结构 |
| LLM | OpenAI 兼容 HTTP API，`httpx` | `llm/client.py`；thinking 模型输出预算由配置提供 |
| 记忆/状态 | SQLite + FTS5 + memory_embedding blob（暴力余弦）；完整契约见 `docs/MEMORY_SPEC.md` | `memory/schema.py`, `memory/store.py`, `core/learning.py` |
| 人格循环 | Character Core + User/Self/Relationship Model + Persona Brief + PAD/ResponsePlan；完整契约见 `docs/PERSONA_LOOP_SPEC.md` | 复用 `core/character.py`, `core/agent.py`, `core/state.py`, `memory/`；按 P-0~P-9 增量实现 |
| Embedding | 本地 `sentence-transformers` / bge-m3 | `memory/embedding.py`；远程 API 不作为默认 embedding |
| 角色卡 | Character Card V3 兼容 JSON + `extensions.veranima` | `core/character.py`, `core/roles.py` |
| QQ | NapCatQQ OneBot v11 反向 WS | `adapters/qq.py` |
| 桌宠壳 | Electron 34，Python 核心通过 localhost WebSocket | `pet/main.js`, `pet/preload.js`, `pet/renderer.js` |
| TTS | GPT-SoVITS v4 本地服务，9880，当前整段合成 | `tts/client.py`, `pet_server.py` |
| 视觉 | Win32 ctypes + Pillow/numpy；远程多模态仅按事件调用 | `core/presence.py`, `core/attention/` |
| 任务协作 | dsh CLI 子进程，独立配置/会话 | `core/workorder.py`, `tools/dsh_bridge.py` |
| 测试 | pytest；低成本模型每片改动必须先跑定向测试再全量 | `tests/` |

不新增框架，除非现有标准库/依赖无法覆盖并且有实测瓶颈。不要引入 LangChain、Redis、PostgreSQL、消息队列、RLHF、训练管线。

## 3. 现有代码真值

### 已可复用

- `Agent.handle()`：对话主入口，但需拆出可测试的阶段函数。
- `TurnResult`：短期兼容；R2 逐步扩展为统一 `Reply`。
- `CharacterCard.to_system_prompt()`：角色卡读取和系统硬约束。
- `MemoryStore.store/recall/decay/curate/erase/update_latest`：SQLite 记忆原语。
- `AgentState`：已有 energy/mood/attachment/持久化；R1 扩展 social_appetite/attention/cause。
- `render_im()`：IM 规则后处理；保留为纯函数。
- `SceneLock/ChannelActivityTracker/Arbitrator`：R4 主动性前置基础。
- `AttentionScheduler`：视觉感知骨架；必须改为不直接观察后发言。
- Electron 窗口、WS、TTS、角色包：保留，不重写技术栈。

### 必须迁移而非复制

- `TurnResult` → `Reply`：保留兼容属性，避免一次性改所有 adapter。
- `memories` 五层旧命名 → R1 的 identity/user_fact/shared_episode/commitment/session：使用映射，不立刻重建数据库。
- `proactive_message_prob`：废弃每轮随机主动；改由 `ProactiveDecision` 统一裁决。
- `AttentionEvent`：保留字段兼容，新增 confidence/source/event_id/expiry。
- `config.example.yaml`：合并重复 `chat` 段；以当前真实 GPT-SoVITS 取代 Qwen 注释。

## 4. 统一协议

### 4.1 TurnContext

```python
@dataclass(frozen=True)
class TurnContext:
    channel: Literal["im", "tts"]
    user_text: str
    images: tuple[str, ...]
    scene: str
    current_time: str
    state: dict
    recalled_memories: tuple[dict, ...]
    active_focus: dict | None
```

### 4.2 Reply

```python
@dataclass
class Reply:
    segments: list[ReplySegment]
    stance: str = ""
    follow_up: Literal["none", "answer", "invite", "close"] = "none"
    memory_candidates: list[dict] = field(default_factory=list)
    degraded: str = ""

@dataclass
class ReplySegment:
    text: str
    translation: str = ""
    tone: str = "中性"
    portrait: str = ""
```

规则：LLM 解析失败先提取纯文本；仍为空才返回角色化失败文案；绝不展示 JSON、markdown fence、thinking 残片、内部错误。

### 4.3 ProactiveDecision

```python
@dataclass(frozen=True)
class ProactiveDecision:
    allow: bool
    reason: str
    source: str
    intent: str = ""
    cooldown_until: float = 0.0
```

`allow=false` 是正常返回，不是异常。视觉模块只能提供 source/context，不能直接发送。

## 5. 规则优先级

低成本模型只负责自然语言和候选结构化输出；以下必须由程序确定：

1. 输入为空、核心/TTS 可用性、取消和超时。
2. 角色卡字段读取、标签白名单、配置校验。
3. 记忆去重、版本链、删除和敏感数据处理。
4. 状态迁移、场景锁、通道互斥、主动冷却和每日上限。
5. 回复解析、语言校验、失败降级。
6. 视觉隐私分类、暂停开关、观察过期。

## 6. 低成本模型实现规约

每次只实现一个垂直切片：

1. 先读本 SPEC 和指定文件。
2. 先写/改 1-3 个定向测试。
3. 只改列出的目标文件，不顺手重构。
4. 输出输入/输出日志和失败路径。
5. 定向测试通过后跑全量 pytest。
6. 对照本 SPEC 验收，提交一个小 commit。

禁止：看到“接口”就新建抽象层；看到“未来”就加配置；看到 LLM 不稳定就把规则全塞进 prompt；看到测试 mock 不匹配就绕过测试。

## 7. 里程碑与依赖

```text
R0 角色内核与 Reply 协议
 ↓
P-0 角色核心扩展（人格循环见 docs/PERSONA_LOOP_SPEC.md）
 ↓
R1 共同经历/状态连续性（记忆见 docs/MEMORY_SPEC.md；人格循环 P-1~P-4）
 ↓
R2 IM/TTS 表达与失败降级
 ↓
R3 Electron 桌宠/独立聊天闭环（界面契约见 docs/GUI_SPEC.md）
 ↓
R4 Presence/Attention/主动性
 ↓
R5 dsh 可选任务协作
```

R0-R3 未完成前不扩展 R4/R5。每阶段完成必须通过体验验收，而不是只看测试数量。

GUI 实现以 `docs/GUI_SPEC.md` 为唯一界面契约：主窗突出角色存在，聊天/设置/日志采用安静的 Operate 界面。只借鉴 sakura/airi 已验证的组件职责，不迁移其 UI 框架和插件规模。

## 8. 总体验收

- 盲测 10 轮能说出角色至少 3 个稳定特征。
- 一周后角色能自然回用至少 2 个用户私人概念，并能保留具体分歧；不能只复述用户原句。
- 共同事件不仅保留事实，还能在证据充分时形成双方解释、共同意义和后续行为变化。
- 一次关系冲突具有触发、解释、澄清、修复/守界和关闭过程，不立刻清零也不永久惩罚。
- 情绪通过句式、节奏、TTS 和主动性同时可感知，变化有原因、有惯性、会衰减。
- 同一话题允许有因差异和自然联想，但角色立场不随机漂移，跑题可追溯且用户不接时回到主线。
- 跨重启/跨 QQ/桌宠能接续共同经历。
- 换反差角色不泄漏旧角色词汇和锚点。
- 状态变化有 cause 且可恢复。
- 同一 Reply 在 IM/TTS 事实和立场一致。
- LLM/TTS/核心失败时文字不消失，有恢复动作。
- 主动消息能解释为什么现在发，忽略后停止。
- 视觉观察降低盲目提问，不制造监控感。

## 9. 近期设计扩展

以下是围绕角色包、表达适配和共同经历的专项契约；核心状态以各文档头部为准：

- [`COMPANION_CONTINUITY_DESIGN.md`](COMPANION_CONTINUITY_DESIGN.md)：伴侣连续性增强方案；统一关系上下文、未完事项跟进、共同意义回用、主动关怀和虚拟生活回顾。当前为方案稿，尚未进入实现。
- [`CHARPKG_SPEC.md`](CHARPKG_SPEC.md)：在现有 `.char` ZIP 归档基础上升级 `.charpkg`，增加 manifest/schema、哈希清单、quarantine、原子安装、冲突和回滚；不允许执行代码、打包密钥或默认导出用户记忆。
- [`STYLE_LEARNING_SPEC.md`](STYLE_LEARNING_SPEC.md)：未标注语料的自动处理、抽样复核、`StyleProfile → StyleBrief → ResponsePlan` 核心已实现；LoRA 仍暂缓，风格不得覆盖角色核心。
- [`SHARED_CREATION_SPEC.md`](SHARED_CREATION_SPEC.md)：复用 `shared_episode/shared_meaning/commitment/relationship_event` 和现有关系模型，增加 Project/Arc/Scene/Decision/Artifact/OpenThread 的协作工作流；关系变化必须有证据和用户确认。
- [`VIRTUAL_SCHEDULE_SPEC.md`](VIRTUAL_SCHEDULE_SPEC.md)：角色目录日程模板、昼夜节律、睡眠、偏移回正、活动上下文和有来源的生活主动性。
- [`VIRTUAL_SPACE_SPEC.md`](VIRTUAL_SPACE_SPEC.md)：以有限生活范围、稳定场所池、路线和 CurrentScene 取代“角色永远固定在一个 scenario”；当前为设计稿，尚未实现。

Yuki 运行时卡位于 [`characters/yuki/character.json`](../characters/yuki/character.json)，说明文件 [`characters/yuki/card.md`](../characters/yuki/card.md) 区分公开人设参考、项目原创桌宠延展和明确排除的原作文本/剧透。

## 10. 主动交互增强（2026-08 design_append 裁决，已落码）

- **无人应答追问**：主动消息含直接问句（`extract_direct_question`）→ 记 `proactive_feedback` pending 期待（窗口 `tension.unanswered_reply_window_hours`）；到期原子结算 expired + TV(+10, dedupe)；对 expired 未回期待发一句 ≤15 字轻追问（`followup_status='asked'` 原子占坑，每期待至多一次，追问后再石沉大海即终）；用户任何回话在 `handle` 统一闭合（responded=1 幂等）。消费端：安卓 bridge tick；QQ 侧过期结算由其 `_expire_qq_expectations` 承担，记录端 `_record_qq_expectation` 独立不双写。
- **被看穿**：prompt 级——夜间 digest 在概括末尾可点一句行为规律（看不出就不编）；heartbeat 破冰可用「我注意到你总是…」语气。不做独立检测子系统。
- **翻旧账**：`_dig_old_memory` 原为孤儿函数（能力在、消费端断），已接进 heartbeat 素材；独立触发条件（超一周/情绪强烈）暂不做。
- **默契沉默**：不实现——沉默是现有架构默认态（候选+闸门+掷骰全过才说话），「我还以为你睡着了」是 LLM 拿时间上下文的自然产出。
- **主动输**：暂缓——「观点分歧 vs 原则冲突」的语义判断错了就是角色崩；tension repair band 只管关系冲突降温。真要口味修正塞一句 prompt 即可。
