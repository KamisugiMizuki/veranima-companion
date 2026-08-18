# veranima 设计总纲 v2.1

> 状态：低成本模型实现基线（2026-08-19）。
> 产品原则：**不追求更像 AI，追求更像「某个人」**。
> 本文规定模块边界、功能栈、实现顺序和完成定义；详细契约见 R1-R5 与 `VISION_SPEC.md`。

## 1. 产品与非目标

veranima 是一个固定人格、拥有共同经历、会随当下状态变化、能通过 QQ 与桌宠陪伴用户的虚拟人物。QQ 与桌宠是同一 Agent 的两种媒介，不是两个角色。

成功不是“回答更聪明”，而是用户在一周后能说出：她是谁、她记得什么、她今天为何不同、她为何现在来找我或没有打扰。

非目标：通用 AI 助手、自动化平台、视觉监控器、随机错误模拟器、Live2D 展示 demo、插件市场。

## 2. 技术栈冻结

| 用途 | 决定 | 当前代码/实现 |
|---|---|---|
| 核心运行时 | Python 3.11 + `src/veranima` | 复用现有包结构 |
| LLM | OpenAI 兼容 HTTP API，`httpx` | `llm/client.py`；thinking 模型输出预算由配置提供 |
| 记忆/状态 | SQLite + FTS5 + sqlite-vec | `memory/schema.py`, `memory/store.py` |
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
R1 共同经历/状态连续性
 ↓
R2 IM/TTS 表达与失败降级
 ↓
R3 Electron 桌宠/独立聊天闭环
 ↓
R4 Presence/Attention/主动性
 ↓
R5 dsh 可选任务协作
```

R0-R3 未完成前不扩展 R4/R5。每阶段完成必须通过体验验收，而不是只看测试数量。

## 8. 总体验收

- 盲测 10 轮能说出角色至少 3 个稳定特征。
- 跨重启/跨 QQ/桌宠能接续共同经历。
- 换反差角色不泄漏旧角色词汇和锚点。
- 状态变化有 cause 且可恢复。
- 同一 Reply 在 IM/TTS 事实和立场一致。
- LLM/TTS/核心失败时文字不消失，有恢复动作。
- 主动消息能解释为什么现在发，忽略后停止。
- 视觉观察降低盲目提问，不制造监控感。
