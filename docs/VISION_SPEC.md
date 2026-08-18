# 视觉注意力模块专项设计（docs/VISION_SPEC.md）

> 依据：DESIGN.md 4.6（视觉注意力）、M4_SPEC 1.x（视觉调度器）；2026-08-19 用户拍板：视觉注意力独立成模块，现有「全屏像素差触发 + LLM 全屏观察」机制不足以模拟真人，需按仿生注意力模型重构。
> 状态：设计稿（未动工）。当前 `veranima/core/vision.py` 为过渡实现（M4_SPEC 1.x 落地），本规范是其替代/升级目标。

---

## 1. 现状与真人差距

| 维度 | 当前实现（M4_SPEC 1.x / vision.py） | 真人视觉注意 |
|------|--------------------------------------|--------------|
| 感知范围 | 全屏均匀（像素差占比 > 5%） | 中央凹高分辨率 + 周边低分辨率（对运动/变化敏感） |
| 注意转移 | 无「注视点」概念，只有全屏变化触发 | 扫视（saccade）跳跃式转移，150-300ms 一跳 |
| 停留 | 无停留概念（每次 tick 对比全屏） | 注视（fixation）200-600ms，兴趣内容停留更久 |
| 显著度 | 无排序，全屏等权 | 显著度地图：运动/对比/人脸/文本密度排序，只注意最高者 |
| 习惯化 | 无（同一区域反复触发） | 新奇偏好：60s 无新内容 → 注意衰减 |
| 观察内容 | LLM 理解全屏（一次一张全图） | 只看注视区域（周边只做粗略运动检测） |
| 当前焦点 | focus.tag（变化时更新） | 持续追踪「用户在干什么」+ 注意历史 |

结论：现有实现是「屏幕变化报警器」，不是「注意力」。重构目标 = 在保留截屏+LLM 观察能力的前提下，把**看哪里、看多久、什么时候转移**这三个决策按仿生模型重做。

---

## 2. 仿生注意力模型（核心设计）

### 2.1 三层感知（对应人眼结构）

```
┌─────────────────────────────────────────────┐
│  全局快照（每 1-2s，降采样 1/8，~60KB）       │ ← 周边视野：运动/显著度检测
│  → 显著度地图（saliency map）                 │
├─────────────────────────────────────────────┤
│  注视区域（fovea，当前注视点 ± 15% 屏幕）      │ ← 中央凹：细节内容，LLM 只读这里
│  仅当注视点转移/内容变化时截高清               │
├─────────────────────────────────────────────┤
│  焦点窗口（frontmost window 元数据）          │ ← 任务上下文：窗口标题/进程名
│  Win32 GetForegroundWindow，变化即事件        │
└─────────────────────────────────────────────┘
```

- **全局层**：低成本（降采样灰度图），只算显著度，不调 LLM。
- **注视层**：LLM 只理解注视区域（区域裁剪 → base64 → 多模态），token 成本可控。
- **窗口层**：窗口切换是最高优先级事件（用户任务切换），不依赖像素。

### 2.2 显著度地图（Saliency Map）

对全局快照计算 3 个显著通道（OpenCV/Pillow，无模型）：

| 通道 | 计算 | 对应人眼 |
|------|------|----------|
| 运动显著 | 相邻快照帧差（>阈值像素聚类，取最大连通域中心） | 周边视野对运动敏感 |
| 对比显著 | 灰度梯度幅度（Sobel）聚类 | 视觉突显（弹出窗口/亮色） |
| 结构显著 | 文本密度（高频边缘密度高 = 文字/UI 区域） | 阅读/操作区域 |

显著度 = 三通道加权（运动 0.5 / 对比 0.3 / 结构 0.2，可配）。输出：显著区域列表
`[{center: (x,y), radius, score, source}]`，按 score 降序。

### 2.3 注意调度状态机（扫视-注视循环）

```
       ┌──────────────────────────────────────────────┐
       │                                              │
       ▼                                              │
  ┌─────────┐  注视超时(200-600ms)/显著度更高区域出现  │
  │ 注视中   │ ───────────────────────────────────────┘
  │fixation │   → 扫视目标 = 最高显著度区域（非当前）
  └────┬────┘
       │ 当前区域内容变化（帧差局部 > 阈值）
       ▼
  ┌─────────┐  该区域 60s 无新内容（习惯化）
  │ 持续注视 │ ──────────────────────→ 回全局扫描
  │(内容驱动)│
  └─────────┘
```

状态参数（仿真人节奏，全部可配）：

| 参数 | 默认 | 说明 |
|------|------|------|
| `fixation_min_ms` | 300 | 注视最短停留（防止抖动跳变） |
| `fixation_max_ms` | 3000 | 无新内容时最长停留（超过转扫视） |
| `saccade_interval_ms` | 800 | 扫视间最小间隔（跳转频率上限） |
| `orient_delay_ms` | 300 | 显著变化出现到朝向反应（扫视）的延迟 |
| `habituation_sec` | 60 | 区域无新内容后的注意衰减阈值 |
| `global_scan_sec` | 5 | 全局快照间隔（周边视野刷新率） |
| `observe_cooldown_sec` | 60 | LLM 观察最小间隔（token 成本上限） |

### 2.4 习惯化与新奇偏好

- 每个显著区域记录 `{last_change_ts}`；`now - last_change_ts > habituation_sec` → 该区域从显著度地图降权（×0.3）。
- 全新区域出现（未在历史中）→ 显著度 ×2（新奇偏好）。
- 效果：用户持续打字时注意力留在文本区（局部变化持续更新 last_change）；弹窗出现时立即吸引（新区域）。

### 2.5 观察（LLM 理解）时机

仅两类情况触发 LLM：
1. **注视点转移到新区域**：裁剪注视区域 → 理解「这里是什么」（tag/observe）
2. **焦点窗口切换**：新窗口标题/进程 → 理解「用户在做什么」（含窗口标题提示词）

两者都受 `observe_cooldown_sec`（60s）限制。注视区域内小变化（打字）**不触发** LLM——只刷新区域 `last_change_ts`（习惯化计时）。

---

## 3. 模块架构

独立包：`src/veranima/core/attention/`（替代 `vision.py`，vision.py 保留为过渡兼容，迁移完成删除）

```
attention/
├── __init__.py          # 对外导出 AttentionScheduler
├── scheduler.py         # 扫视-注视状态机（2.3），对外唯一入口
├── perception.py        # 截屏（ImageGrab）、降采样、区域裁剪、窗口元数据（Win32）
├── saliency.py          # 三通道显著度地图（2.2）
├── observer.py          # LLM 区域理解（调用 llm.observe_image，区域裁剪 + 提示词）
└── config.py            # 参数（2.3 表），从 config.yaml `attention:` 段加载
```

接口：

```python
class AttentionScheduler:
    def __init__(self, llm, config: dict): ...
    def tick(self) -> list[AttentionEvent]: ...   # 主循环调用（每 ~500ms），返回事件

@dataclass
class AttentionEvent:
    kind: str            # "fixation_shift" | "window_switch" | "observation" | "habituation"
    region: tuple        # 归一化 (x0,y0,x1,y1) 或全屏
    tag: str = ""        # 观察得到的分类（游戏/办公/浏览器/…）
    note: str = ""       # 观察描述
```

事件消费者（pet_server `_visual_loop` 替换）：
- `observation` → 注入 episodic 记忆（沿用现有 `[屏幕观察]` 格式）+ 联想主动发起（`agent.proactive_from_visual`）
- `fixation_shift` → 更新 `focus`（当前注视区域 tag 可参与对话上下文）
- `window_switch` → 立即观察新窗口 + 高优先级主动发起候选

调度循环（pet_server 内）：
```
async def _attention_loop():
    att = AttentionScheduler(agent.llm, cfg.get("attention", {}))
    while True:
        for ev in await asyncio.to_thread(att.tick):
            ... # 分发事件（记忆注入/主动发起/speak）
        await asyncio.sleep(0.5)   # tick 内部自管理全局快照节奏
```

### 3.1 与现有系统集成点

| 集成点 | 方式 |
|--------|------|
| 记忆注入 | 观察事件 → episodic `[屏幕观察] {note}（{tag}）`（沿用，含注视区域描述） |
| 主动发起 | `window_switch` 事件 → proactive（「你打开 X 了？」）；`observation` tag 匹配记忆 → 联想 |
| 对话上下文 | 当前 `focus`（注视区域 tag + 最近观察）可注入 system prompt（后续迭代，默认关） |
| QQ 通道互斥 | 沿用 M4_SPEC 1.3：QQ 活跃 30min 内全局快照降频（5s → 30s），不调 LLM |
| 打断 | 注意力不打断对话（只产生事件，由 pet_server 决定是否 speak） |

---

## 4. 配置（config.yaml `attention:` 段）

```yaml
attention:
  enabled: true
  global_scan_sec: 5          # 全局快照间隔
  fixation_min_ms: 300        # 注视最短停留
  fixation_max_ms: 3000       # 注视最长停留
  saccade_interval_ms: 800    # 扫视最小间隔
  orient_delay_ms: 300        # 朝向反应延迟
  habituation_sec: 60         # 习惯化阈值
  observe_cooldown_sec: 60    # LLM 观察冷却
  saliency_weights: [0.5, 0.3, 0.2]   # 运动/对比/结构
  fovea_ratio: 0.15           # 注视区域半径（屏幕短边比例）
  observe_on_window_switch: true
```

---

## 5. 验收标准（可实测）

1. **任务切换响应**：用户切换前台窗口（浏览器↔编辑器）→ ≤3s 内产生 `window_switch` 事件 + 观察注入（日志可见 `visual: 窗口切换 → tag=办公 note=…`）
2. **持续工作不打扰**：用户在文本编辑器持续打字 5 分钟 → 无 LLM 观察触发（打字只是局部帧差，不满足观察时机），仅注视区域停留（日志 `visual: 持续注视`）
3. **显著变化朝向**：屏幕出现弹窗/视频切换 → ≤1s 产生 `fixation_shift` 朝向反应（日志 `visual: 朝向反应 → 显著区域`）
4. **习惯化**：同一静态画面持续 60s → 不再产生观察/扫视事件（日志 `visual: 习惯化`）
5. **成本上限**：连续运行 1 小时，LLM 观察调用 ≤ 60 次（cooldown 60s 硬限）
6. **注意力轨迹可查**：`logs/core.log` 每个事件带区域坐标与时间戳，可还原「看了哪里→跳到哪」
7. **回归**：现有 253 测试全过；`vision.py` 迁移后删除（无残留引用）

---

## 6. 实现里程碑

| 阶段 | 内容 | 验证 |
|------|------|------|
| V1 基础 | attention 包骨架 + 全局快照 + 显著度（运动/对比） + 注视状态机 + 事件日志 | 验收 1/3/6 |
| V2 完整 | 窗口元数据（Win32）+ 习惯化 + 观察时机 + 记忆注入接入（替换 pet_server 现有调用） | 验收 2/4 |
| V3 收尾 | 对话上下文注入（可选开关）+ vision.py 删除 + 配置文档 | 验收 5/7 |

V1 即替换现有 `_visual_loop` 的调用点（pet_server 只改循环体），`vision.py` 保留至 V3。
