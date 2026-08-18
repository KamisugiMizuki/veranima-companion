# R0 专项：角色内核与统一 Reply

> R0 是所有实现的入口。没有 R0，后续记忆、TTS、视觉和主动性都会变成给一个空壳模型接功能。
> 现有复用：`core/character.py`, `core/prompts.py`, `core/segments.py`, `core/agent.py`, `llm/client.py`。

## 1. 角色卡真值

`CharacterCard.from_file()` 是唯一加载入口，角色字段按以下优先级读取：

1. Character Card V3 `data.*`。
2. `data.extensions.veranima.*`。
3. 兼容旧格式顶层 `veranima.*`。

必需字段：`name, personality, scenario, first_mes, mes_example`。建议字段：

```json
{
  "extensions": {
    "veranima": {
      "communication_style": "可观察的说话习惯",
      "virtual_background": "虚拟日常背景",
      "quirks": ["稳定癖好"],
      "taboos": ["不主动触碰的话题"],
      "values": ["价值排序"],
      "capabilities": {"strong": [], "weak": [], "unknown": []},
      "tones": ["中性", "调侃", "疲惫"],
      "avatar": {"expressions": {"闲置": "portraits/idle.png"}},
      "voice": {"provider": "gpt-sovits", "ref": "voice/refs/ref.wav"},
      "bilingual": {"enabled": false, "display": "zh", "tts": "ja"}
    }
  }
}
```

字段不存在时使用空值/默认，不把另一角色的默认文本写进系统层。角色级意象只能通过角色卡注入。

## 2. Prompt 分层

`build_system_prompt()` 输出顺序固定：

```text
A. 系统硬边界（IDENTITY_BLOCK，不含角色专属词）
B. 角色卡（name/personality/scenario/communication_style/...）
C. 当前状态（由 AgentState 生成）
D. 当前通道规则（im/tts）
E. 相关记忆（预算内）
F. 当前场景/注意力（可选短期 context）
G. 本轮任务要求
```

低成本模型提示规则：一条规则一句话、避免互相冲突；不要在系统 prompt 里重复角色样本十遍。`mes_example` 最多 2 个短回合，示范行为而非固定句子。

## 3. 人格稳定性检查

新增纯函数 `validate_character_prompt(card, prompt) -> list[str]`，只检查：

- name 非空。
- tones/portrait 在白名单。
- prompt 包含角色名和 personality 关键片段。
- 系统硬约束不含其他角色名/生活锚点。
- 角色卡 JSON 可解析。

换卡验收使用 Zima/Yuki/反差测试卡，确认旧角色关键词不泄漏。

## 4. Reply 解析

建议新增 `core/reply.py`，定义 `Reply`/`ReplySegment`；`core/segments.py` 作为兼容 facade。

### IM

LLM 返回纯文本 → 一个 `ReplySegment(text=raw)`。

### TTS

LLM 返回 JSON segments；解析步骤必须确定性：

1. trim。
2. 去 ```json fence。
3. `json.loads`。
4. 读取 `segments` 数组，最多 6 段。
5. 每段 text/ja/zh 截断 1200 字总计。
6. tone/portrait 只接受角色卡白名单，否则回退中性/闲置。
7. 双语缺 ja：segment 标记 `suppress_tts=True`，显示翻译。
8. 失败：从原文提取可读文本；完全失败返回 `degraded`。

不使用“匹配到第一个正则对象就算成功”作为唯一解析策略；低成本模型常输出多段/残缺 JSON，必须有测试样本。

## 5. Agent.handle 分阶段

将现有长函数保持行为兼容，逐步抽为以下纯/窄函数：

```python
prepare_turn(user_text, images, channel) -> TurnContext
build_turn_prompt(context) -> list[dict]
call_llm(messages, channel) -> str
parse_reply(raw, channel, card) -> Reply
persist_turn(context, reply) -> None
```

第一阶段不移动所有代码；先在 `Agent.handle()` 内按注释划分并给每阶段测试。任何阶段失败都返回 `Reply(degraded=...)`，不吞掉错误。

## 6. 配置

```yaml
llm:
  max_tokens: 4096
  short_task_max_tokens: 1024
  timeout: 120
output:
  max_segments: 6
  max_reply_chars: 1200
  parse_retry: 1
```

思考模型不得在短任务传 120/200；所有短任务读取 `short_task_max_tokens`。配置由 `config.py` 加载并做范围校验。

## 7. 测试与完成定义

新增 `tests/test_reply.py`、`tests/test_character_contract.py`。

必须通过：

- V3 卡/旧格式卡/缺字段卡加载。
- 角色 prompt 不泄漏。
- 纯文本、JSON、fence、残缺、多段、非法标签、双语缺 ja。
- LLM 空回复、超时、400、TTS 不可用的降级。
- TurnResult 兼容旧 adapter。
- 10 轮固定测试对话能识别至少 3 个角色习惯（用 FakeLLM，避免真实模型随机性）。
