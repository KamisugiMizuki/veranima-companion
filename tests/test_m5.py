"""M5 测试：需求翻译层（工单/触发/追问）+ dsh 薄壳。"""
import json

import pytest

from veranima.core.workorder import (
    build_workorder,
    clarification_question,
    extract_format_pref,
    extract_source_path,
    is_task_request,
)
from veranima.tools.dsh_bridge import dsh_available


# ---------- M5 2.3 触发条件 ----------

def test_task_triggers():
    """任务意图 → 转交；闲聊 → 不转交。"""
    assert is_task_request("帮我整理一下桌面") is True
    assert is_task_request("把这份文档转成PDF") is True
    assert is_task_request("今天心情不错") is False  # 闲聊
    assert is_task_request("在吗？") is False


# ---------- M5 2.1 意图补完 ----------

def test_workorder_llm_completion(monkeypatch):
    """LLM 版补全：模型 JSON → 工单字段。"""
    from veranima.core.workorder import build_workorder_llm

    class FakeLLM:
        def chat(self, messages, **kw):
            return '{"goal": "整理周报", "context": "D:/docs/周报.xlsx", "constraints": {"format": ["pdf"]}, "fallback": "找不到就算了", "needs_clarification": []}'

    wo = build_workorder_llm(FakeLLM(), "帮我整理周报")
    assert wo.goal == "整理周报"
    assert wo.constraints.get("format") == ["pdf"]
    assert wo.needs_clarification == []


def test_workorder_llm_fallback(monkeypatch):
    """LLM 输出非法 → 降级规则版（不报错）。"""
    from veranima.core.workorder import build_workorder_llm

    class BadLLM:
        def chat(self, messages, **kw):
            raise RuntimeError("model down")

    wo = build_workorder_llm(BadLLM(), "把 D:/docs/周报.xlsx 转成PDF")
    assert wo.task_type == "文档处理"
    assert wo.goal  # 降级仍产出工单


def test_workorder_llm_fence_stripped():
    """LLM 返回 markdown fence 包裹 JSON 也能解析。"""
    from veranima.core.workorder import build_workorder_llm

    class FenceLLM:
        def chat(self, messages, **kw):
            return '```json\n{"goal": "x", "needs_clarification": []}\n```'

    wo = build_workorder_llm(FenceLLM(), "随便")
    assert wo.goal == "x"

def test_workorder_basic():
    """基础工单：goal/task_id/task_type/fallback。"""
    wo = build_workorder("帮我整理桌面那个Excel表格")
    assert wo.goal == "帮我整理桌面那个Excel表格"
    assert len(wo.task_id) == 8
    assert wo.task_type == "文档处理"
    assert "不要编造" in wo.fallback


def test_workorder_extract_source_and_format():
    """来源路径 + 目标格式提取。"""
    wo = build_workorder("把 D:/docs/周报.xlsx 转成PDF")
    assert "D:/docs/周报.xlsx" in wo.context
    assert wo.constraints.get("format") == ["pdf"]


def test_workorder_needs_clarification():
    """信息不足 → 缺失维度标记 + 追问建议。"""
    wo = build_workorder("帮我转成")
    assert "目标格式" in wo.needs_clarification  # 缺格式（无具体格式）
    assert "来源路径" not in wo.needs_clarification  # 未提具体文件不标来源
    q = clarification_question(wo)
    assert q  # 有追问


def test_extract_helpers():
    assert extract_source_path('处理 "C:\\Users\\a\\report.docx"') == "C:\\Users\\a\\report.docx"
    assert extract_format_pref("转成 PDF 看看") == "pdf"


# ---------- M5 3.3 薄壳 ----------

def test_dsh_bridge_available():
    """dsh 已安装到 dsh/（真实环境验证）。"""
    # dsh/ 目录存在且 bin.js 在
    assert dsh_available() is True


def test_dsh_bridge_workorder_json_roundtrip():
    """工单 JSON 可序列化且字段完整（TASK_TRANSFER_PROTOCOL）。"""
    wo = build_workorder("把 D:/docs/周报.xlsx 转成PPT")
    data = json.loads(wo.to_json())
    assert data["task_id"]
    assert data["goal"]
    assert "format" in data["constraints"]
    assert data["fallback"]
