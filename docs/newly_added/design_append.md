<!-- 新功能点子粘贴区：读完评估后，已并入 docs/DESIGN.md 的段落会从本文件删除。
     保持本文件常驻，新点子直接粘贴进来即可。 -->

<!-- 2026-08-29 本批 5 条已全部裁决并并入 DESIGN.md §10：追问（落码）、被看穿（prompt 级）、
     翻旧账（接线孤儿函数）、默契沉默（不实现）、主动输（暂缓）。 -->

<!-- 2026-08-29 视觉小说 UI 稿已并入 ANDROID_UI_VISUAL_NOVEL_SPEC.md（旧稿段落待删，见文末） -->

<!-- 2026-08-30 三块新功能设计稿：性格成长树 / 记忆库管理 / 锁屏组件。评估见 DESIGN.md §11（并入后本段删除） -->

## 11. 三块新功能设计（2026-08-30 数据面评估稿）

### A. 性格成长树（可视化技能点 / 亲密度进度）

**数据面现成（无需新字段）**：
- `RelationshipModel` 七维（trust/familiarity/intimacy/reciprocity/safety/conflict_tension/repair_progress，persona.py:335）+ `derive_relationship_stage` 五阶段（初识→熟悉→信任→亲密伙伴→长期共同体，STAGE_THRESHOLDS）
- `StyleParams` 四维可学习风格参数（reply_length/formality/humor/topic_follow，learning.py:50）
- procedural 层记忆（交互规则/偏好）→ 即「技能点」实体：规则数+最新更新时间
- `promises.py` 承诺跟踪 → 履约率

**设计**：设置页新增「成长」分区卡片（沿用 surface-card 风格）：
1. 关系阶段横幅：当前阶段名（衬线大字）+ 下一阶段差距（最近的两维差值提示，如「离『信任』还差 trust 0.72 / familiarity 0.70」）
2. 七维进度条：每维一根 Hairline 底条 + 珊瑚填充 + 数值%（只读展示，无交互——数据是 core 侧确定性更新，UI 不做手调）
3. 技能点面板：procedural 规则按 meta.kind 分组计数（interaction_rule / preference / 其他），显示「已学会 N 条规矩」+ 最近 3 条内容摘要
4. 风格四维雷达/条状：展示当前文风画像（只读）

**判定**：✅ 能用。数据全在 `relationship.to_dict()` + `memory.stats()` + `list_layer("procedural")`，bridge 加一个 `growth_report()` 打包即可，零 core 改动。不做：手动加点/洗点（破坏 core 确定性）、技能树多级解锁（无对应机制）。

### B. 记忆库管理（标签云 + 手动删除不良记忆）

**数据面现成**：
- `MemoryStore.erase(id)` 已实现完整删除（整条版本链 + FTS + 向量，store.py:1108）——删除记忆的核已存在
- `memories.category` 字段有值但分布稀疏（实测 mostly unknown/event/preference/promise/sensitive）
- `list_layer(layer, limit)` 可列各层；`stats()` 有分层计数
- memory_review_inbox 默认关闭（M-D 审核收件箱）——「不良记忆」的自动识别通道已存在但未启用

**设计**：设置页「记忆」分区：
1. 标签云：按 category 聚合记忆条数（无 category 的归「未分类」），字号/深浅随数量映射；点标签 → 该分类记忆列表（content 摘要 + strength + updated_at）
2. 列表项右滑/长按 → 「删除」确认（调用 `bridge.erase_memory(id)` → `MemoryStore.erase`）；删除后 FTS/向量同步清——用户可手动清掉「记错的事」
3. 顶部小字：总记忆数 / 分层计数（stats()）

**判定**：✅ 能用，工作量小（bridge 加 list/erase 两个 callAttr + 一个 Compose 分区页）。⚠️ 注意：core 的记忆提取可能重新生成被删记忆（下次对话同样内容再入 memories）——「删除」是幂等操作而非永久拉黑，文档需写明；永久拉黑需加 ignore 表（暂缓）。

### C. 锁屏组件

**数据面**：无现成。Android 锁屏 = App Widget（RemoteViews，Kotlin 壳层实现，与 core 无关）或锁屏通知面板（现有主动消息通知已能在锁屏显示——CompanionService 常驻 + POST_NOTIFICATIONS 已就绪）。

**设计（最小可行）**：
1. **锁屏小部件（App Widget，首选）**：2×2 透明卡片，显示立绘头像（assets/portraits 解包文件）+ 最新一条主动/回复消息摘要（widget 内 1-2 行，禁止长文本）；点击 → 打开 MainActivity。数据源：widget 进程读 filesDir/veranima.db 最新消息（同进程 DB 只读，无需 bridge IPC）；刷新：onUpdate + 每次消息落库后 `sendBroadcast(ACTION_APPWIDGET_UPDATE)`（core 侧 bridge 加一行广播，或 Android 侧 CompanionService 轮询）。
2. **锁屏通知面板（零开发，现状即达）**：主动消息通知已可锁屏显示——立绘 largeIcon 已有。此项只补「通知栏长按 → 直达聊天」已存在。

**判定**：⚠️ 能做但收益需确认。锁屏小部件是纯壳层工作（RemoteViews 布局 + widget provider，无 Python 改动），但：a) MuMu 模拟器锁屏/小部件行为与真机差异大，验收只能在真机（用户手机 Android 16）；b) 伴侣类 App 锁屏小部件的使用场景是「不亮屏看消息」——与现有通知栏（已能锁屏显示）功能重叠 80%。建议：先做「立绘小部件」，不做消息流（避免和通知重复）。

---

<!-- 旧稿清理：视觉小说 UI 稿（第 7-41 行原文）已并入 ANDROID_UI_VISUAL_NOVEL_SPEC.md，删除本段 -->
