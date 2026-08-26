# QQ_STICKER_SPEC：静态表情包记忆与发送

> 状态：实现版 v1.0（2026-08-21）。
> 复用：`core/stickers.py`、`Agent.annotate_sticker()`、QQAdapter，不新建数据库。
> 后续设计：授权审核、用户隔离、删除入口、完整匹配与迁移见 `IMAGE_STICKER_LIFECYCLE_SPEC.md`；该设计尚未实现。

## 分类

1. 普通照片/截图：送当前轮多模态 LLM，不保存到表情库。
2. GIF/WebP 动图：送当前轮多模态 LLM，不保存；避免动图无限增长和误发送。
3. 静态表情包：只有 LLM JSON 中 `is_sticker` 是字面 boolean `true` 才保存；字段缺失、字符串 `"false"`、其他类型或标注失败均不落库。

标注在当前文字回复发送后进入后台任务，并复用现有 Agent lock；因此不阻塞首响，也不与下一次 Agent LLM 调用并发。adapter 退出时不取消已进入 `to_thread` 的任务，而是 shield 并等待同步 worker 自然返回，避免协程先释放共享 Agent lock、旧线程继续访问 LLM/SQLite。

## 标注数据

```json
{
  "is_sticker": true,
  "meaning": "一句话含义",
  "moods": ["调侃", "无语"],
  "scenarios": ["用户自嘲", "轻微吐槽"]
}
```

原图保存为 `data/stickers/<内容SHA-256>.<ext>`，使用排他创建防止并发静默覆盖；`index.json` 另存完整 64-bit dHash、MIME、情绪、情境、创建时间和使用次数。dHash 仅用于汉明距离小于等于 5 的近似判重，不再承担文件唯一命名；索引写入临时文件后原子替换。

## 发送

回复完成后，QQAdapter 从回复情绪和文本情境检索候选，情境命中权重高于单纯情绪命中，低使用次数优先；没有合适候选不发送。当前发送使用 OneBot `[CQ:image,file=...]`，后续可按 NapCat 能力改为 face/market 表情段，不影响库契约。

## 原生 QQ 表情输入

- OneBot 原生 `face` segment、CQ `face` 字符串和 NapCat 的 `[表情 [名称]]` 占位统一转换为 `[QQ表情：名称]` 文本输入；纯表情消息不能因无普通文本而被跳过。
- 含文字的消息若 face 只有数字 ID，则保留为 `[QQ表情：id=...]`；有 `summary` 时优先使用 summary。

## 生命周期

- `delete(hash/file)` 同时删除原图和索引，拒绝越界路径。
- index 中缺失/越界文件启动时跳过，不阻塞聊天。
- 不把原图写入长期记忆；仅当前轮由 LLM 视觉理解，结构化标注进入表情库。
- 未配置多模态 LLM 时，入库标注失败，普通 QQ 文字仍正常回复。
- 当前输入路径兼容 OneBot `face`、CQ `face` 和 NapCat `[表情 [名称]]` 占位；统一作为文本语义输入，不要求先获得表情图片二进制。
