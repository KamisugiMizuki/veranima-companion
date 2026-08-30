# CHARPKG_SPEC：`.charpkg` 角色包格式与生命周期

> 状态：设计稿 v1.0（2026-08-20）。
> 实现状态：Pkg-1/Pkg-2 核心已落地：`.charpkg` manifest/checksums 双清单、V3/legacy 兼容、成员/规范化路径/链接/压缩比/哈希/扩展名校验、角色卡与立绘引用验证、staging 后原子安装和 CLI 导入导出。默认不打包 `voice/`、`example_voices/` 或模型权重。版本目录、更新 diff、完整回滚和设置页 UI 仍待后续切片。
> 核心原则：角色包是可预览、可校验、可回滚的内容包，不是插件，不携带可执行代码，也不是记忆数据库备份。

## 1. 目标与非目标

### 目标

- 一个稳定的 `character_id` 对应角色卡、立绘、声音、示例语料和可选风格档案。
- 导入前能预览名称、版本、来源、许可、资源清单、权限和冲突。
- 资源哈希、大小、媒体类型和引用关系可验证。
- 路径穿越、符号链接、Zip bomb、恶意超大资源、任意覆盖和未知字段污染可拒绝。
- 安装是暂存目录 → 校验 → 原子改名；失败不破坏当前激活角色。
- 更新、卸载、恢复默认和回滚有明确状态。
- 兼容已有 Character Card V3 与旧 `.char` 包。

### 非目标

- 包内执行 Python/JS、插件、宏、工具调用或网络请求。
- 把用户聊天历史、SQLite、API key、登录信息和完整私人记忆默认打包。
- 通过角色包修改系统级身份边界、现实行动边界、隐私策略和安全规则。
- 在第一版实现角色商店、在线自动更新、付费签名体系或多租户隔离。

## 2. 文件扩展名与兼容策略

- `.charpkg`：新格式，默认导出格式，`package_format="veranima.charpkg"`。
- `.char`：旧格式，继续只读导入；导出可保留 legacy 选项，不能再作为新能力的唯一格式。
- 两者底层都可以是 ZIP，但 `.charpkg` 必须有版本化 `manifest.json`、清单哈希和明确根目录。
- 导入器先识别 `manifest.json.package_format`：旧 `.char` 走兼容映射，新包走严格校验。

## 3. 包目录

```text
manifest.json
checksums.json
character/
  character.json              # Character Card V3 真值
  card.md                     # 人类可读说明和来源边界
  portraits/
    ...png|jpg|webp
    image_description.txt    # 可选批量表情映射
  voice/
    refs/                     # 可选参考音频，需授权
    models/                   # 默认不打包，通常太大
  examples/
    dialogue.jsonl            # 可选，原创/授权示例
  style/
    profile.json              # 可选结构化 StyleProfile
    README.md                 # 来源、授权、脱敏说明
  memory/
    seed.jsonl                # 可选、默认不导出，必须显式确认
preview/
  cover.png                   # 可选，展示资源，不参与 prompt
  preview.json                # 可选，UI 摘要
```

### 3.1 各目录责任

- `character/character.json`：唯一运行时角色卡；必须是 V3 或可迁移的旧卡。
- `card.md`：来源、原创化边界、使用限制、资源说明；不作为系统 prompt 直接拼接。
- `portraits/`：角色卡引用的本地资源；路径只能相对 `character/`。
- `voice/refs/`：参考音频；导入时记录媒体信息和许可，不默认复制到共享资源库。
- `voice/models/`：默认拒绝或只接受明确允许的模型文件，避免包大小和授权风险；Yuki 微调权重不随普通角色包导出。
- `examples/dialogue.jsonl`：仅用于风格校准和人工预览，不能自动晋升为角色经历。
- `style/profile.json`：统计画像，不保存完整语料原文，不覆盖 Character Core。
- `memory/seed.jsonl`：只允许用户明确选中的、可审计的角色初始资料；禁止包含真实用户聊天原文和秘密。

## 4. manifest 契约

```json
{
  "package_format": "veranima.charpkg",
  "schema_version": 1,
  "package_id": "yuki-minakami-veranima",
  "character_id": "yuki",
  "version": "1.0.0",
  "display_name": "水上由岐",
  "author": {"name": "...", "contact": "..."},
  "source": {
    "type": "original|adaptation|fan_work|imported",
    "title": "...",
    "urls": [],
    "note": "来源和原创化说明"
  },
  "license": {"text": "...", "assets": "...", "audio": "..."},
  "compatibility": {
    "character_card": "chara_card_v3",
    "veranima_min": "0.1.0",
    "platforms": ["windows"]
  },
  "entrypoints": {
    "character": "character/character.json",
    "readme": "character/card.md",
    "style": "character/style/profile.json"
  },
  "features": {
    "portraits": true,
    "voice_refs": true,
    "voice_models": false,
    "style_profile": true,
    "memory_seed": false
  },
  "files": [{"path": "character/character.json", "sha256": "...", "bytes": 1234, "media_type": "application/json"}],
  "created_at": "2026-08-20T00:00:00Z"
}
```

规则：

- `package_id` 只用于包身份；`character_id` 是安装注册表稳定键，不能由显示名每次重新生成。
- `version` 使用 SemVer；资源替换和卡片行为变化都必须递增版本。
- `author/source/license` 不得省略；未知来源写 `unknown`，不能伪造授权。
- `files` 与 `checksums.json` 必须一致；manifest 自身不放入自身哈希。
- 未知顶层字段：同一主版本默认拒绝；更高 minor 字段可警告并隔离，不把未知字段写回角色卡。
- 敏感字段扫描失败时阻止导出，不提供“忽略并继续”快捷按钮。

## 5. 导出流程

```text
选择角色
→ 显示将导出内容和不包含内容
→ 用户选择声音/风格/初始资料
→ 复制到临时 staging
→ 规范化相对路径和 UTF-8/LF
→ 检查 Character Card V3 与资源引用
→ 计算 sha256/bytes/media_type
→ 写 manifest/checksums
→ 生成确定性 ZIP
→ 重新打开 ZIP 做一次验证
→ 输出 .charpkg
```

默认不包含：

- API key、access token、Cookie、绝对路径。
- `config.yaml` 全量配置。
- `data/veranima.db`、`chat.json`、原始消息、向量索引。
- 用户语音、私人图片和未授权参考音频。
- 大模型权重；需要单独安装/下载说明。

确定性要求：文件按 POSIX 路径排序，文本统一 UTF-8/LF，manifest 字段稳定排序；这样相同输入可得到可比较的哈希。

## 6. 导入流程

```text
选择 .charpkg
→ 复制到 quarantine，不直接解包到 characters/
→ 扫描 ZIP 元数据和路径
→ 校验成员数/单文件/总大小/压缩比
→ 拒绝目录外路径、符号链接、重复路径和可执行文件
→ 读取 manifest，校验 schema/version/id
→ 校验 checksums 和 JSON/媒体引用
→ 生成预览报告
→ 用户选择冲突策略
→ 解包到 staging/_import_<id>_<nonce>
→ 再次扫描解包树
→ 原子 rename 到 characters/<character_id>@<version>
→ 更新注册表和 active pointer
→ 写安装审计
→ 清理 quarantine/staging
```

### 6.1 安全限制建议

- 成员数：4096。
- 单文件：2 GiB；普通角色包默认 UI 限制 512 MiB。
- 总解压大小：8 GiB；普通导入 UI 默认 2 GiB。
- 压缩比：200 倍以上拒绝并要求人工检查。
- 允许扩展名：JSON/MD/TXT/PNG/JPEG/WebP/WAV/OGG/MP3/FLAC；可执行扩展名、快捷方式、脚本和动态库拒绝。
- 解析媒体时不调用外部程序；Pillow/标准库解析失败则隔离，不当作可用资源。
- 所有最终路径必须满足 `target_root` 的 `resolve().is_relative_to(target_root)`；Windows 驱动器路径、UNC 路径、NUL 字符均拒绝。
- 不跟随符号链接、junction 和 hardlink；ZIP 中的 link-like extra attributes 直接拒绝。

## 7. 冲突、更新与回滚

| 情况 | 默认处理 | 可选操作 |
|---|---|---|
| `character_id` 不存在 | 安装新角色 | 取消 |
| 同 id、导入版本更高 | 预览差异后更新 | 另存副本、跳过 |
| 同 id、版本相同 | 拒绝覆盖 | 另存副本、重新安装 |
| 导入版本更低 | 警告，不自动降级 | 明确确认后安装旧版本 |
| 当前角色有本地修改 | 显示字段/资源 diff | 保留本地、包覆盖、另存副本 |
| 显示名重复但 id 不同 | 允许安装 | 自动显示作者/版本区分 |
| 激活角色正在使用 | 先安装不切换 | 重启后切换 |

安装目录建议：

```text
characters/<character_id>/versions/<version>/...
characters/<character_id>/active.json
characters/<character_id>/backups/<timestamp>-<version>/...
```

激活只修改 `active.json` 或现有配置指针；任何卡片覆盖前先复制旧 active 版本。启动失败时自动回滚到上一个通过验证的版本。卸载只删除包资源，不删除共享用户记忆；恢复默认只恢复角色，不清理关系和聊天历史。

## 8. UI 流程

### 导入预览

显示：角色名、作者、来源、版本、许可、文件数/大小、立绘缩略图、声音是否包含、风格是否包含、记忆种子是否包含、验证警告、与现有角色差异。

### 导出

复选项：立绘、声音参考、示例语料、风格画像、用户选中的角色初始资料。每项旁边显示“是否包含原文/来源/敏感数据”。默认全不选记忆种子和模型权重。

### 角色管理

- 激活：只切换角色指针，记忆/关系是否共享遵循当前产品单库策略。
- 更新：显示版本和 diff，确认后安装。
- 另存副本：生成新 `character_id`，不覆盖旧卡。
- 卸载：要求输入角色名确认，保留最近一个恢复包。
- 恢复默认：只恢复该角色包，不清空聊天/记忆。
- 导出审计：显示上次导入来源、校验结果和安装时间。

## 9. 记忆、风格和隐私边界

- 角色包不等于用户数据包。
- `style_profile` 是派生统计，不是人格核心；导入后必须经过用户确认才启用。
- `memory.seed` 每条要有 `source`, `subject`, `sensitivity`, `confirmed_at`；无来源或 private/secret 默认拒绝。
- 包内来源声明不能替代真实授权；导入 UI 必须显示“由用户确认来源和许可”。
- 删除角色包不删除历史中已经存在的事实；用户要求忘记时走现有 MemoryStore 删除流程。

## 10. 兼容迁移

旧 `.char` 导入映射：

- 根 `manifest.json` → 新 `manifest` 的基础字段。
- `character/character.json` → `entrypoints.character`。
- 缺失 checksums 时导入器重新计算并标记 `legacy_unverified=true`。
- 缺少版本时按 `0.0.0-legacy` 处理。
- 原有 `image_description.txt` 继续由 `apply_portrait_description()` 消费。
- 不修改旧包原文件；迁移输出新 `.charpkg` 时才补齐 manifest/checksums。

## 11. 实现分期与验收

1. Pkg-1：manifest/checksum/schema/legacy import。
2. Pkg-2：quarantine、路径/链接/媒体/大小安全检查。
3. Pkg-3：staging、原子安装、冲突和回滚。
4. Pkg-4：设置页导入预览/导出选项/角色管理。
5. Pkg-5：风格/初始资料显式选择与隐私删除。

行为验收：

- 合法 V3 卡 round-trip 后角色名、核心字段、表情路径和 card.md 不变。
- `../`, 绝对路径、UNC、符号链接、重复成员、Zip bomb、未知危险扩展名均拒绝。
- 校验失败不改变 active 角色和现有文件。
- 更新中断可恢复旧版本；导入重复不覆盖原目录。
- 秘密、用户历史和未选 memory seed 不进入包。
- legacy `.char` 可导入并被明确标记未验证哈希。

暂缓：在线商店、远程签名服务、角色包内插件、模型权重自动下载。只有多角色发布和安全审计形成真实需求时再加。
