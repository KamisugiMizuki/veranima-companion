"""R5 需求翻译层（R5_SPEC 2：WorkOrder）：模糊指令 → 结构化任务工单。

- 意图补完五维：目标澄清 / 来源路径 / 用户偏好注入 / 优先级约束 / 异常预案
- 信息不足主动追问（附带猜测建议）
- TASK_TRANSFER_PROTOCOL JSON 工单，发送后必须给「已安排」反馈
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

# 可转交任务类型清单（R5_SPEC 2.3 触发条件；config.yaml task_types 可配置）
DEFAULT_TASK_TYPES = ("文档处理", "信息检索", "系统操作", "自动化流程")

# 触发关键词（能力匹配层判定：命中 → 任务管道）
_TASK_TRIGGERS = (
    "帮我", "帮我做", "帮我弄", "帮我写", "帮我整理", "帮我转", "帮我查", "帮我下载",
    "生成", "整理一下", "转成", "转换成", "汇总", "做成", "处理一下",
)


@dataclass
class WorkOrder:
    """TASK_TRANSFER_PROTOCOL 工单（R5_SPEC 2 JSON 格式）。"""

    goal: str                       # 目标澄清：可验证的结果
    context: str = ""               # 补充上下文（来源路径/用户偏好）
    source: str = ""                # 来源路径（R5_SPEC 2.2；LLM 不能猜绝对路径）
    constraints: dict = field(default_factory=dict)  # 优先级约束（deadline/format 等）
    fallback: str = ""              # 异常预案
    cancellation_policy: str = "confirm"  # 取消策略（confirm=需用户确认）
    task_id: str = ""               # 自动生成
    task_type: str = ""             # 任务类型（能力匹配层）
    needs_clarification: list[str] = field(default_factory=list)  # 缺失维度
    status: str = "draft"           # 生命周期（R5_SPEC 3）：draft/needs_clarification/
                                    #   confirmed/running/succeeded/failed/cancelled/timed_out

    @property
    def deadline(self) -> str:
        return str(self.constraints.get("deadline") or "")

    def to_json(self) -> str:
        """工单序列化（TASK_TRANSFER_PROTOCOL JSON）。"""
        d = asdict(self)
        return json.dumps(d, ensure_ascii=False, indent=2)


# 危险操作（R5_SPEC 2 校验：必须用户确认）
DANGEROUS_ACTIONS = ("删除", "格式化", "覆盖", "清空", "重置", "卸载", "rm ")


def validate_workorder(wo: WorkOrder, *, task_types: tuple = DEFAULT_TASK_TYPES,
                       confirm_dangerous: bool = True) -> list[str]:
    """工单发送前程序校验（R5_SPEC 2：JSON 发送前校验）。

    返回问题列表，空列表 = 通过。LLM 不能猜绝对路径；缺路径必须追问。
    """
    issues: list[str] = []
    if not wo.goal or len(wo.goal) > 500:
        issues.append("goal 为空或超过 500 字")
    if len(wo.source) > 500:
        issues.append("source 超过 500 字")
    if wo.source:
        p = Path(wo.source)
        if not p.exists():
            issues.append(f"来源路径不存在: {wo.source}")
    if wo.task_type and wo.task_type not in task_types:
        issues.append(f"task_type 不在白名单: {wo.task_type}")
    dl = wo.deadline
    if dl:
        try:
            import datetime
            dl_dt = datetime.datetime.fromisoformat(dl)
            if dl_dt < datetime.datetime.now():
                issues.append("deadline 早于当前时间")
        except ValueError:
            issues.append(f"deadline 格式非法: {dl}")
    if confirm_dangerous and any(d in wo.goal for d in DANGEROUS_ACTIONS):
        issues.append("危险操作需要用户确认")
    if "来源路径" in wo.needs_clarification and not wo.source:
        issues.append("缺来源路径（必须追问，LLM 不得猜路径）")
    return issues


def is_task_request(user_text: str) -> bool:
    """能力匹配层判定（R5_SPEC 2.3）：任务意图 → 转交任务管道。

    规则：明确任务前缀（帮我/整理/转成等）优先——闲聊词仅在无任务前缀时生效。
    """
    has_task_prefix = any(t in user_text for t in _TASK_TRIGGERS)
    if has_task_prefix:
        return True
    return False


def classify_task_type(user_text: str, task_types: tuple = DEFAULT_TASK_TYPES) -> str:
    """粗分类到任务类型清单（低配关键词；精确分类由 LLM 补全阶段做）。"""
    if any(k in user_text for k in ("文档", "文件", "word", "excel", "ppt", "pdf", "表格", "周报")):
        return "文档处理"
    if any(k in user_text for k in ("查", "搜索", "找", "信息", "资料")):
        return "信息检索"
    if any(k in user_text for k in ("安装", "打开", "启动", "关闭", "删除", "复制", "移动", "重启")):
        return "系统操作"
    if any(k in user_text for k in ("自动", "每天", "定时", "批量", "脚本", "流水线")):
        return "自动化流程"
    return task_types[0] if task_types else ""


def extract_source_path(user_text: str) -> str:
    """来源路径提取（低配：引号/盘符/常见路径模式）。"""
    m = re.search(r"[A-Za-z]:[\\/][^\s，。！？\"']+", user_text)
    if m:
        return m.group(0)
    m = re.search(r"[\"']([^\"']+\.(?:xlsx?|docx?|pptx?|pdf|csv|md|txt))[\"']", user_text)
    if m:
        return m.group(1)
    return ""


def extract_format_pref(user_text: str) -> str:
    """格式偏好提取（低配：'转成 X' / '做成 X'）。"""
    m = re.search(r"(?:转成|转换成|做成|导出为)\s*([A-Za-z0-9]+)", user_text)
    return m.group(1).lower() if m else ""


def build_workorder_llm(llm, user_text: str, *, task_type: str = "") -> WorkOrder:
    """LLM 版意图补全（R5_SPEC 2.1）：模型直接补全五维，替换规则提取。

    LLM 不可用/输出异常 → 降级规则版 build_workorder（调用方无感）。
    """
    try:
        prompt = (
            "你是任务意图补全器。把用户的模糊指令补全为结构化 JSON，字段：\n"
            '{"goal": "可验证的目标", "context": "补充上下文（来源路径/用户偏好）", '
            '"constraints": {"deadline": "或空", "format": ["目标格式数组"]}, '
            '"fallback": "异常预案", "needs_clarification": ["缺失维度数组"]}\n'
            "缺失维度只能从：来源路径/目标格式/任务类型/优先级约束。信息足够则 needs_clarification 为空。\n"
            f"用户指令：{user_text}"
        )
        resp = llm.chat([{"role": "user", "content": prompt}], max_tokens=400)
        import json as _json
        data = _json.loads(resp.strip().lstrip("```json").rstrip("```").strip())
        wo = WorkOrder(
            goal=str(data.get("goal") or user_text),
            context=str(data.get("context") or ""),
            constraints=data.get("constraints") or {},
            fallback=str(data.get("fallback") or "找不到文件时返回错误说明，不要编造"),
            task_id=f"{uuid.uuid4().hex[:8]}",
            task_type=task_type or classify_task_type(user_text),
            needs_clarification=[str(x) for x in (data.get("needs_clarification") or [])],
        )
        return wo
    except Exception:
        logger = logging.getLogger(__name__)
        logger.warning("LLM 意图补全失败，降级规则版")
        return build_workorder(user_text)


def build_workorder(user_text: str, *, username: str = "", task_types: tuple = DEFAULT_TASK_TYPES) -> WorkOrder:
    """模糊指令 → 工单（R5_SPEC 2.1 意图补完五维，规则版）。

    LLM 版见 build_workorder_llm（可用时替换本函数）。
    """
    wo = WorkOrder(
        goal=user_text.strip(),
        context="",
        constraints={},
        fallback="找不到文件时返回错误说明，不要编造",
        task_id=f"{uuid.uuid4().hex[:8]}",
        task_type=classify_task_type(user_text, task_types),
    )
    src = extract_source_path(user_text)
    fmt = extract_format_pref(user_text)
    if src:
        wo.context += f"来源路径：{src}。"
    if fmt:
        wo.constraints["format"] = [fmt]
        wo.context += f"目标格式：{fmt}。"
    if username:
        wo.context += f"用户：{username}。"

    # 缺失维度标记（R5_SPEC 2.1：信息不足主动追问）
    if not src and any(k in user_text for k in ("这个", "那个", "那份", "桌面", "文件")):
        wo.needs_clarification.append("来源路径")
    if not fmt and any(k in user_text for k in ("转成", "转换成", "做成", "导出")):
        wo.needs_clarification.append("目标格式")
    if not wo.task_type:
        wo.needs_clarification.append("任务类型")
    return wo


def clarification_question(wo: WorkOrder) -> str:
    """针对缺失维度生成追问（附带猜测建议，R5_SPEC 2.1）。"""
    if not wo.needs_clarification:
        return ""
    parts = []
    if "来源路径" in wo.needs_clarification:
        parts.append("那个文件在哪？桌面上还是某个文件夹里？（发路径给我）")
    if "目标格式" in wo.needs_clarification:
        parts.append("想要什么格式？（比如 PDF / PPT / Word）")
    if "任务类型" in wo.needs_clarification:
        parts.append("具体要做什么？（整理 / 转格式 / 查资料 / 自动化）")
    return "；".join(parts)
