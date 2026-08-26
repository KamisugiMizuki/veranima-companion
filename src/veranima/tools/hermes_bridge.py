"""Hermes Agent 执行桥（HERMES_AGENT_INTEGRATION_SPEC §4.2/§5 阶段 1-2）。

veranima 侧唯一接触 Hermes /v1/runs 的文件。职责只有协议转换：
WorkOrder → run 提交 → 状态轮询 → stop/approve → 结构化 TaskRun。

安全边界（SPEC §15.4/15.5）：
- 写操作只允许 workspace_root 内；run 结束后审计 changed_files 越界即 violation
- 审批只接受 once/session/always/deny 四种 choice，按 run 提交；409 视为已过期
- /v1/runs 状态是 API Server 进程内短期保留：GET 404 时非终态本地记录标 orphaned

本模块不做任何 prompt 工程兜底：Hermes 离线/未配置时 fail-closed，
陪伴主链不受影响（SPEC §10 回滚策略）。
"""
from __future__ import annotations

import json
import re
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "timed_out", "orphaned"})
APPROVAL_CHOICES = ("once", "session", "always", "deny")


@dataclass
class TaskRun:
    """结构化任务结果（SPEC §4.3）。changed_files/test_summary 是派生字段：
    只能来自任务输出的四段结构解析，Hermes 不原生保证；无法验证时保持空值。"""

    task_id: str
    run_id: str
    status: str  # queued/running/waiting_for_approval/succeeded/failed/cancelled/timed_out/orphaned
    raw_status: str = ""
    output: str = ""
    error: str = ""
    changed_files: tuple[str, ...] = ()
    test_summary: str = ""
    warnings: tuple[str, ...] = ()
    approval_request: dict | None = None
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["changed_files"] = list(self.changed_files)
        d["warnings"] = list(self.warnings)
        return d


class HermesBridgeError(RuntimeError):
    pass


class HermesExecutionBridge:
    """/v1/runs 协议转换。config 来自 tasks.hermes 段。"""

    def __init__(self, config: dict):
        cfg = config or {}
        self.base_url = str(cfg.get("base_url", "")).rstrip("/")
        # multiplex 模式：/p/<profile>/v1/...；独立端口模式直接 base_url + /v1/...
        self.profile = str(cfg.get("profile", "") or "")
        self.multiplex = bool(cfg.get("multiplex_profiles", False)) and bool(self.profile)
        prefix = f"/p/{self.profile}" if self.multiplex else ""
        self.api_prefix = f"{self.base_url}{prefix}/v1"
        # key 从环境变量读取，绝不写入 config/git（SPEC §4.5）
        env_key = f"VERANIMA_HERMES_KEY_{self.profile.upper().replace('-', '_')}" if self.profile else "VERANIMA_HERMES_KEY"
        self.api_key = os.environ.get(env_key) or os.environ.get("VERANIMA_HERMES_KEY") or ""
        self.workspace_root = str(Path(cfg.get("workspace_root") or Path.cwd()).resolve())
        self.timeout = int(cfg.get("timeout_seconds", 600))
        self.output_max_chars = int(cfg.get("output_max_chars", 12000))

    # ---------- HTTP 基础 ----------

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 timeout: float | None = None) -> tuple[int, dict]:
        import httpx

        url = f"{self.api_prefix}{path}"
        with httpx.Client(timeout=timeout or 10.0) as client:
            resp = client.request(method, url, json=body, headers=self._headers())
        try:
            data = resp.json()
        except Exception:
            data = {}
        return resp.status_code, data

    # ---------- health / submit / status / stop / approve ----------

    def health(self) -> tuple[bool, str]:
        """区分离线 / 鉴权失败 / 正常（SPEC 阶段 1 第 2 步）。"""
        if not self.base_url:
            return False, "not_configured"
        try:
            code, _ = self._request("GET", "/../health".replace("/..", ""), timeout=5.0)
            # /health 与 /v1 同级：单独拼一次
        except Exception as e:
            return False, f"offline: {e}"
        try:
            import httpx
            url = f"{self.base_url}{'/p/' + self.profile if self.multiplex else ''}/health"
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(url, headers=self._headers())
            if resp.status_code == 200:
                return True, "ok"
            if resp.status_code in (401, 403):
                return False, "auth_failed"
            return False, f"http_{resp.status_code}"
        except Exception as e:
            return False, f"offline: {e}"

    def submit(self, workorder: dict, *, require_confirmation: bool = True) -> TaskRun:
        """WorkOrder JSON → 自包含任务 prompt → POST /v1/runs。

        任务输出强制四段结构（做了什么/改动清单/验证结果/未验证项），
        供 report 解析与 workspace 审计使用。
        """
        if not self.base_url:
            raise HermesBridgeError("hermes 未配置（tasks.hermes.base_url 为空），任务拒绝执行")
        wo = workorder if isinstance(workorder, dict) else json.loads(workorder)
        task_id = str(wo.get("task_id") or f"wo_{uuid.uuid4().hex[:12]}")
        goal = str(wo.get("goal") or "").strip()
        constraints = wo.get("constraints") or {}
        lines = [
            "你是一个受控工作执行器。请完成以下任务并严格遵守边界。",
            f"任务 ID：{task_id}",
            f"目标：{goal}",
        ]
        if wo.get("context"):
            lines.append(f"补充上下文：{wo['context']}")
        if wo.get("source"):
            lines.append(f"来源路径（只读参考）：{wo['source']}")
        if constraints:
            lines.append(f"约束：{json.dumps(constraints, ensure_ascii=False)}")
        if wo.get("fallback"):
            lines.append(f"异常预案：{wo['fallback']}")
        lines += [
            f"写操作边界：只允许写/改/删 {self.workspace_root} 内的文件；"
            "该目录外允许读，禁止任何形式的修改。",
            "最终回复必须包含以下四段，缺一不可：",
            "1. 做了什么：<简述>",
            "2. 改动文件清单：<逐行列出相对/绝对路径；无改动写「无」>",
            "3. 验证结果：<实际执行的验证与结果>",
            "4. 未验证项：<未能验证的内容>",
        ]
        code, data = self._request(
            "POST", "/runs",
            body={"input": "\n".join(lines), "session_id": f"veranima-{task_id}"},
            timeout=min(30.0, float(self.timeout)),
        )
        if code == 202 and isinstance(data, dict) and data.get("run_id"):
            return TaskRun(task_id=task_id, run_id=str(data["run_id"]),
                           status="queued", raw_status="started")
        if code == 401 or code == 403:
            raise HermesBridgeError(f"hermes 鉴权失败（HTTP {code}）——检查 VERANIMA_HERMES_KEY*")
        if code == 429:
            raise HermesBridgeError("hermes 并发已满（HTTP 429），稍后重试")
        raise HermesBridgeError(f"/v1/runs 提交失败（HTTP {code}）：{str(data)[:300]}")

    # ---------- 状态归一化 ----------

    @staticmethod
    def normalize_status(raw_status: str) -> str:
        mapping = {
            "queued": "queued", "started": "running", "running": "running",
            "waiting_for_approval": "waiting_for_approval",
            "completed": "succeeded", "failed": "failed", "cancelled": "cancelled",
            "stopping": "stopping",
        }
        return mapping.get(raw_status, raw_status or "")

    @staticmethod
    def parse_report(output: str) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
        """从四段结构解析 (changed_files, test_summary, warnings)。缺失段落记 warning。

        段落格式：「N. 标签：内容」，内容可跨多行直到下一段。
        """
        if not output:
            return (), "", ("empty_output",)
        # 找出四个段落标记行（行首 N. 开头）
        marks: list[tuple[int, int]] = []  # (section_idx, line_no)
        for ln_no, line in enumerate(output.splitlines()):
            stripped = line.strip()
            for idx in range(1, 5):
                if stripped.startswith(f"{idx}.") or stripped.startswith(f"{idx}、"):
                    marks.append((idx, ln_no))
                    break
        sections: dict[int, str] = {}
        lines = output.splitlines()
        for pos, (idx, ln) in enumerate(marks):
            end = marks[pos + 1][1] if pos + 1 < len(marks) else len(lines)
            body = "\n".join(lines[ln:end])
            body = body.split(".", 1)[-1].split("、", 1)[-1]
            sections[idx] = body.strip()
        files: tuple[str, ...] = ()
        test_summary = ""
        warnings: list[str] = []
        # 标签剥离只作用于段落首行（路径里可能含 ":"，不能对整段做冒号切分）
        def _strip_label(section: str) -> str:
            lines_ = section.splitlines()
            if not lines_:
                return ""
            head = re.split(r"[：:]", lines_[0], maxsplit=1)[-1]
            rest = lines_[1:]
            return "\n".join([head] + rest).strip()
        sec2_body = _strip_label(sections.get(2) or "")
        if 2 not in sections:
            warnings.append("missing_section_changed_files")
        elif not sec2_body or sec2_body.strip() in ("无", "none"):
            files = ()
        else:
            files = tuple(
                re.sub(r"^[-•\s]+", "", ln).strip()
                for ln in sec2_body.splitlines()
                if ln.strip()
            )
        if 3 not in sections:
            warnings.append("missing_section_test_summary")
        else:
            test_summary = _strip_label(sections[3])
        if 4 not in sections:
            warnings.append("missing_section_unverified")
        if 1 not in sections:
            warnings.append("missing_section_what_done")
        return files, test_summary, tuple(warnings)

    def audit_workspace(self, run: TaskRun) -> TaskRun:
        """SPEC §15.4：changed_files 全部位于 workspace_root 内，否则 violation。"""
        bad = [
            p for p in run.changed_files
            if p.strip() and p.strip() not in ("无",)
            and not (Path(p).resolve().is_relative_to(Path(self.workspace_root)))
        ]
        if bad:
            run.warnings = run.warnings + ("workspace_violation",)
        return run

    def from_status_payload(self, task_id: str, payload: dict,
                            prior: TaskRun | None = None) -> TaskRun:
        raw = str(payload.get("status") or "")
        status = self.normalize_status(raw)
        out = str(payload.get("output") or "")
        if len(out) > self.output_max_chars:
            out = out[: self.output_max_chars] + "…（截断）"
        run = TaskRun(
            task_id=task_id,
            run_id=str(payload.get("run_id") or (prior.run_id if prior else "")),
            status=status if status != "stopping" else (prior.status if prior and prior.status in TERMINAL_STATUSES else "running"),
            raw_status=raw,
            output=out,
            error=str(payload.get("error") or ""),
            approval_request=payload.get("approval_request") if isinstance(payload.get("approval_request"), dict) else None,
        )
        if status == "succeeded":
            run.changed_files, run.test_summary, warn = self.parse_report(out)
            run.warnings = warn
            run = self.audit_workspace(run)
        return run

    def status(self, task_id: str, run_id: str, prior: TaskRun | None = None) -> TaskRun:
        code, data = self._request("GET", f"/runs/{run_id}")
        if code == 200 and isinstance(data, dict):
            return self.from_status_payload(task_id, data, prior=prior)
        if code == 404:
            # SPEC：终态短期保留后过期 / gateway 重启 → 不能猜终态
            if prior is not None and prior.status in TERMINAL_STATUSES:
                return prior
            return TaskRun(task_id=task_id, run_id=run_id, status="orphaned",
                           raw_status="not_found",
                           error="run 状态已过期或 hermes 已重启，无法确认终态")
        if code in (401, 403):
            raise HermesBridgeError(f"hermes 鉴权失败（HTTP {code}）")
        raise HermesBridgeError(f"状态查询失败（HTTP {code}）：{str(data)[:200]}")

    def wait_terminal(self, task_id: str, run_id: str, *, poll_seconds: float = 2.0,
                      cancel_event=None, on_run=None) -> TaskRun:
        """轮询到终态。本地超时先请求 stop；远端确认 cancelled 才记 timed_out，
        否则 orphaned（SPEC 阶段 1 第 6 步）。on_run 回调用于持久化中间态。"""
        deadline = time.monotonic() + self.timeout
        prior: TaskRun | None = None
        while True:
            run = self.status(task_id, run_id, prior=prior)
            if on_run:
                on_run(run)
            prior = run
            if run.status in TERMINAL_STATUSES:
                return run
            if cancel_event is not None and cancel_event.is_set():
                self.stop(task_id, run_id)
                continue  # 轮询直到远端真正 cancelled
            if time.monotonic() > deadline:
                stopped = self.stop(task_id, run_id)
                if stopped.status == "cancelled":
                    stopped.raw_status = "local_timeout_confirmed"
                    return stopped
                return TaskRun(task_id=task_id, run_id=run_id, status="orphaned",
                               error="本地超时且无法确认远端终态",
                               started_at=stopped.started_at)
            time.sleep(poll_seconds)

    def stop(self, task_id: str, run_id: str) -> TaskRun:
        """POST stop 后继续轮询到终态（stopping ≠ 已停止）。"""
        code, _ = self._request("POST", f"/runs/{run_id}/stop", timeout=10.0)
        if code == 404:
            return TaskRun(task_id=task_id, run_id=run_id, status="orphaned",
                           raw_status="stop_not_found")
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            run = self.status(task_id, run_id)
            if run.status in TERMINAL_STATUSES:
                return run
            time.sleep(1.0)
        return TaskRun(task_id=task_id, run_id=run_id, status="orphaned",
                       error="stop 后等待终态超时")

    def approve(self, task_id: str, run_id: str, choice: str) -> TaskRun:
        """SPEC §15.5：choice ∈ once/session/always/deny，按 run 提交。
        409 approval_not_pending = 已过期，fail-closed 返回当前真实状态。"""
        choice = str(choice).strip().lower()
        if choice not in APPROVAL_CHOICES:
            raise ValueError(f"非法审批选项：{choice!r}（允许 {APPROVAL_CHOICES}）")
        code, data = self._request(
            "POST", f"/runs/{run_id}/approval",
            body={"choice": choice}, timeout=10.0,
        )
        if code == 409:
            logger.info("approval no longer pending (run=%s): %s", run_id, str(data)[:120])
            return self.status(task_id, run_id)
        if code in (401, 403):
            raise HermesBridgeError(f"hermes 鉴权失败（HTTP {code}）")
        if code >= 400:
            raise HermesBridgeError(f"审批提交失败（HTTP {code}）：{str(data)[:200]}")
        return self.status(task_id, run_id)

    # ---------- 审批关键词硬匹配（SPEC §15.5，纯规则，不走 LLM） ----------

    NEGATION_WORDS = ("不要", "别", "不想", "拒绝")

    @classmethod
    def match_approval_choice(cls, text: str, *, strict: bool = True) -> str | None:
        """严格模式：trim 后整句等于关键词。宽松模式：恰好一个关键词命中；
        否定语境视为歧义不执行。零/多命中返回 None。"""
        t = (text or "").strip()
        hits = [c for c in APPROVAL_CHOICES if c in t]
        if strict:
            return t if t in APPROVAL_CHOICES else None
        if len(hits) != 1:
            return None
        hit = hits[0]
        before = t[: t.index(hit)]
        if any(neg in before for neg in cls.NEGATION_WORDS):
            return None
        return hit


def load_bridge_config(tasks_cfg: dict) -> dict:
    """从 tasks 配置段提取 hermes 子段（缺省 enabled=false）。"""
    t = tasks_cfg or {}
    if not t.get("enabled"):
        return {"enabled": False}
    return {"enabled": True, **(t.get("hermes") or {})}
