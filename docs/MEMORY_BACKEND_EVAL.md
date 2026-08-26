# 记忆存储选型评估（MEMORY_BACKEND_EVAL）

> 文档状态：选型评估与演进方案，未实施。
> 触发来源：`docs/newly_added/design_append.md` 中用户粘贴的第三方记忆库清单，要求逐个评估后给出新增或重写的设计方案。
> 结论先行：**不替换现有 SQLite 存储作为系统记录（system of record）**；吸收候选项目的四个机制（热度衰减、双时间线、离线整理、审核收件箱）以增量阶段落地。替换存储引擎的收益不成立。

## 1. 现状真值（评估基准）

当前实现：`src/veranima/memory/` 单一入口。

| 组件 | 实现 | 说明 |
|---|---|---|
| 存储 | SQLite（WAL、busy_timeout、外键） | `data/veranima.db`，gitignore |
| 全文检索 | FTS5 `memories_fts`（trigram） | 显式同步，非触发器（外部内容表模式实测会损坏库，已弃用） |
| 向量检索 | sqlite-vec `memory_vec`（cosine） | 扩展缺失时降级为纯 FTS |
| Embedding | 本地 bge-m3（fastembed ONNX / Ollama 可选），1024 维 | 无远程依赖 |
| 召回 | 混合：FTS + 向量 + 时间/重要性加权 | `recall_top_k=5`，`recall_threshold=0.3` |
| 契约 | 五层记忆、ADD-only 候选、版本链、`source_message_id` 追溯、事件生命周期、预算注入 | `MEMORY_SPEC.md` |

已有且不可丢失的能力：证据追溯、冲突不覆盖（新版本链）、隐私擦除（erase 级联）、行为级测试基线（478+ 通过）。

## 2. 候选项目核实结果

对 design_append 清单逐项核实。标注：✅ 已核实（本次实际访问确认存在）；⚠️ 未核实（仅凭清单描述，未访问仓库）。

| 项目 | 核实 | 关键事实 | 对 veranima 的适配判断 |
|---|---|---|---|
| Engram | ✅ arXiv 2606.09900《Less Context, More Accuracy》真实存在 | 双时间线事实追踪；dense+lexical+graph+recency 混合读路径；as-of 时点过滤；LongMemEval_S 83.6%，检索切片约 9.6k token；BGE 系列嵌入 | **机制最值得借鉴**：双时间线与显著性衰减可映射到现有版本链；其嵌入家族与本项目 bge-m3 同源 |
| kiwi-mem | ✅ GitHub 仓库存在（LucieEveille/ayao0912 均可见） | AI 伴侣记忆系统；向量搜索；AGPL-3.0；PostgreSQL/pgvector + FastAPI + Docker | 理念契合但栈不合：引入 Postgres+Docker 违反本项目本地单机约束；AGPL-3.0 若直接复用代码有传染义务；个人项目维护风险 |
| Memory Trigger | ⚠️ | 中文文档；命令行/MCP 双模；承诺闹钟、否认降权 | 承诺追踪 veranima 已有 PromiseBook；MCP 形态对本项目是额外进程 |
| KokoroMemo | ⚠️ | OpenAI-compatible 代理形态；记忆收件箱审核；会话状态板 | 代理拦截形态与本架构冲突（veranima 记忆在进程内）；「收件箱审核」理念可取 |
| MemoryConstellations | ⚠️ | 三层提取/分组/检索；星图可视化 | 可视化非刚需；机制与现有 curator 重叠 |
| GensokyoAI | ⚠️ | 两层记忆的 Agent 工具包 | 能力子集，无增量 |
| chronicler | ⚠️ | canon/heuristic/reflex 三级写入；ST 卡导入 | 分级写入理念与 ADD-only 候选近似 |
| HyperMEM | ⚠️ | 逐字存储+混合召回+生命周期；本地优先 | 与现状高度同构，无增量 |
| OpenMemory | ⚠️ | 自托管 SQLite/Postgres；可解释追踪 | 「可解释追踪」已满足（source_message_id） |
| MindCache | ⚠️ | 四类记忆分类；过去为真/现在为真区分 | 分类法与五层不同但等价；双时态理念同 Engram |
| Mnemosyne | ⚠️ | 图谱+向量+全文 RRF；艾宾浩斯衰减；版本快照；Obsidian 集成 | 衰减+快照理念可取；其余超需求 |
| LATRACE-AI | ⚠️ | 多模态 TKG；LoCoMo/LongMemEval SOTA | 多模态记忆暂无产品需求；重依赖 |
| awesome-ai-memory | ✅ 性质明确（清单仓库） | 导航用 | 仅作后续调研索引 |

清单中「kiwi-mem 有遗忘曲线/Dream 整合」「Engram 83.6%」两条核心卖点均已核实为真实陈述；其余项目的具体功能数字未逐一验证，若进入实施前必须重新核对该仓库当前 README。

## 3. 替换方案为何不成立

1. **数据所有权**：人物记忆的版本链、来源消息 ID、事件生命周期与关系状态深度耦合在现有 schema 和行为测试里。迁移到任何外部记忆服务都要先把这些契约在对方模型上重建一遍，收益为零、风险为正。
2. **部署形态**：kiwi-mem 需要 PostgreSQL+Docker；Memory Trigger/KokoroMemo 是独立进程或代理。veranima 的分发目标是 clone 后填 key 即跑（用户既定约束），当前栈零额外服务。
3. **规模错配**：单用户单机，记忆量级为万条以下。SQLite+sqlite-vec 在该量级的检索延迟和准确率不是瓶颈；没有任何候选项目针对「中文陪伴对话+本地 bge-m3」给出优于现状的证据。
4. **许可证**：AGPL-3.0（kiwi-mem）直接复用代码会使本项目承担传染义务；MIT/Apache 项目又未提供超出现状的机制。
5. **清单自身的局限**：多数为个人项目，star 数与维护活跃度未经核实，作为运行时依赖的供应链风险高于自持代码。

## 4. 吸收方案（增量阶段）

按收益/成本排序，全部在现有 SQLite 上实施，不改存储引擎。

### M-A 记忆强度与自然遗忘（借鉴 Engram 显著性衰减、kiwi-mem 热度、Mnemosyne 艾宾浩斯）

- `memories` 增加 `strength REAL`（已有字段则复用）与 `last_recalled_at`；
- 召回命中即强化（strength 提升），长期未命中的低层记忆按曲线衰减；
- 衰减只影响召回排序与注入预算，**不删除数据**；core_profile 层永不衰减；
- 现有 `decay_enabled=false` 开关保留为总闸。

验收：行为测试断言「高频共提的记忆排在久未提及之前」「衰减不减少 memories 行数」。

### M-B 双时间线（借鉴 Engram bi-temporal、MindCache 过去/现在之分）

- 为事件类记忆显式区分 `valid_from/valid_until`（事态有效期，已有 expires_at 可承载一部分）与 `recorded_at`（系统认知时间，即 created_at）；
- 冲突更新继续走版本链（现状），但召回默认只取「现在有效」版本，as-of 查询接口供审计与「我当时以为」表达使用；
- 这是把现有隐式行为显式化，迁移成本最低。

### M-C 夜间整理任务（借鉴 kiwi-mem Dream）

- 新增离线 job（桌宠空闲或每日一次）：把近 N 天 episodic/session 片段整理成更抽象的上层摘要（shared_meaning/semantic），并降低被摘要原始片段的召回权重；
- 整理产物走既有 ADD-only 候选校验与来源追溯，**不走 LLM 自由改写**；
- 与现有 `curator_turns=8` 的在线整理互补：在线管即时去重合并，夜间管跨日叙事压缩。

验收：整理前后记忆总数单调不减、每条产物有 source_message_ids、随机抽查无虚构事实。

### M-D 审核准入可选化（借鉴 KokoroMemo 收件箱）

- config 增加开关：低置信度候选（confidence < 阈值）进入待审队列，由用户在设置界面批准后才成为可召回记忆；
- 默认关闭（维持自动通过+阈值），作为高误记率时期的降级手段。

### 明确不吸收

- 图谱/TKG 后端（LATRACE、Mnemosyne 图谱层）：单机规模下复杂度不成比例；
- PostgreSQL/pgvector/远程服务：违反本地单机分发约束；
- 记忆代理拦截形态（KokoroMemo）：与进程内架构冲突；
- 星图等可视化：非产品目标，需要时基于现有数据另做只读页面。

## 5. 与 MEMORY_SPEC 的关系

本文件是 MEMORY_SPEC §2 参考系统的延伸评估。结论反向写入 MEMORY_SPEC：技术约束段补充「2026-08-26 选型评估结论：维持 SQLite 栈，吸收机制见本文档」。若未来触发重新选型（多设备同步、亿级语料、官方 sqlite-vec 停维），以本文件 §3 的判据为准重新评审。

## Sources

[1] https://arxiv.org/abs/2606.09900 — Less Context, More Accuracy: A Bi-Temporal Memory Engine for LLM Agents (Engram)
[2] https://github.com/ayao0912/kiwi-mem — kiwi-mem: AI 伴侣记忆系统
