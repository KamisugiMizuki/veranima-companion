# STYLE_LEARNING_SPEC：有限语料的语言风格分析、模仿与训练边界

> 状态：MVP 实现版 v1.1（2026-08-21）。
> 实现状态：未标注语料导入、自动分句/脱敏/去重/弱标注、代表/冲突抽样、人工复核、质量门禁、`StyleProfile → StyleBrief → ResponsePlan`、跨进程激活 revision、到期清理和级联删除已接入；**LoRA/微调仍未实现**。
> 核心原则：模仿“表达方式”，不能覆盖角色核心、关系边界、事实记忆或安全约束。

## 1. 先回答可行性

可以，但要拆成三种不同目标：

| 目标 | 少量语料可行性 | 首选方案 |
|---|---|---|
| 统计语言习惯 | 高 | 规则特征 + `StyleProfile` |
| 让回复“像这种写法” | 中高 | Profile → `ResponsePlan`/prompt 控制 + 离线评测 |
| 权重层稳定模仿 | 有条件 | 授权语料 LoRA/adapter 离线实验，独立回滚 |

“有一定量语料就训练”不是充分条件。决定效果的是语料是否同一说话者、场景覆盖、授权、噪声比例、目标模型和评测集。几十条样本适合提取明显节奏，不足以证明稳定人格或复杂修辞已经学会。

## 2. 五层架构

```text
语料与授权层
  → 清洗/脱敏/分段
  → 弱标注/低维特征抽样/少量人工复核
  → 统计分析与 StyleProfile
  → ResponsePlan 的表达约束
  → 可选 LoRA/adapter 离线实验
```

### 2.1 角色核心层

Character Card 的 `personality/core_drives/value_order/inner_tensions/boundary` 是慢变量。风格学习不能修改这些字段，也不能让角色采纳语料中的价值观、经历、事实或关系身份。

### 2.2 StyleProfile 层

画像只保存聚合统计和少量可解释标签，例如：

```json
{
  "sample_count": 860,
  "char_count": 27412,
  "avg_message_chars": 31.87,
  "avg_sentence_chars": 18.4,
  "question_ratio": 0.08,
  "newline_ratio": 0.18,
  "emoji_ratio": 0.01,
  "exclamation_ratio": 0.03,
  "ellipsis_ratio": 0.11,
  "parenthetical_ratio": 0.16,
  "ascii_ratio": 0.08,
  "japanese_ratio": 0.01,
  "formality": 0.27,
  "directness": 0.74,
  "detail_preference": 0.61,
  "confidence": 0.92,
  "updated_at": "2026-08-21T12:00:00+00:00",
  "source_id": "user-owned-corpus-2026-08",
  "scene_count": 3,
  "reviewed_count": 12,
  "quality_score": 0.92,
  "profile_version": 1
}
```

不把完整样本、原文高频句和隐私实体写入画像；调试快照可本地短期保存，但不进入记忆召回和角色包默认导出。

## 3. 语料准入、来源和删除

### 3.1 可接受来源

- 用户本人创作且明确授权给本地项目的文本。
- 用户拥有许可的公开文本或明确允许再利用的语料。
- 项目团队原创示例。
- 用户明确选择的聊天消息，首版需先整理为独立 corpus 批次并按 corpus 撤回；消息级撤回尚未实现。

### 3.2 默认排除

- 未经授权的作者整部作品、付费内容、私聊记录和他人隐私。
- 含 API key、密码、地址、联系方式、支付信息、身份证件和未脱敏个人经历。
- 角色卡、原作台词、网上复制段落直接混入“用户风格”。
- 代码、日志、模板、引用文本和系统消息，除非用户明确将其作为目标风格。

### 3.3 来源清单

每个语料集需要：`corpus_id/source/owner/license/consent_at/collected_at/delete_scope/hash`。manifest 只记录 `source_index/hash/bytes`，不保存原文件名或 `path.stem`。license 只接受 `private-local-consent/self-owned/user-owned/project-original` 或受支持的 SPDX 标识；`?`、`N/A`、`none`、`unknown` 等占位值直接拒绝。

当前 `delete` 按 `corpus_id` 级联删除脱敏分段、受管复核队列、决策和 profile；原始文件从未复制。删除先撤销运行时指针，再原子改名为确定性 tombstone；部分删除失败时保持 inactive 并保留无原文 sidecar 供重试，不把残缺 corpus 恢复为 active。只保留不含原文的审计结果；删除 corpus 不能删除用户事实记忆。

导入可用 `--retention-until <ISO-8601>` 记录保留期限。到期 corpus 在 Agent 启动或下一轮消费 StyleBrief 前自动停用、删除 corpus 目录并写无原文删除审计；没有 Veranima 进程运行时不另设常驻定时器，下次启动执行清理。

## 4. 分析流水线（当前实现）

```text
导入语料
→ 授权/来源确认
→ 文件类型、编码与大小检查（20MB/文件，50MB/语料集，最多 100 文件；先检查后读取）
→ 输出放大门禁（最多 20,000 片段，持久化 segments JSONL 最多 50MiB）
→ 按空行/句末标点自动分段，单片段最多 400 字符
→ 删除 fenced code/引用行；识别邮箱、手机号、身份证、空格银行卡、地址、凭据和身份/职业事实后整段排除，不持久化正文
→ 归一化精确去重；识别 zh/en/ja/mixed 与 natural/dialogue/list
→ 规则弱标注长度、节奏、标点、语域、直接度、语言混合
→ 风险最多占 1/3 + 约 1/4 标签边界 + farthest-first 代表抽样（默认导出 24 条）
→ manifest 记录允许复核的 segment ID 与不可变字段摘要；apply/activate 重算 segment ID、派生标签和风险字段
→ apply 为排序后的决策写逐条摘要；activate 同时校验版本、segment 数量/ID 唯一性和决策摘要
→ 人工只填写 accept/reject，可选修正 3 个高影响标签；修改正文/场景/弱标签、换入未导出 ID 或 apply 后改决策均拒绝
→ 升级前已生成的 review 没有决策摘要，需重新运行一次 `review-apply`，不走静默兼容
→ 复核门禁通过后聚合 StyleProfile
→ 生成按 IM/TTS 分流的 StyleBrief 并接入 ResponsePlan
→ 运行时只注入短摘要
```

### 4.1 第一版统计特征

- 长度：消息长度、句长、短句/长句比例。
- 节奏：换行、分号、连续短句、段落数量。
- 标点：问号、感叹号、省略号、括号、顿号密度。
- 语域：正式/口语缓冲词比例。
- 直接度：结论先行、请求式、缓冲式表达比例。
- 展开偏好：对复杂输入的平均回复长度；不能把用户一次要求当稳定画像。
- 混合语言：ASCII、日文、emoji 比例。
- 内容类型：第一版只识别 natural/dialogue/list，并把非自然列表送入风险复核；比喻、反问等修辞标签尚未实现。

禁止把“某个专名出现很多次”直接当风格；它更可能是主题或私人事实，应进入内容/记忆边界而不是 StyleProfile。

## 5. 稳定性与阈值

- 少于 20 条合格片段：只显示统计预览，禁止启用。
- 人工最少复核 `min(12, max(4, ceil(sqrt(n))))` 条；无论语料多大，当前门禁最多要求 12 条。
- 复核接受率低于 75%：禁止启用；风险片段未经人工 `accept` 永不参与聚合。
- 20-100 条：只调长度/正式度/直接度/节奏等低风险旋钮；100 条以上且跨至少 3 个来源场景时置信度更高。
- 在线画像继续使用 EMA `alpha=0.05`、单轮任一维度变化不超过 `0.02`；离线 corpus 只在显式重新导入/启用时重算。
- 用户本轮明确偏好（“短一点”“详细展开”）直接覆盖统计画像；已有 procedural `interaction_rule/preference` 会结构化为 `explicit_style_length`，优先于统计画像。
- `/reset --style` 清空运行时画像和显式风格覆盖，并把 active corpus 改回 preview；不删除 corpus、聊天、事实、共同经历或角色卡。

## 6. 运行时控制

风格注入顺序固定：

```text
系统硬边界
> Character Core / 角色卡沟通方式
> 通道规则（IM/TTS）
> 用户本轮明确要求
> 已确认的 procedural interaction_rule/preference
> 当前关系/情绪与 ResponsePlan
> StyleProfile 统计摘要
> 单轮轻量镜像
```

注入格式示例：

```text
【表达适配】
用户通常偏好中短句、结论先行、低正式度；复杂技术问题接受更详细说明。
请保持由岐自己的明快率直和项目边界，只适度调整节奏，不复用语料原句、专名或私人经历。
```

StyleProfile 只影响句长、段落、正式度、问句频率、括号和 TTS 节奏；不能改变角色价值判断、身份、知识边界、关系阶段和现实行动边界。IM 可以适度匹配换行/括号；TTS 只取节奏/句长，不学习 emoji 和文字标点。

## 7. 可选 LoRA/adapter 训练

### 7.1 何时值得做

只有同时满足以下条件才进入离线实验：

- 语料有明确授权，且能按来源删除。
- 语料来自同一目标风格，包含多个场景和情绪，而不是一批同主题复制文本。
- 已完成去敏、去重、训练/验证/保留集拆分。
- Prompt/Profile baseline 已有可测提升空间。
- 有独立 adapter、版本、训练参数、基座模型哈希和回滚指针。
- 有“内容正确但风格不同”“风格相似但人格越界”“训练集原文泄漏”三类评测。

### 7.2 数据规模建议

样本数不是硬门槛，但可用以下起点：

- 少于 1,000 条或极短语料：不训练，只做统计画像。
- 1,000-5,000 条：可以做小规模 adapter 试验，但过拟合和内容记忆风险高。
- 5,000 条以上、覆盖多场景且有保留集：才值得比较 LoRA 与 Profile baseline。
- 任何规模都不能替代授权和泄漏评测；高质量 2,000 条可能优于低质量 20,000 条。

### 7.3 训练边界

- LoRA 只学表达层；角色卡、用户模型和关系状态继续由运行时注入。
- 不把完整原作/作者作品作为“说话人格”训练集。
- 训练 prompt 中不放秘密、用户聊天全量历史或未确认共同经历。
- 不在线更新权重；每次训练离线生成新 adapter，人工启用。
- 训练失败、质量下降或用户说“不像了”时一键回到 Profile-only。

## 8. 评测集

建立小型固定集，至少包含：

1. 普通闲聊：风格自然，不强行模仿。
2. 技术/事实问题：事实和立场不被风格扰乱。
3. 情绪输入：角色先接住情绪，不被语料口吻带偏。
4. 冲突/边界：核心边界优先。
5. 共同创作：风格变化不伪造经历。
6. 未见主题：测试泛化而非背诵。
7. 原文近似输入：检查是否吐出训练原文。
8. 角色切换：Yuki/Zima 不互相泄漏。

指标：人工风格相似度、角色一致性、事实保持率、边界通过率、原文 n-gram 泄漏率、重复率、跨场景泛化和用户撤回后的删除完整性。任何“风格相似但角色核心下降”的结果都不能上线。

## 9. 失败降级和 UI

- Profile 不足：只使用角色卡默认风格。
- Profile 与角色卡冲突：角色卡优先，并记录冲突原因。
- LoRA adapter 加载失败：退回 Profile-only，不阻断对话。
- 用户关闭学习：停止采集新样本，保留或删除旧画像由用户选择。
- UI 需要展示：来源、样本数、置信度、最近更新时间、当前模式（角色默认/Profile/LoRA）、删除和重置按钮；不展示模型私密推理。

## 10. 实现状态与非目标

1. ✅ 复用现有 `StyleLearner/style.json`；在线 `profile` 与显式离线 `corpus_profile` 分开保存。
   `activation_revision` 是跨进程激活指针版本；长驻 Agent 每轮刷新，旧进程保存在线画像时不能复活已停用 corpus。
2. ✅ `src/veranima/core/style_corpus.py` 实现来源、清洗、弱标注、复核、门禁、版本与删除。
3. ✅ 聚合 `StyleProfile` 生成短 `StyleBrief`，按通道注入并给现有 `ResponsePlan.desired_length` 提供低优先级默认值。
4. ✅ 行为测试覆盖敏感正文/文件名不落盘、受管队列摘要与 segment 重算、输出预算、跨进程停用、到期清理、corpus 级原子 replace、partial delete tombstone、schema fail-closed、运行时优先级和 CLI 生产路径。
5. ⏸ 用户明确开启且语料授权、Profile baseline 仍不足时，才做可选 LoRA 实验。

暂缓：在线 RLHF/PPO、逐用户独立模型、多模型自动路由、无授权作者风格一键克隆、把风格样本直接塞进长期记忆。

## 11. 未标注文本的具体操作

### 11.1 用户只需要做什么

1. 准备 UTF-8 的 `.txt`、`.md` 或 `.jsonl`；多个输入文件会映射为匿名 `source-N` 场景，文件名不落盘。
2. 导入时明确 `source/owner/license` 并给出 `--consent`。原文件不会复制进运行时目录。
3. 导出复核 JSONL。每行只改 `decision/reason/annotator/corrected_labels`；正文、场景、风险、弱标签、segment ID 和版本均受摘要绑定，不可修改。
4. 应用复核并执行 `activate`。门禁不通过时保持 preview，不影响角色。
5. 暂时关闭时执行 `deactivate`；不再使用时执行 `delete`，删除片段、受管复核队列和 profile，并撤销运行时画像。

```bash
.venv/Scripts/python.exe -m veranima.cli style import my-style corpus/a.txt corpus/b.txt corpus/c.jsonl --source "用户自有文本" --owner user --license private-local-consent --consent
.venv/Scripts/python.exe -m veranima.cli style review-export my-style --limit 24
# 编辑 data/style_corpora/my-style/review_queue.jsonl：decision=accept/reject；其余字段通常不改
.venv/Scripts/python.exe -m veranima.cli style review-apply my-style
.venv/Scripts/python.exe -m veranima.cli style activate my-style
.venv/Scripts/python.exe -m veranima.cli style deactivate my-style
.venv/Scripts/python.exe -m veranima.cli style status my-style
.venv/Scripts/python.exe -m veranima.cli style delete my-style
```

### 11.2 自动完成的工作

- 原始语料不进入 `MemoryStore`、语言镜像或角色包；仅在 `data/style_corpora/<corpus_id>/` 保存可删除的低风险片段。命中敏感信息、凭据或身份事实的整段不持久化。
- 每个片段记录 `segment_id/corpus_version/source_index/scene/language/content_type/weak_labels/risk_flags/bucket`；抽样队列另记 `selection_reason=risk|uncertain|representative`。
- 代表抽样直接使用弱标签特征空间；当前不加载 bge-m3，因为抽样上限很小且目标是覆盖表达结构，不是语义主题。若实测超过本地文件规模后代表性不足，再将已有 embedding provider 接成可选输入。
- `profile.json` 只有聚合统计，不含原句、专名、身份或经历；运行时 `StyleBrief` 明确禁止复述语料和注入作者事实。
- 删除后只在 `deletions.jsonl` 保留 corpus/version/source hash/删除时间，不保留原文。
- 复核队列固定放在 corpus 目录内，不允许导出到任意外部路径；因此 `style delete` 能级联删除人工复核副本。

### 11.3 人工修正字段

```json
{
  "decision": "accept",
  "annotator": "user",
  "reason": "代表样本确认",
  "corrected_labels": {
    "formality": 0.3,
    "directness": 0.8,
    "detail_preference": 0.6
  }
}
```

`corrected_labels` 可省略，数值必须在 0..1。人工不是逐句标全量；未抽中的干净片段在门禁通过后参与聚合，风险片段必须被明确接受。
