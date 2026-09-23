from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from agent.env_utils import get_env


TASK_KIND_DEFAULT_LIMITS: dict[str, tuple[int, int]] = {
    "qa": (60, 600),
    "inspect": (30, 300),
    "sync": (40, 300),
    "analysis": (120, 900),
    "planning": (120, 900),
    "coding": (300, 1800),
}

DEFAULT_MAX_TOOL_CALLS = 240
DEFAULT_MAX_SECONDS = 1800


class AgentRunLimitExceeded(RuntimeError):
    """Agent 本轮运行超过保护上限。"""


@dataclass
class AgentRunLimits:
    """Agent Loop 的轻量保护阈值。

    open-swe 的模型循环主要由 `ModelCallLimitMiddleware` 保护。LX_AICODING
    运行在 FastAPI 内，这里只保留更可靠的时间和工具调用上限。不要用 raw event
    里的 `message-start` 统计模型调用次数，因为 DeepAgents 的流式片段、子 Agent
    和中间 assistant message 都可能产生该事件，容易误判。
    """

    max_tool_calls: int
    max_seconds: int
    task_kind: str = "default"

    @classmethod
    def from_env(cls, task_kind: str | None = None) -> "AgentRunLimits":
        """按任务类型读取运行保护阈值。

        配置优先级：
        1. `AGENT_<TASK_KIND>_MAX_TOOL_CALLS` / `AGENT_<TASK_KIND>_MAX_SECONDS`
        2. `AGENT_MAX_TOOL_CALLS` / `AGENT_MAX_SECONDS`
        3. 代码内置的任务类型默认值

        这样复杂 coding 任务可以使用更高阈值，同时 qa、inspect、sync 仍保持保守。
        """

        normalized_kind = (task_kind or "default").lower()
        default_tool_calls, default_seconds = TASK_KIND_DEFAULT_LIMITS.get(
            normalized_kind,
            (DEFAULT_MAX_TOOL_CALLS, DEFAULT_MAX_SECONDS),
        )
        env_prefix = f"AGENT_{normalized_kind.upper()}_"
        return cls(
            max_tool_calls=int(
                get_env(
                    f"{env_prefix}MAX_TOOL_CALLS",
                    get_env("AGENT_MAX_TOOL_CALLS", str(default_tool_calls)),
                )
            ),
            max_seconds=int(
                get_env(
                    f"{env_prefix}MAX_SECONDS",
                    get_env("AGENT_MAX_SECONDS", str(default_seconds)),
                )
            ),
            task_kind=normalized_kind,
        )


class AgentRunLimitTracker:
    """根据官方事件流统计本轮 Agent 运行规模。"""

    def __init__(self, limits: AgentRunLimits | None = None, task_kind: str | None = None):
        self.limits = limits or AgentRunLimits.from_env(task_kind)
        self.started_at = time.monotonic()
        self.tool_calls = 0

    def observe_event(self, event: Any) -> None:
        """读取一个 raw event 并检查是否超过限制。"""

        self._check_time()
        if not isinstance(event, dict):
            return

        method = event.get("method")
        if method in {"tool_calls", "tools"}:
            payload = self._first_payload(event)
            event_name = payload.get("event") if isinstance(payload, dict) else None
            if method == "tool_calls" or event_name == "tool-started":
                self.tool_calls += 1
                self._check_tool_calls()

    def _first_payload(self, event: dict[str, Any]) -> Any:
        params = event.get("params")
        if not isinstance(params, dict):
            return None
        data = params.get("data")
        if isinstance(data, tuple):
            return data[0] if data else None
        if isinstance(data, list):
            return data[0] if data else None
        return data

    def _check_time(self) -> None:
        elapsed = time.monotonic() - self.started_at
        if elapsed > self.limits.max_seconds:
            raise AgentRunLimitExceeded(
                f"本轮运行已超过 {self.limits.max_seconds} 秒保护上限"
                f"（任务类型：{self.limits.task_kind}）"
            )

    def _check_tool_calls(self) -> None:
        if self.tool_calls > self.limits.max_tool_calls:
            raise AgentRunLimitExceeded(
                f"本轮工具调用已超过 {self.limits.max_tool_calls} 次保护上限"
                f"（任务类型：{self.limits.task_kind}）。"
                "复杂开发任务可以提高 AGENT_CODING_MAX_TOOL_CALLS，"
                "或让智能体分阶段继续完成 Git 提交、推送和 Pull Request。"
            )
