# 记忆模块 · 档位 B（结构地图）

> 目的：按"模块有哪些部分、各部分边界在哪"组织，适合快速定位结构矛盾。
> 不含实现细节（权重数值、SQL 语句、函数签名见代码与 MEMORY_SPEC.md）。

## 1. 组件清单

| 组件 | 职责 | 位置 |
|---|---|---|
| MemoryStore | 唯一读写入口（单连接 SQLite） | memory/store.py |
| schema | 建表 + 迁移（列补齐、vec0 退役、FTS 重建） | memory/schema.py |
| EmbeddingProvider | 文本→向量（安卓走远程 dashscope，本地可换） | llm/ 层 |
| validate_candidate | 写入前校验白名单（kind/来源/敏感词） | store.py 顶部 |
| decay / digest | 夜间维护：强度衰减、情节→摘要 | store.py + agent |
| memory_review_inbox | 审核准入收件箱（默认关闭） | schema.py |

## 2. 数据分层（写入侧）

```
对话消息(messages) ──原始永远保留──┐
                                   ├─→ 候选(candidate, LLM 提取)
                                   │     ├─ validate_candidate 过滤
                                   │     └─ store() ADD-only 写入 memories
                                   │           ├─ memories 行（含 category 兜底分类）
                                   │           ├─ memories_fts 行（M-3 显式同步）
                                   │           └─ memory_embedding 行（向量，写时 embed）
                                   └─ 修正 → update_latest 开新版本链（supersedes）
```

## 3. 检索侧（recall 一条路径）

三源候选池合并 →（向量 knn ∪ FTS 直接命中 ∪ provenance 关联消息命中）
→ 统一打分排序 → top_k → 双时间线过滤（valid_from/is_active）→ 注入 prompt。

关键边界：
- 向量失败（网络/embedding 挂）降级 FTS-only，不报错、不补随机分。
- 兜底 bigram 包含匹配仅在全空时启用（低精准，标记 ponytail）。

## 4. 生命周期

| 阶段 | 机制 | 不变式 |
|---|---|---|
| 写入 | ADD-only | 永不 UPDATE/DELETE 原行 |
| 修正 | 版本链 supersedes | 旧版本 current=0 但保留 |
| 召回命中 | strength +0.05（封顶 1.0） | 用得多=记得牢 |
| 衰减 | decay 按年龄降 strength | core_profile 豁免 |
| 摘要 | 夜间 digest 把 episodic 升层 | 原始片段×0.5 降权不删 |
| 遗忘 | 只有低 strength 沉底，不物理删 | 物理删=记忆库手动 erase |

## 5. 一致性责任表（结构矛盾高发区）

| 变更操作 | memories | memories_fts | memory_embedding | 谁负责 |
|---|---|---|---|---|
| store() | ✅ | ✅ | ✅ | MemoryStore 内部 |
| update_latest() | ✅新版本 | ✅ | ⚠️ 新向量 | 核对：版本链上向量是否补 |
| erase() | ✅链级 | ✅ | ✅ | store 三清 |
| 外部直改 DB（备份导入） | ✅ | ⚠️ schema 启动重建 | ❌ 需重嵌 | 备份导入端指纹重铸 |

## 6. 自查问题（审代码时问）

1. 有没有第二条绕过 store() 的写路径？（应该只有 backfill/migration 类例外）
2. recall 的权重和是否为 1.0？某源不可得时是否归一化剩余权重？
3. decay 是否豁免 core_profile？digest 是否只处理当日新增且未被引用过的素材？
4. category 是否有兜底（NULL→按层默认）？
5. 删除操作是否三清（行/FTS/向量）？
