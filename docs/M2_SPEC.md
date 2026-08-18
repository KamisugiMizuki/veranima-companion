# R2 专项：同一个人的表达

> 目标：同一角色在 IM 与 TTS 中表达不同，但事实、立场、关系和情绪一致。
> 现有复用：`core/agent.py`, `core/segments.py`, `core/render.py`, `tts/client.py`, `pet_server.py`。
> 最小新增：统一 Reply DTO、回复解析器、通道 renderer 契约和失败状态。

## 1. Reply 契约

建议放置：`src/veranima/core/reply.py`。

```python
@dataclass
class ReplySegment:
    text: str
    translation: str = ""
    tone: str = "中性"
    portrait: str = ""

@dataclass
class Reply:
    segments: list[ReplySegment]
    stance: str = ""
    follow_up: str = "none"
    memory_candidates: list[dict] = field(default_factory=list)
    degraded: str = ""
```

兼容策略：`TurnResult.reply/portrait/tone/ja_text` 暂时保留为 property；QQ/TTS adapter 逐步消费 `Reply`。

## 2. LLM 输出与解析

### IM

提示模型输出纯文本。解析器直接构造一个 `ReplySegment(text=raw)`。禁止为 IM 强制 JSON。

### TTS

提示模型输出：

```json
{"segments":[{"ja":"日语","zh":"中文","tone":"中性","portrait":"闲置"}]}
```

`core/segments.py` 负责：去 markdown fence、解析、语言方向检查、tone/portrait 白名单、缺 ja 防御。失败顺序：

1. 从 JSON/fence 提取可读文本。
2. 有中文显示文本但无日语：静默文字降级，不送日语 TTS。
3. 完全无文本：返回角色化“我这边没组织好，再说一次”并记录 reason。

不得将 JSON、thinking 残片或异常堆栈送至 UI/TTS。

## 3. Renderer 接口

```python
render_im(reply: Reply, state: AgentState) -> str
render_tts(reply: Reply, state: AgentState) -> list[SpeechSegment]
```

IM 只做可逆清理：感叹号上限、连续空行、角色卡 emoji 频率、亲密度阈值。不得随机改写事实。

TTS 生成 `SpeechSegment(text, tone, portrait, display_text)`。`display_text` 与语音必须同一 segment，禁止整段中文/单句日文错配。

## 4. 角色表达配置

角色卡 `extensions.veranima`：

```json
{
  "communication_style": "可观察的说话习惯",
  "sentence_style": {"length":"short|mixed|long", "opening":["嘛","啧"]},
  "emoji_frequency": "never|low|medium|high",
  "tones": ["中性","调侃","疲惫"],
  "avatar": {"expressions": {"闲置":"portraits/idle.png"}},
  "bilingual": {"enabled": true, "display":"zh", "tts":"ja"}
}
```

系统级 prompt 不写具体角色意象；角色卡才写角色习惯。对话样本 8 组作为校准数据，不作为模板。

## 5. 状态与取消

统一状态：`idle → generating → speaking → idle`；失败为 `failed`，取消为 `cancelled`，都必须清理队列。

用户新输入时：

1. adapter 发 `cancel_current_turn`。
2. 壳暂停当前音频并清空队列。
3. 核心丢弃旧 turn 的迟到结果（`turn_id` 不匹配）。
4. 新输入正常生成。

低成本实现使用递增 `turn_id`，不引入任务编排库。

## 6. 配置

```yaml
output:
  max_segments: 6
  max_text_chars: 1200
  parse_retry: 1
  keep_text_on_tts_failure: true
tts:
  provider: gpt-sovits
  timeout: 60
  segment_mode: whole
```

thinking 模型的普通回复使用 `llm.max_tokens`；短任务不得自行传 120/200 等小预算，统一走 `short_task_max_tokens >= 1024`。

## 7. 测试

覆盖：纯文本、fence JSON、残缺 JSON、双语缺 ja、非法 portrait、TTS 失败保留文字、取消后迟到音频丢弃、IM/TTS 事实一致。
