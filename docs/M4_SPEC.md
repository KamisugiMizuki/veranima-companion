# M4 专项细化：视觉注意力调度器 + 表情标签驱动（docs/M4_SPEC.md）

> 依据：DESIGN.md 4.6（视觉注意力）、4.8（表情标签驱动）、M3_SPEC（进程架构/通道互斥）
> M4 范围：视觉感知管线（L0-L3）+ 表情标签驱动落地（模型输出 portrait → 形象渲染）
>
> **实现状态（2026-08-19）**：**第 1 章（视觉注意力调度器）已被 VISION_SPEC 替代**——独立模块 `core/attention/`（AttentionScheduler：三层感知/三通道显著度/扫视-注视状态机/鼠标焦点/习惯化/分层冷却），原 `vision.py` 已删除；第 2 章（表情标签驱动）保持有效（已实现：portrait/tone 结构化输出 + 立绘映射）。

---

## 1. 视觉注意力调度器（4.6 细化）

### 1.1 兴趣锚点（数据结构）

```python
# 锚点定义：region 为屏幕坐标（0-1 归一化），tag 供联想式主动发起匹配
Anchor = {"name": str, "region": (x0, y0, x1, y1), "tag": str, "priority": int}
# 例：
# {"name": "游戏血条", "region": (0.30, 0.90, 0.70, 0.97), "tag": "游戏", "priority": 3}
# {"name": "窗口标题栏", "region": (0.05, 0.0, 0.95, 0.04), "tag": "应用", "priority": 1}
```

- 锚点默认提供 1~3 个：窗口标题栏（应用识别）+ 用户自定义（游戏血条等）
- 锚点命中 → 更新「当前屏幕焦点」状态（`focus: {tag, since}`），供联想式主动发起（4.7 联想机制）与认知冲突（4.7 冲突机制）使用

### 1.2 三态切换（判定规则）

| 状态 | 进入条件 | 行为 | 退出条件 |
| --- | --- | --- | --- |
| 稳定期 | 启动默认 / 触发期后 10s 无显著变化 | 30s 低频小截图（仅锚点区域，~50KB/张） | 锚点区域像素差异 > 阈值（如 5%） |
| 触发期 | 稳定期检测到显著变化 | 高频（5s）+ 扩窗（锚点 2 倍区域）截图 | 连续 3 次无显著变化 → 回稳定期 |
| 游离期 | 触发期 2min 无变化 / 用户 30min 无输入（在场检测 off） | 低分辨率全屏扫描（每 2min 一次） | 检测到变化 / 用户在场恢复 |

- 像素差异判定：锚点区域灰度直方图（64 bins）卡方距离 > 0.2 → 显著变化
- **三态只决定截屏频率，不决定 token 消耗**——截图后先过 L2 本地筛选，只有筛选通过才进 L3 大模型

### 1.3 分级管线 L0-L3（各层输入输出）

| 层 | 能力 | 输入 | 输出 | 成本 |
| --- | --- | --- | --- | --- |
| L0 系统 API | 零 token 文本状态 | Windows 全局钩子事件 | `presence: on/off`、`foreground_app: 名称`、`input_idle: 秒` | 0 |
| L1 事件驱动截图 | 低频画面事实 | L0 判定 foreground 变化 / 锚点变化 | 触发截图时机（交给 L2） | 0（仅调度） |
| L2 本地小模型筛选 | 画面显著性判断 | 截图（压缩后） | `interesting: bool` + 一句话描述（可选） | 本地（0 token） |
| L3 大模型理解 | 语义理解 | L2 判定 interesting 的截图 | 结构化观察（`{"observe": "在打游戏，血条掉了", "tag": "游戏"}`） | 远程 token |

- **节流规则**：L3 调用间隔 ≥60s（防烧 token）；L2 判定 interesting 率 > 30% 时提高 L2 阈值（自适应）
- **通道互斥**（M3_SPEC 2.2）：QQ 通道活跃 30min 内 → 视觉进入游离期最低频（只跑 L0）

### 1.4 观察注入（与主动发起衔接）

- L3 观察写入 `visual_observations` 环形缓冲（内存，最近 10 条）
- 联想式主动发起（4.7）：`focus.tag` × 事件记忆层模糊匹配 → 命中则生成主动消息
- 认知冲突（4.7）：`focus.tag` 与用户行为画像冲突 → 触发
- **观察只进内存，不落记忆库**（视觉内容是瞬时上下文，不是长期记忆）——避免记忆污染

---

## 2. 表情标签驱动（4.8 落地细化）

### 2.1 输出格式与现有 LLM 客户端兼容

现有 `chat()` 返回纯文本。M4 改造：
- `handle(channel="tts")` 时请求结构化输出（prompt 要求 JSON），LLM 响应解析：
  `{"segments":[{"text":"回复内容","tone":"中性","portrait":"开心脸红"}]}`
- **解析失败降级**：JSON 解析失败 / 缺 text → 整段当纯文本（portrait 回退 idle），不重试、不报错
- IM 通道（channel="im"）保持纯文本（不强制 JSON——文字通道没有立绘需求，避免破坏 IM 表达质量）
- 实现位置：`core/agent.py` 生成后处理（`_extract_segments(reply) -> (text, tone, portrait)`），TTS 渲染器消费

### 2.2 表情词表（角色卡 avatar.expressions）

```json
"avatar": {
  "idle": "assets/pet/idle.png",
  "speaking": "assets/pet/speaking.png",
  "thinking": "assets/pet/thinking.png",
  "sleeping": "assets/pet/sleeping.png",
  "expressions": {
    "站立待机": "assets/pet/stand.png",
    "开心脸红": "assets/pet/happy.png",
    "疑惑": "assets/pet/puzzled.png",
    "难过": "assets/pet/sad.png",
    "惊讶": "assets/pet/surprised.png"
  }
}
```

- 四态（idle/speaking/thinking/sleeping）保留为**渲染基础态**（无表情标签时的回退链）
- expressions 是**情绪覆盖层**：portrait 命中 expressions → 用表情图；未命中 → 用四态
- prompt 注入：`build_system_prompt` 把 expressions 的标签列表注入（「可用表情：站立待机/开心脸红/疑惑/难过/惊讶」），模型只能选列表内标签

### 2.3 立绘说明.txt 批量映射（格式）

`portraits/立绘说明.txt`，每行「文件前缀 标签」（空格分隔，前缀匹配文件名开头）：

```
stand 站立待机
happy 开心脸红
puzzled 疑惑
sad 难过
surprised 惊讶
```

- 匹配：文件名以「前缀」开头 → 绑定「标签」；`import_character`（4.11）导入角色包时自动应用
- 与 sakura 同款：批量管理多张立绘，不用逐个手配
- 实现位置：`core/character_archive.py` 扩展 `apply_portrait_description(char_dir) -> dict[标签, 路径]`，写入 avatar.expressions

### 2.4 渲染链（TTS 通道）

```
agent.handle(channel=tts) → 结构化回复(text, tone, portrait)
  → 壳 speak 消息 {text, portrait}
  → renderer: expressions[portrait] ?? 四态回退链
```

- `tone` 暂不做映射（GPT-SoVITS 无语气参数，见 M3_SPEC 3.2；等接入后评估）
- 气泡文本 = `text`；形象图 = `expressions[portrait]` 或回退

---

## 3. M4 验收清单

| 项 | 验收 |
| --- | --- |
| 1.2 三态 | 稳定→触发（锚点变化）→游离（2min 无变化）切换正确；像素差异判定有测试 |
| 1.3 L0-L3 | L0 在场/前台识别；L2 筛选后 L3 调用间隔 ≥60s；QQ 活跃时视觉降频 |
| 1.4 观察 | 视觉观察进环形缓冲不落记忆库；联想/冲突机制可消费 focus.tag |
| 2.1 结构化 | channel=tts 返回 JSON 解析成功；解析失败降级纯文本不报错 |
| 2.2 词表 | expressions 注入 prompt；portrait 命中用表情图，未命中回退四态 |
| 2.3 立绘说明 | 立绘说明.txt 批量映射生效；导入角色包自动应用 |
| 2.4 渲染链 | 桌宠收到 speak 带 portrait → 显示对应表情图 |

---

## 4. 与 DESIGN.md 的关系

本文件是 DESIGN.md 4.6/4.8 的 M4 专项细化；M4 完成后核心结论回写 DESIGN.md。
