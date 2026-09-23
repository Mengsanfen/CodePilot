from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent.backends.local_shell import LocalShellBackend
from agent.backends.permissions import WorkspacePermissionError
from agent.core.events import record_event
from agent.tools.gitee_api import mask_token
from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger("agent.run.middleware.tool_error")


def tool_error_result(
    error: Exception,
    *,
    tool_name: str,
    backend: LocalShellBackend,
) -> dict[str, Any]:
    """把未捕获异常转换为 Agent 可继续理解的中文结构化结果。

    没有这个 middleware 时，一个工具抛出普通异常会直接让 LangGraph 工具节点失败，
    页面上只能看到整轮任务 failed。课程项目更需要把错误变成“反馈”，让模型下一步
    可以调整路径、命令或仓库参数继续处理。
    """

    error_text = mask_token(str(error))
    if isinstance(error, WorkspacePermissionError):
        hint = "只能访问工作区内路径。请使用 '/projects' 或 '/projects/仓库名' 这样的虚拟路径。"
    elif isinstance(error, IsADirectoryError):
        hint = "当前路径是目录。请先调用 ls（旧工具名 list_files）查看目录内容，再对具体文件调用 read_file 或 write_file。"
    elif isinstance(error, FileNotFoundError):
        hint = "文件不存在。请先调用 ls 确认真实路径，再继续操作。"
    elif isinstance(error, PermissionError):
        hint = "文件系统拒绝访问。请确认路径不是目录、没有被其他程序占用，并改用工作区内具体文件路径。"
    elif isinstance(error, TimeoutError):
        hint = "操作超时。请缩小任务范围，或先用更小的命令检查项目状态。"
    else:
        hint = "工具执行失败。请根据 error 字段调整参数，必要时先读取目录、文件或 Git 状态再重试。"

    return {
        "ok": False,
        "tool": tool_name,
        "error_type": error.__class__.__name__,
        "error": error_text,
        "workspace": str(backend.workspace.root),
        "hint": hint,
    }


def _get_tool_call_id(request: ToolCallRequest) -> str | None:
    """兼容读取 LangGraph tool_call id。"""

    if isinstance(request.tool_call, dict):
        value = request.tool_call.get("id")
        return value if isinstance(value, str) else None
    return None


def _get_tool_name(request: ToolCallRequest) -> str:
    """从 ToolCallRequest 中读取工具名称。"""

    if isinstance(request.tool_call, dict):
        name = request.tool_call.get("name")
        return name if isinstance(name, str) and name else "unknown_tool"
    return "unknown_tool"


def _get_tool_args(request: ToolCallRequest) -> dict[str, Any]:
    """从 ToolCallRequest 中读取工具参数。"""

    if isinstance(request.tool_call, dict):
        args = request.tool_call.get("args", {})
        return args if isinstance(args, dict) else {}
    return {}


def _get_thread_id(request: ToolCallRequest) -> str | None:
    """从 runtime config 中读取 thread_id。"""

    config = getattr(getattr(request, "runtime", None), "config", None)
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _error_tool_message(
    error: Exception,
    *,
    request: ToolCallRequest,
    tool_name: str,
    backend: LocalShellBackend,
) -> ToolMessage:
    """把工具异常转换为标准 ToolMessage。"""

    return ToolMessage(
        content=json.dumps(
            tool_error_result(error, tool_name=tool_name, backend=backend),
            ensure_ascii=False,
        ),
        tool_call_id=_get_tool_call_id(request),
        status="error",
    )


def _record_original_tool_error(
    thread_id: str,
    *,
    tool_name: str,
    kwargs: dict[str, Any],
    error: Exception,
) -> None:
    """把工具失败回写到原来的步骤 key，避免页面上出现长时间“运行中”。

    各工具在真正执行前会先写一条 in_progress 事件。异常被 middleware 捕获后，
    如果只新增 `tool-error:*` 事件，原步骤会停留在运行中。这里按工具名和参数
    复原原事件 key，把它更新为 error 状态。
    """

    error_detail = mask_token(str(error))
    mapping = {
        "ls": ("list:{path}", "查看目录", "search"),
        "read_file": ("read:{path}", "读取文件", "read"),
        "write_file": ("write:{path}", "写入文件", "edit"),
        "edit_file": ("write:{file_path}", "修改文件", "edit"),
        "execute": ("cmd:{command}", "执行命令", "execute"),
        "list_files": ("list:{path}", "查看目录", "search"),
        "run_command": ("cmd:{command}:{cwd}", "执行命令", "execute"),
        "open_gitee_pull_request": ("gitee:pr", "创建或复用 Pull Request", "fetch"),
    }
    item = mapping.get(tool_name)
    if not item:
        return

    key_template, title, kind = item
    try:
        key = key_template.format(**{"cwd": ".", **kwargs})
    except Exception:
        key = f"tool:{tool_name}"
    record_event(
        thread_id,
        key,
        title,
        kind=kind,
        status="error",
        detail=json.dumps(
            {
                "tool": tool_name,
                "args": {name: mask_token(str(value)) for name, value in kwargs.items()},
                "error": error_detail,
            },
            ensure_ascii=False,
        ),
    )


class ToolErrorMiddleware(AgentMiddleware):
    """open-swe 风格的工具异常处理中间件。

    它捕获所有工具调用异常，并返回 `status="error"` 的 ToolMessage。
    这样模型能把错误当作观察结果继续推理，不会因为一个路径错误或权限错误
    直接终止整轮 FastAPI 后台任务。
    """

    state_schema = AgentState

    def __init__(self, *, backend: LocalShellBackend) -> None:
        super().__init__()
        self.backend = backend

    def _record_error(self, request: ToolCallRequest, tool_name: str, error: Exception) -> None:
        thread_id = _get_thread_id(request)
        if not thread_id:
            return
        kwargs = _get_tool_args(request)
        _record_original_tool_error(thread_id, tool_name=tool_name, kwargs=kwargs, error=error)
        record_event(
            thread_id,
            f"tool-error:{tool_name}",
            f"工具失败：{tool_name}",
            kind="other",
            status="error",
            detail=json.dumps(
                {
                    "tool": tool_name,
                    "error_type": error.__class__.__name__,
                    "error": mask_token(str(error)),
                },
                ensure_ascii=False,
            ),
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        tool_name = _get_tool_name(request)
        try:
            return handler(request)
        except Exception as exc:  # noqa: BLE001 - middleware 的职责就是兜底所有工具异常
            logger.warning("工具执行异常已被中间件捕获：tool=%s error=%s", tool_name, mask_token(str(exc)))
            logger.debug("工具异常调试栈：tool=%s", tool_name, exc_info=True)
            self._record_error(request, tool_name, exc)
            return _error_tool_message(exc, request=request, tool_name=tool_name, backend=self.backend)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_name = _get_tool_name(request)
        try:
            return await handler(request)
        except Exception as exc:  # noqa: BLE001 - middleware 的职责就是兜底所有工具异常
            logger.warning("工具执行异常已被中间件捕获：tool=%s error=%s", tool_name, mask_token(str(exc)))
            logger.debug("工具异常调试栈：tool=%s", tool_name, exc_info=True)
            self._record_error(request, tool_name, exc)
            return _error_tool_message(exc, request=request, tool_name=tool_name, backend=self.backend)
