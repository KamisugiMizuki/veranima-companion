# 文档与代码不符清单

> 生成：2026-08-31（docs/ 按模块重构时逐条核对）。
> 每条 = 文档位置 → 文档说法 → 代码实况 → 建议。均为本次实际检索/读码核实，非推测。
> 全部条目已按用户逐条拍板处理完毕（见「处理」列）。

| # | 严重度 | 文档位置 | 文档说法 | 代码实况 | 处理 |
|---|---|---|---|---|---|
| 1 | 高 | docs/roadmap/DESIGN.md §9 近期设计扩展（VIRTUAL_SPACE 行） | 「当前为设计稿，尚未实现」 | CurrentScene/SpaceProfile/地点路线/路由转换已在虚拟日程运行时内实现，且行为测试覆盖 8 份空间测试文件（安卓与桌宠端都在消费）；虚拟空间审计文档两份也已确认落地修复 | ✅ DESIGN.md §9 已改为「已实现并接入日程运行时」 |
| 2 | 中 | docs/hermes/HERMES_AGENT_INTEGRATION_SPEC.md | 「文档状态：架构修改方案，尚未实施」 | Hermes/dsh 桥接、任务会话、工件生成、审批流转均已在生产链路（QQ 适配与 CLI 都装配了任务管理器和桥） | ✅ 文档头已改为「已实施」并注明个别项以各节内联状态为准 |
| 3 | 中 | docs/android/ANDROID_SCOPE_SPEC.md（C1） | 「删除 core/attention/*（视觉注意力六件套）」 | 该六件套仍在仓库且被桌宠服务与安卓桥消费（安卓端 visual_note→proactive_from_visual 链路 08-30 刚上线）；gradle 打包层也没有做排除 | ✅ 按实际改写：处置列改「安卓不接线」，注明安卓感知链路（前台包名→联想）与本模块无关、bridge 零 import、gradle 物理排除未执行=APK 内死代码；Windows 侧不在范围 |
| 4 | 低 | docs/roadmap/R4_SPEC.md §5 配置示例 | `quiet_hours: [23, 8]` 作为常规配置展示 | 静默时段已裁决退役（由虚拟日程睡眠块承担），运行配置中显式关闭 | ✅ R4_SPEC §5 示例已加退役注释 |
| 5 | 低 | docs/memory/… 与 docs/android/ANDROID_SCOPE_SPEC.md | 安卓范围内提及 sqlite-vec/vec0（SCOPE 的「交付面」一行） | vec0 已整体退役为 blob 表 + numpy KNN（同文档下方裁决也写了，前后两行自相矛盾） | ✅ 交付面行已改为与 gradle pip 清单一致的实际依赖 |
| 6 | 低 | docs/voice/STT_SPEC.md、docs/roadmap/DESIGN.md（语音相关行） | 只描述常驻式运行链 | 08-29 语音端裁决为「按需启动/释放以释放显存」+ 冷启动实测数据，该决策未回写任何文档 | ✅ STT_SPEC 升 v1.3，回写按需启停+冷启动实测数据 |
| 7 | 低 | docs/proactive/PROACTIVE_DESIGN_REVIEW.md、docs/virtual_life/audits/* | 各测试基线数字（919/930/967/970 passed） | 当前实测 1024 passed / 4 failed（4 红均为存量：睡眠边界×2、vec0 迁移、electron 启动契约） | ✅ 用户拍板：6 份 virtual_life 审计 + PROACTIVE_DESIGN_REVIEW 全部删除；README/空间 SPEC 引用已清 |

## 核对过、确认一致的重点项（不用动）

- 记忆召回权重公式（recall 文档式 = store 实际权重）、vec0→blob 迁移器存在
- P-0~P-9 全部有人格模块对应实现（persona 循环、冲突闸门、表达计划）
- 表情包子系统：核心存在且 config 实际启用（学习/发送均开）——08-29「砍掉」口头裁决与现状相反，**若真要砍请另立裁决**，文档目前按启用写是对的
- 睡前牵挂（DESIGN.md §10 主动引擎段落）与追问闭环均已在问候引擎内
- 安卓默认角色卡：生成器与桌宠壳的矛盾（gen_config 指 lin / run_spike 推 yuki）属工具脚本不一致而非文档不符，已在 memory 记录，建议顺手统一 run_spike 推送目录
