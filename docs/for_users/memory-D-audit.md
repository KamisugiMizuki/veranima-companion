# 记忆模块 · 档位 D（代码对照走查）

> 目的：每个组件给「代码位置 + 它保证什么 + 与规范/其他组件的已知矛盾点」。
> 适合拿着它逐文件走查、审 PR、或核对 MEMORY_SPEC 与实现的一致性。
> ⚠ 标记 = 走查时已实测确认的矛盾；其余为待核对声明。

## 文件地图

| 文件 | 行数 | 职责 |
|---|---|---|
| memory/store.py | ~1414 | MemoryStore 全部读写接口 + MemoryEntry + validate_candidate |
| memory/schema.py | ~349 | 建表 SQL + init_db 迁移链（补列/FTS 重建/vec0 退役） |
| memory/embedding.py | ~191 | EmbeddingProvider 工厂（远程 dashscope / 本地） |
| memory/brief.py | ~168 | 召回结果→prompt 注入打包（预算裁剪） |
| （上游）core/agent.py | — | 候选提取、digest、decay 调用、recall 注入时机 |

## 走查线 1：写路径（store.py）

`store()` L353 → 校验/兜底/INSERT/FTS/向量
- **保证**：layer 白名单（LAYER_R1_MAP 先映射）；category 空按层兜底（L340 附近）；同函数内写 memories + memories_fts + memory_embedding。
- ⚠ **矛盾（已实测）**：`mood_at` 曾从 SELECT 列清单漏出（本轮修）。列清单手写三处（store/recent_messages/list_layer），漏列无类型检查兜底——改列时三处都要核对。
- 核对点：`_store_embedding()` L233 失败仅 log——向量缺失的记忆只能被 FTS 召回，无告警面。是否接受静默降级为 FTS-only？（当前设计=接受）

`update_latest()` L687 → 版本链
- **保证**：新行 version=旧+1、strength=旧、category 继承；meta.supersedes=old_id（调用方显式传入优先）。
- 核对点：旧行 current 标记在哪？（读：`is_active`/`_superseded_ids` L726 反查 supersedes——**链方向靠 meta JSON，无索引**，记忆量大时 `_superseded_ids` 全表扫）。

`validate_candidate()` L90
- **保证**：kind 白名单、source 必填（曾静默全拒的坑源）、敏感词 SECRET_PATTERNS 拒收。
- 核对点：agent 直接 store() 的路径（digest L1883、tension L695、promises）**绕过** validate_candidate——这是有意的（带完整来源的内部写入），但审计时要确认每处 bypass 都自带来源。

## 走查线 2：读路径（recall）

`recall()` L887 → 三源候选池
1. 向量：`_knn()` L247（numpy 点积，归一化 blob）
2. FTS 直接命中 memories_fts（M-3 规范记忆全文）
3. 旧路径：messages_fts 命中 → provenance 关联记忆
4. 兜底：池全空时 bigram 包含匹配（同层 limit 200）

`_score_entry()` L992
- ⚠ **矛盾（已实测）**：`recall` 的 docstring 写「score = 0.45*semantic_sim + 0.20*FTS + 0.15*freshness + 0.10*importance + 0.10*confidence」（R1_SPEC 4 旧公式），**实际代码是八项**：sim 0.35 / fts 0.20 / temporal 0.15 / subject 0.10 / age 0.05 / importance 0.05 / confidence 0.05 / strength 0.05（M-A 后加的 strength 信号，docstring 没跟上）。以代码为准，或修 docstring。
- **保证**：权重按可得项归一化（wsum），不补随机分。
- 核对点：命中后 strength+0.05 写回在哪？（在 `get` 还是 `recall` 尾部？grep `+0.05` 确认调用点唯一，避免 decay 抵消）。

`recall_asof()` L1013（双时间线审计视图）+ `list_layer()` L768（include_superseded 开关）。

## 走查线 3：维护与一致性

| 机制 | 位置 | 矛盾/风险点 |
|---|---|---|
| decay | store.py L1107 | core_profile 豁免；物理删=永不 |
| digest | agent.maybe_nightly_digest | 已修：只取当日+排除已引用来源；`json_each` 查询依赖 SQLite≥3.18（安卓端 OK） |
| FTS 重建 | schema.py init_db | 计数不等即全量重建——**memories 与 fts 不同事务**，中途崩溃由下次启动自愈 |
| backfill_categories | store.py L55 + bridge.boot | 幂等；注意 digest 降权用的裸 SQL UPDATE 不过 store() |
| erase | store.py | 链级三清；messages 不级联（MEMORY_SPEC 14.2） |
| 备份导入 | core/backup.py | 指纹重铸（换 embedding 模型全量重嵌）——向量一致性的最后兜底 |

## 走查线 4：周边表边界

**规范记忆**（参与 recall 打分）= memories 一表。**其余全部是"记录"不是"记忆"**：messages / sleep_cycles / virtual_life_events / proactive_feedback / self_model_chapters / agent_state / task_runs / memory_review_inbox。
- 核对点：有没有代码把周边表内容塞进 recall 候选池？（当前没有；provenance 关联 messages 仅作 FTS 信号，不进向量）

## 已知跨文档漂移（待裁决）

1. ⚠ recall docstring 权重 ≠ 代码（上文）。
2. MEMORY_SPEC.md 的 M-A~M-D 条目 vs 代码：strength 信号、双时间线、digest、review_inbox 已实现，但 SPEC 的 M-x 编号在代码里是注释引用——搜代码请搜函数名别搜编号。
3. `LAYER_R1_MAP` 的 R1 类型名→层映射在 store/schema 两处各自维护？（核对 import 唯一性）。
