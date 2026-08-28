# 安卓移植功能范围（定稿 2026-08-29）

> 本表只约束安卓 APK 打包面；Windows 端功能不受影响，QQ/TTS/STT 在安卓 App 验收前仍是生产链路。
> 判定来源：用户逐项拍板（B 组早前已定，C 组 2026-08-29 定案）。

## 总表

| 处置 | 模块 | LOC | 理由 / 说明 |
|---|---|---|---|
| **保留** | A 组伴侣本体：agent、memory/*、persona、reflection、prompts、virtual_schedule、learning、style_corpus、state、ambient、proactive、promises、character、roles、character_archive、capability、interrupt、segments、tension、review、holiday_calendar（免费远程 API）、reply、backup、shared_creation、creation_relationship、image_payload、config、app、llm/client | ~13.9k | 伴侣的脑子，一行不删 |
| **删除** | QQ 全家：adapters/qq、qq.py、qq_advisor、qq_proactive | 1743 | QQ 通道退役（已拍板），App 内聊天是唯一通讯端 |
| **删除** | tts/server、tts/client、stt/server、stt/client、sensevoice | 601 | 移动端无 TTS/STT（已拍板） |
| **删除** | adapters/cli（rich 交互壳） | 153 | 安卓无终端形态；核心调试走测试与日志 |
| **删除** | core/render（IM 防刷屏后处理） | 123 | App 内聊天无 IM 风控约束 |
| **删除** | core/attention/*（视觉注意力六件套） | 614 | C1 拍板砍。MediaProjection 每次授权+常驻通知，与拟真质感冲突；手机端她的感知=时间+日程+presence |
| **删除** | tools/search（SearXNG 联网搜索） | 916 | C5 拍板砍。SearXNG 是 PC 本地 Docker，远程搜索 API 全付费。**已知降级**：手机端她对未知实体改为「不知道」而非查证（配合不编造红线） |
| **不打包** | R5 执行后端：tools/hermes_bridge、dsh_bridge、workorder、task_session、artifact_generation | 1144 | C2 拍板。代码保留在仓库（PC 端仍用），APK 不 import；将来若需要「她让 PC 干活」，另做 HTTP 瘦客户端打局域网 Hermes API，不在本期 |
| **瘦身** | pet_server | 1163→~200 | C3 拍板「事件流可以留着」。Chaquopy 同进程后 WS 帧协议退役；保留 speak/状态/主动消息的事件流语义，改成进程内事件总线 + Kotlin UI 直调 |
| **保留换实现** | core/presence | 67 | C6 拍板。接口不变（L0 在场信号），安卓实现=亮屏/解锁/前台状态由 Kotlin 侧推给 Python |
| **保留** | core/stickers（表情包记忆库） | 552 | C4 拍板。发送端接 App 图片消息；库与情绪匹配管道不动 |

## APK 打包排除清单（gradle 层执行）

```
adapters/qq.py, qq.py, qq_advisor.py, qq_proactive.py
tts/**, stt/**
adapters/cli.py
core/render.py
core/attention/**
tools/search.py
tools/hermes_bridge.py, tools/dsh_bridge.py, core/workorder.py, core/task_session.py, core/artifact_generation.py
```

排除生效的行为级验收：APK 构建脚本 assert 打包文件集合 ∩ 上表 = ∅；核心 import 图（agent 入口递归）不触任何被排除模块——被 agent 窄引用的（presence、capability、interrupt）保留接口、剔除桌面实现。

### 实施注记（2026-08-29 依赖核对）

- `agent.py:28` 顶层 `from ..tools.search import ...`（EvidencePack/SearchTrigger/SemanticLocator 等 7 符号）是无条件导入，search 被排除后 agent 直接 ImportError。修法=挪进 `search_enabled` 分支内做 lazy import（安卓 config 里 search.enabled=false，永不触包）；若将来安卓要恢复搜索再拆数据层。`presence` 仅被 pet_server 函数内引用，agent 干净，C6 换实现零连坐。
- C3 的「瘦身」实为改写：事件流语义从 pet_server 抽成进程内总线，WS 协议、端口、子进程管理全退役。

## 依赖包袱同步出局

- `sentence-transformers` + torch（embedding 已切 dashscope 远程 API）
- `aiocqhttp`（QQ 退役）
- websockets（pet_server WS 退役后复查）
- 交付面：Python 核心 + httpx + sqlite-vec（探针已验证 android-x86_64 直载）+ APScheduler + Pillow

## 遗留未决（不阻塞动工）

- 聊天客户端 UI：深入设计后续单独开题（用户拍板）
- Termux 形态的后台存活结论作废，真机 Doze 验证排进 APK 第一里程碑
- 锁屏小组件/通知栏（主动对话评审中「通道不存在」的那批设计）随安卓化复活，归聊天 UI 开题一起谈
