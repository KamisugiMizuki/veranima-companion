# 记忆模块 · 档位 C（功能分区 + 接口契约）

> 目的：按"做什么"分区，每区给接口和行为契约，适合实现时核对是否越界。
> 不列具体权重/SQL/签名（那些看代码，这里只说"有什么"和"保证什么"）。

## A. 写入区

**入口**：`store()` / `update_latest()`
**保证**：
- store 永远新增行，不覆盖；layer 校验白名单；category 空则按层兜底
- 写入同时同步三处（memories / memories_fts / memory_embedding），任一失败不阻塞其余（FTS/向量失败仅告警）
- update_latest 开版本链（meta.supersedes 指向旧行），不修改历史行
- 候选写入前先过 validate_candidate（kind 白名单、provenance 必填、敏感词拒收）

**审核准入收件箱（memory_review_inbox）**：默认关闭；开启后候选先进收件箱，人工 decide_review 批准才 store()。

## B. 检索区

**入口**：`recall()` / `search_messages()` / `recall_asof()` / `list_layer()`
**保证**：
- recall 混合检索（向量+FTS+时间/重要性信号加权），top_k 可调，支持按 layer 过滤
- 降级链：embedding 挂 → FTS-only；全空 → bigram 兜底（精准度低，仅作最后防线）
- 时间旅行：recall_asof(时刻) 只看当时有效的版本（双时间线 valid_from/is_active）
- 命中记忆 strength +0.05（封顶 1.0）——"用得多=记得牢"
- search_messages 只搜原始消息（不搜提取的记忆），支持分页 before_id

## C. 维护区

**入口**：`decay()` / 夜间 digest（agent.maybe_nightly_digest）/ `curate()`
**保证**：
- decay：按年龄降 strength；core_profile 豁免；不物理删
- digest：当日新增 episodic（排除已整理过的来源）→ LLM 摘要 → 写入 shared_meaning 层带 digest_date + 来源消息 ID；原始片段 ×0.5 降权；当日已生成则跳过
- curate：分层统计，报告遗忘/待整理数
- 一致性修复：启动时 FTS 计数不等则重建；backfill_categories 回填 NULL 分类

## D. 删除区

**入口**：`erase()`
**保证**：
- 默认按 id 删**整条版本链**（行+FTS+向量三清）
- 原始 messages 不级联删除（原文删除是独立操作，不在记忆删除时静默发生）
- 幂等：已删的不报错

## E. 周边存储（同库不同契约）

messages（原始对话，含 tone/mood/energy/attachments 列）/ sleep_cycles（用户作息周期）/ virtual_life_events（角色日程）/ proactive_feedback（主动消息反馈+追问状态机）/ self_model_chapters（自我模型章节）/ task_runs / memory_review_inbox / agent_state（单行状态快照）

> ⚠ 这些表与 memories 共享一个连接和文件，但**不参与 recall 打分**；各自有独立读写接口。区分"规范记忆(memories)"与"周边记录(其他表)"是理解本模块的关键边界。

## F. 迁移区

vec0 退役（sqlite-vec 虚拟表 → 归一化 blob + numpy 点积）/ 补列迁移（tone_at、user_asleep 等）/ 备份导入端指纹重铸（换 embedding 模型时全量重嵌）

## 接口速查（只列名字，不列签名）

store / update_latest / get / get_history / list_layer / recall / recall_asof / search_messages / recent_messages / store_message / queue_review / list_review / decide_review / decay / curate / erase / save_state / load_state / warm_embedding / _knn / _store_embedding
