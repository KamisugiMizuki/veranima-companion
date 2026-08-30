# veranima 设计文档地图

按模块组织。`roadmap/` 是总纲与实现分期，其余目录各自对应一个功能模块；单文件即该模块的完整契约，audit/review 类文档单独放在模块内的 `audits/`。

## 根模块

| 目录 | 模块 | 内容 |
|---|---|---|
| `roadmap/` | 总纲与分期 | DESIGN.md（人物中心四变量公式 + 拟真五维 + R0–R5 总序）+ R0~R5 各期契约 + DISTRIBUTION_SPEC（发行/打包） |
| `memory/` | 记忆系统 | MEMORY_SPEC（M1–M8 契约：分层/召回/衰减/双时间线）+ MEMORY_BACKEND_EVAL（选型评估结论，sqlite-vec 退役史） |
| `persona/` | 人格循环 | PERSONA_LOOP_SPEC（P-0~P-9）+ COMPANION_CONTINUITY_DESIGN（多端连续性）+ RELATIONAL_TENSION_SPEC（关系张力 TV）+ SHARED_CREATION_SPEC（共同创作） |
| `proactive/` | 主动发言 | QQ_PROACTIVE_SPEC（QQ 时机引擎） |
| `virtual_life/` | 虚拟日程与空间 | VIRTUAL_SCHEDULE_SPEC（作息/睡眠/日程）+ VIRTUAL_SPACE_SPEC（CurrentScene/地点） |
| `expression/` | 表达层 | STYLE_LEARNING_SPEC（文风学习）+ EXPRESSION_GENE_TRANSFER_SPEC（表达基因迁移） |
| `vision/` | 视觉注意力 | VISION_SPEC（截屏观察→联想→主动） |
| `search/` | 联网搜索 | WEB_SEARCH_SPEC（SearXNG/博查双后端、语义定位、EvidencePack） |
| `images/` | 图片链路 | IMAGE_MESSAGE（用户图片+识图）+ IMAGE_STICKER_LIFECYCLE（表情包生命周期）+ QQ_STICKER |
| `voice/` | 语音 | STT_SPEC（SenseVoice；TTS 定案在 GPT-SoVITS，见 config 与 tts/） |
| `desktop/` | 桌宠 GUI | GUI_SPEC（Electron 壳：主窗/聊天窗/设置/日志） |
| `android/` | 安卓端 | ANDROID_SCOPE_SPEC（APK 功能范围裁决）+ ANDROID_UI_VISUAL_NOVEL_SPEC（视觉小说舞台化） |
| `character/` | 角色包 | CHARPKG_SPEC（.charpkg 导出/导入/安全边界） |
| `hermes/` | Hermes 集成 | HERMES_AGENT_INTEGRATION_SPEC（R5 执行后端对接契约） |
| `newly_added/` | 灵感暂存 | design_append.md（用户随手记的功能点子常驻模板，评估后并入 DESIGN.md） |
| `for_users/` | 人读文档 | 设计视角三档（A 一页纸 / B 结构图 / C 契约）+ 项目思维导图 HTML |

## 阅读顺序建议

1. `roadmap/DESIGN.md` —— 产品定位、四变量、R0–R5 是什么
2. 感兴趣的模块目录内主 SPEC（每个文件头部有设计原点）
3. 想知道某模块"做到哪了"：看该 SPEC 的实现状态节 + `for_users/` 思维导图 + 本目录下的不符清单

## 维护约定

- 新增模块文档 → 建同名目录放入；跨模块引用写全路径 `docs/<模块>/<文件>.md`
- 审计/评审类一次性记录文档一律进模块的 `audits/`，不与契约混放
- 文档里出现行号、函数签名、SQL 即越界（人读文档只允许设计语言：表名、组件名、概念、不变式）
