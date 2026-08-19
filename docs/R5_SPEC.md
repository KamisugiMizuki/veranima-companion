# R5 专项：外部任务协作

> 目标：把明确的桌面任务交给独立工具，同时保持 veranima 是一个人，不是自动化平台。
> 现有复用：`core/workorder.py`, `tools/dsh_bridge.py`。
> dsh 参考：当前目录 `dsh/` 独立 npm 环境；不把 dsh API/会话混入 veranima。

## 1. 触发契约

默认不自动转交。命中以下规则才进入候选：

- 明确动作词 + 可验证产物：“帮我把 X 转成 PDF”。
- 用户说“交给桌面助手/执行这个任务”。
- 超出角色能力边界后，角色先说明可转交并等待确认。

“我想聊聊周报”不得触发。规则入口复用 `is_task_request()`，LLM 只补全字段，不改变是否需要确认。

## 2. WorkOrder

现有 `WorkOrder` 扩展字段：

```python
task_id, goal, task_type, source, constraints,
deadline, fallback, cancellation_policy,
needs_clarification, status
```

JSON 发送前校验：goal/source 长度、路径存在性、危险操作确认、deadline 范围、task_type 白名单。LLM 不能猜绝对路径；缺路径必须追问。

## 3. 生命周期

```text
draft → needs_clarification → confirmed → running
→ succeeded | failed | cancelled | timed_out
```

状态持有在 dsh bridge 的任务记录，不写陪伴记忆。聊天窗口能显示状态，但只显示角色化摘要和可操作按钮。

## 4. dsh bridge

`run_dsh_task(workorder, cancel_event)`：

- 独立 cwd、env、超时和 stdout/stderr。
- argv 列表调用，不拼 shell 字符串。
- 输出截断到配置上限，原始日志单独落盘。
- 非零退出、超时、取消都返回结构化结果。
- veranima 核心对话线程不能被 `subprocess.run` 阻塞；使用已有线程/异步边界。

## 5. 配置

```yaml
tasks:
  enabled: false
  require_confirmation: true
  timeout_seconds: 600
  output_max_chars: 12000
  allowed_types: [文档处理, 信息检索, 系统操作, 自动化流程]
```

## 6. 测试/验收

低成本模型先测 `workorder.py` 纯函数，再测 bridge fake subprocess；真实 dsh 冒烟单独执行，不放普通 pytest。覆盖闲聊不转交、缺路径追问、用户确认、取消、超时、失败、结果不写记忆。

暂缓：并发队列、Web UI、多工具编排、自动记忆结果。
