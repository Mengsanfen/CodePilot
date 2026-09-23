from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.core import settings
from agent.core.background import run_task_safely
from agent.core.runtime import delete_task, get_task, initialize_task_record, list_tasks
from agent.core.task_intent import classify_task_kind
from agent.env_utils import get_env

dashboard_router = APIRouter(prefix="/dashboard/api")


class DashboardThreadCreateRequest(BaseModel):
    prompt: str
    images: list[dict[str, Any]] | None = None
    repo: str | None = None
    repo_explicitly_none: bool = False
    model_id: str | None = None
    effort: str | None = None


class DashboardThreadMessageRequest(BaseModel):
    content: str
    images: list[dict[str, Any]] | None = None
    model_id: str | None = None
    effort: str | None = None


def _timestamp_ms(value: str | None) -> int:
    """把 SQLite 中的 ISO 时间转换成前端使用的毫秒时间戳。"""

    if not value:
        return int(datetime.now().timestamp() * 1000)
    normalized = value.replace("Z", "+00:00")
    return int(datetime.fromisoformat(normalized).timestamp() * 1000)


def _status_for_frontend(status: str | None) -> str:
    """把课程后端状态映射成 open-swe 前端 AgentStatus。"""

    if status in {"running", "pushed", "pr_created"}:
        return "running"
    if status in {"completed", "awaiting_approval"}:
        return "finished"
    if status == "failed":
        return "error"
    return "idle"


def _repo_full_name(thread: dict[str, Any]) -> str:
    owner = thread.get("repo_owner") or ""
    repo = thread.get("repo_name") or ""
    if owner and repo:
        return f"{owner}/{repo}"
    return thread.get("repo_url") or ""


def _pr_payload(thread: dict[str, Any]) -> dict[str, Any] | None:
    pr_url = thread.get("pr_url")
    if not pr_url:
        return None
    number = 0
    try:
        number = int(str(pr_url).rstrip("/").split("/")[-1])
    except ValueError:
        number = 0
    return {
        "number": number,
        "title": thread.get("title") or "LX-AICODING Pull Request",
        "state": "open",
        "headRef": thread.get("branch_name") or "",
        "baseRef": "master",
        "url": pr_url,
    }


def _parse_event_detail(detail: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """把 run_events.detail 转成前端工具块的 input/output。

    新版本工具事件会把路径、文件列表等信息保存为 JSON；旧事件仍可能是普通字符串。
    这里同时兼容两种格式，避免升级后历史会话显示异常。
    """

    if not detail:
        return None, None
    try:
        parsed = json.loads(detail)
    except (TypeError, ValueError):
        return None, detail
    if not isinstance(parsed, dict):
        return None, detail

    input_payload: dict[str, Any] = {}
    if parsed.get("path") is not None:
        input_payload["path"] = parsed["path"]
    if parsed.get("command") is not None:
        input_payload["command"] = parsed["command"]
    if parsed.get("cwd") is not None:
        input_payload["cwd"] = parsed["cwd"]

    files = parsed.get("files")
    if isinstance(files, list):
        preview = "\n".join(f"- {item}" for item in files[:80])
        if len(files) > 80:
            preview += f"\n... 还有 {len(files) - 80} 项"
        output = preview or "目录为空"
    elif parsed.get("chars") is not None:
        output = f"{parsed['chars']} 个字符"
    elif parsed.get("written_path") is not None:
        output = str(parsed["written_path"])
    elif parsed.get("stdout") is not None or parsed.get("stderr") is not None or parsed.get("exit_code") is not None:
        parts = []
        if parsed.get("exit_code") is not None:
            parts.append(f"exit_code={parsed['exit_code']}")
        if parsed.get("stdout"):
            parts.append(str(parsed["stdout"]))
        if parsed.get("stderr"):
            parts.append(str(parsed["stderr"]))
        output = "\n".join(parts)
    elif parsed.get("error") is not None:
        output = str(parsed["error"])
    else:
        output = None

    return input_payload or None, output


def _stream_message_text(event: dict[str, Any]) -> str:
    """从 stream:message 事件中读取累计模型正文。"""

    detail = event.get("detail")
    if not detail:
        return ""
    try:
        parsed = json.loads(detail)
    except (TypeError, ValueError):
        return str(detail).strip()
    if isinstance(parsed, dict):
        return str(parsed.get("text") or "").strip()
    return str(detail).strip()


def _is_stream_message_event(event: dict[str, Any]) -> bool:
    """判断是否是模型正文流式输出事件。

    record_event 会把业务 key 前面加上 thread_id，所以真实 id 通常是
    `<thread_id>:stream:message`；测试和旧数据中也可能直接是 `stream:message`。
    """

    event_id = str(event.get("id") or "")
    return event_id == "stream:message" or event_id.endswith(":stream:message")


def _is_stream_assistant_event(event: dict[str, Any]) -> bool:
    """判断是否是单条 AI/assistant 输出事件。"""

    event_id = str(event.get("id") or "")
    return (
        event_id.startswith("stream:assistant:")
        or ":stream:assistant:" in event_id
    )


def _looks_like_english_process_text(text: str) -> bool:
    """判断文本是否像英文自然语言过程描述。

    代码、路径、命令、工具名允许保留英文；这里仅拦截大段英文句子，
    例如模型流式输出的 “I'll start by...” 或 “Let me check...”。
    """

    stripped = text.strip()
    if not stripped:
        return False
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", stripped))
    english_words = re.findall(r"\b[A-Za-z][A-Za-z']+\b", stripped)
    english_process_markers = (
        "i will",
        "i'll",
        "let me",
        "i can",
        "i need",
        "it seems",
        "there are",
        "checking",
        "running",
        "understand the workspace",
    )
    lowered = stripped.lower()
    if any(marker in lowered for marker in english_process_markers) and len(english_words) >= 8:
        return True
    return chinese_chars == 0 and len(english_words) >= 12


def _has_markdown_structure(text: str) -> bool:
    """判断文本是否已有 Markdown 结构，避免二次排版破坏方案或总结。"""

    markdown_patterns = (
        r"```",
        r"^\s{0,3}#{1,6}\s+",
        r"^\s*[-*]\s+",
        r"^\s*\d+[.、]\s+",
        r"^\s*\|.+\|",
    )
    return any(re.search(pattern, text, flags=re.MULTILINE) for pattern in markdown_patterns)


def _extract_visible_chinese_or_markdown(text: str) -> str:
    """从混合模型输出里提取用户真正需要看的中文正文。

    DeepAgents 流式输出有时会先出现英文过程描述，后面才开始输出中文技术方案。
    如果直接对整段文本做英文过程判断，会把后续中文方案也一起隐藏。这里按行保守提取：
    - 跳过明显英文过程描述行。
    - 保留包含中文的行。
    - 保留 Markdown 结构行，避免标题、列表、表格被误删。
    - 保留代码围栏中的内容。
    """

    lines = text.splitlines()
    if len(lines) <= 1:
        if re.search(r"[\u4e00-\u9fff]", text) or not _looks_like_english_process_text(text):
            return text.strip()
        return ""

    kept: list[str] = []
    in_code_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            kept.append(line)
            continue
        if in_code_block:
            kept.append(line)
            continue
        if not stripped:
            if kept and kept[-1].strip():
                kept.append(line)
            continue
        has_chinese = bool(re.search(r"[\u4e00-\u9fff]", stripped))
        has_markdown = _has_markdown_structure(stripped)
        if has_chinese or has_markdown:
            kept.append(line)
            continue
        if _looks_like_english_process_text(stripped):
            continue
        english_words = re.findall(r"\b[A-Za-z][A-Za-z']+\b", stripped)
        if len(english_words) <= 6:
            kept.append(line)
    return "\n".join(kept).strip()


def _user_visible_text(text: str) -> str:
    """规整用户可见文本，避免英文过程描述污染主输出。"""

    visible = _extract_visible_chinese_or_markdown(text)
    if not visible and _looks_like_english_process_text(text):
        return "智能体正在处理任务，已隐藏非中文过程描述。"
    return _format_visible_chinese_text(visible or text)


def _user_visible_stream_text(text: str) -> str:
    """规整流式正文，避免英文过程描述压住后续中文技术方案。"""

    visible = _extract_visible_chinese_or_markdown(text)
    if not visible and _looks_like_english_process_text(text):
        return "正在生成内容..."
    return _format_visible_chinese_text(visible or text)


def _format_visible_chinese_text(text: str) -> str:
    """把连续中文长段落整理成更容易阅读的段落。

    模型流式输出经常是一整段累计文本。前端会保留换行，但不会自动按中文
    句号分段，所以这里在展示层做轻量处理：
    - 已经有 Markdown、列表、代码块或明显换行的文本保持原样。
    - 只处理包含中文、长度较长、且没有段落结构的自然语言。
    - 每两句合成一段，避免每句话都换行导致内容过碎。
    """

    stripped = text.strip()
    if not stripped:
        return stripped
    if "\n" in stripped or _has_markdown_structure(stripped):
        return stripped
    if len(stripped) < 80 or not re.search(r"[\u4e00-\u9fff]", stripped):
        return stripped

    sentences = [
        item.strip()
        for item in re.findall(r".+?[。！？；]|.+$", stripped)
        if item.strip()
    ]
    if len(sentences) <= 2:
        return stripped

    paragraphs: list[str] = []
    for index in range(0, len(sentences), 2):
        paragraphs.append("".join(sentences[index:index + 2]))
    return "\n\n".join(paragraphs)


def _is_final_status(status: str | None) -> bool:
    return status in {"completed", "failed", "awaiting_approval"}


def _should_hide_event(event: dict[str, Any]) -> bool:
    """隐藏内部工具事件，避免前端输出无关噪音。"""

    title = event.get("title") or ""
    event_id = event.get("id") or ""
    if event.get("kind") == "todo":
        return True
    if _is_stream_assistant_event(event):
        return True
    if "write_todos" in title or "write_todos" in event_id:
        return True
    if title.startswith("调用工具："):
        return True
    return False


def _normalize_todo_status(status: Any) -> str:
    text = str(status or "pending")
    if text in {"pending", "in_progress", "completed"}:
        return text
    return "pending"


def _todos_from_events(run_events: list[dict[str, Any]], *, final_status: str | None = None) -> list[dict[str, str]]:
    """读取最新一次 write_todos 事件，转换成前端 TodoChunk。"""

    force_completed = final_status in {"completed", "awaiting_approval"}
    for event in reversed(run_events):
        if event.get("kind") != "todo":
            continue
        detail = event.get("detail")
        if not detail:
            continue
        try:
            parsed = json.loads(detail)
        except (TypeError, ValueError):
            continue
        raw_todos = parsed.get("todos") if isinstance(parsed, dict) else None
        if not isinstance(raw_todos, list):
            continue
        todos: list[dict[str, str]] = []
        for item in raw_todos:
            if isinstance(item, dict):
                content = str(item.get("content") or "").strip()
                status = _normalize_todo_status(item.get("status"))
            else:
                content = str(item).strip()
                status = "pending"
            if content:
                if force_completed and status in {"pending", "in_progress"}:
                    status = "completed"
                todos.append({"content": content, "status": status})
        if todos:
            return todos
    return []


def _fallback_plan_text(thread: dict[str, Any]) -> str:
    """没有 write_todos 时，根据任务类型给前端一个短 fallback。"""

    task_kind = classify_task_kind(thread.get("user_prompt") or thread.get("title") or "")
    if task_kind == "coding":
        steps = ["读取仓库上下文", "完成代码修改", "执行必要验证", "提交分支并创建或复用 PR"]
    elif task_kind == "analysis":
        steps = ["准备并读取仓库", "梳理目录结构", "识别关键模块", "归纳结论和建议"]
    elif task_kind == "planning":
        steps = ["确认目标和约束", "阅读相关模块", "设计实施步骤", "列出风险和验证方式"]
    elif task_kind == "inspect":
        steps = ["确认工作区", "列出 projects 目录", "归纳可见项目"]
    else:
        steps = ["定位相关上下文", "读取证据文件", "组织中文答案"]
    return "任务计划\n" + "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))


def _plan_text(thread: dict[str, Any]) -> str:
    """生成类似 Codex 的固定任务计划。"""

    return "\n".join(
        [
            "任务计划",
            "1. 分析需求并读取仓库上下文",
            "2. 定位相关模块并完成代码修改",
            "3. 执行必要的检查或测试",
            "4. 提交分支并创建或复用 Pull Request",
        ]
    )


def _final_summary_text(thread: dict[str, Any], run_events: list[dict[str, Any]]) -> str:
    """根据业务事件生成稳定的最终总结，不依赖模型自由输出。"""

    latest_run = thread.get("latest_run") or {}
    status = thread.get("latest_run_status")
    lines = ["最终总结", f"- 任务状态：{status}"]
    task_kind = classify_task_kind(thread.get("user_prompt") or thread.get("title") or "")
    lines.append(f"- 任务类型：{task_kind}")

    if thread.get("branch_name"):
        lines.append(f"- 分支：{thread['branch_name']}")
    if thread.get("pr_url"):
        lines.append(f"- PR：{thread['pr_url']}")
    if latest_run.get("error"):
        lines.append(f"- 错误：{latest_run['error']}")
    changed_files: list[str] = []
    commands: list[str] = []
    test_results: list[str] = []
    for event in run_events:
        detail = event.get("detail")
        if not detail:
            continue
        try:
            parsed = json.loads(detail)
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        path = parsed.get("path") or parsed.get("written_path")
        if event.get("kind") == "edit" and path:
            changed_files.append(str(path))
        command = parsed.get("command")
        if command:
            commands.append(str(command))
            lowered = str(command).lower()
            if "pytest" in lowered or "test" in lowered:
                exit_code = parsed.get("exit_code")
                test_results.append(f"{command}：exit_code={exit_code}")

    if changed_files:
        lines.append("- 编辑清单：")
        for path in sorted(set(changed_files))[:20]:
            lines.append(f"  - {path}")
    if not changed_files:
        lines.append("- 代码修改：未执行")
    if not thread.get("pr_url"):
        lines.append("- Pull Request：未创建")

    if commands:
        lines.append("- 执行命令：")
        for command in commands[:20]:
            lines.append(f"  - {command}")
    if test_results:
        lines.append("- 测试结果：")
        for result in test_results[:10]:
            lines.append(f"  - {result}")

    return "\n".join(lines)


def _message_payload(thread: dict[str, Any]) -> list[dict[str, Any]]:
    """生成前端可展示的最小消息列表。

    课程版目前把完整 LangGraph 消息保存在 checkpoint 中，业务 Store 只保存摘要。
    为了让复制来的 UI 可以先跑通，这里用摘要和 PR 链接生成可读消息。
    """

    created_at = thread.get("created_at") or datetime.now().isoformat()
    messages: list[dict[str, Any]] = []
    persisted_messages = thread.get("messages") or []
    for index, message in enumerate(persisted_messages):
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        content = _user_visible_text(content)
        author = message.get("author") if message.get("author") in {"user", "agent", "system", "tool"} else "agent"
        messages.append(
            {
                "id": message.get("message_id") or f"{thread['thread_id']}-message-{index}",
                "author": author,
                "timestamp": message.get("created_at") or created_at,
                "chunks": [{"kind": "text", "text": content}],
            }
        )
    if not messages:
        user_prompt = thread.get("user_prompt") or thread.get("title")
        if user_prompt:
            messages.append(
                {
                    "id": f"{thread['thread_id']}-user",
                    "author": "user",
                    "timestamp": created_at,
                    "chunks": [{"kind": "text", "text": user_prompt}],
                }
            )
    lines = [
        f"任务状态：{thread.get('latest_run_status')}",
        f"仓库：{thread.get('repo_url') or ''}",
    ]
    if thread.get("branch_name"):
        lines.append(f"分支：{thread['branch_name']}")
    if thread.get("pr_url"):
        lines.append(f"PR：{thread['pr_url']}")
    latest_run = thread.get("latest_run") or {}
    if latest_run.get("error"):
        lines.append(f"错误：{latest_run['error']}")
    run_events = thread.get("run_events") or []
    todos = _todos_from_events(run_events, final_status=thread.get("latest_run_status"))
    if todos:
        plan_chunks: list[dict[str, Any]] = [{"kind": "text", "text": "任务计划"}, {"kind": "todo", "todos": todos}]
    else:
        plan_chunks = []
    event_chunks: list[dict[str, Any]] = []
    for event in run_events:
        if _should_hide_event(event):
            continue
        if _is_stream_message_event(event):
            # 模型正在生成方案或回答时，把累计文本直接展示成正文。
            # 最终消息写入 thread_messages 后会隐藏该临时块，避免重复展示。
            if not _is_final_status(thread.get("latest_run_status")):
                stream_text = _stream_message_text(event)
                if stream_text:
                    stream_text = _user_visible_stream_text(stream_text)
                    event_chunks.append(
                        {
                            "kind": "text",
                            "text": f"正在生成内容...\n\n{stream_text}",
                        }
                    )
            continue
        event_status = event.get("status") or "completed"
        if _is_final_status(thread.get("latest_run_status")) and event_status in {"pending", "in_progress"}:
            event_status = "completed" if thread.get("latest_run_status") == "completed" else "error"
        event_kind = event.get("kind") or "think"
        event_input, event_output = _parse_event_detail(event.get("detail"))
        event_chunks.append(
            {
                "kind": "tool-execution",
                "toolCallId": event.get("id") or f"{thread['thread_id']}-event",
                "title": event.get("title") or "执行步骤",
                "toolKind": event_kind if event_kind in {"read", "edit", "delete", "move", "search", "execute", "think", "fetch", "slack", "linear", "other"} else "other",
                "status": event_status if event_status in {"pending", "in_progress", "completed", "error"} else "completed",
                "input": event_input,
                "output": event_output if event_output is not None else event.get("detail") or None,
            }
        )
    messages.append(
        {
            "id": f"{thread['thread_id']}-process",
            "author": "agent",
            "timestamp": created_at,
            "chunks": [
                {"kind": "text", "text": "\n".join(lines)},
                *plan_chunks,
                *event_chunks,
                *(
                    [{"kind": "text", "text": _final_summary_text(thread, run_events)}]
                    if _is_final_status(thread.get("latest_run_status"))
                    else []
                ),
            ],
        }
    )
    return messages


def _thread_payload(thread: dict[str, Any]) -> dict[str, Any]:
    repo_full_name = _repo_full_name(thread)
    return {
        "id": thread["thread_id"],
        "title": thread.get("title") or "LX-AICODING Task",
        "repo": repo_full_name,
        "repoFullName": repo_full_name,
        "branch": thread.get("branch_name") or "master",
        "model": get_env("MAIN_MODEL", "deepseek-v4-pro"),
        "effort": None,
        "source": "dashboard",
        "status": _status_for_frontend(thread.get("latest_run_status")),
        "createdAt": _timestamp_ms(thread.get("created_at")),
        "updatedAt": _timestamp_ms(thread.get("updated_at")),
        "messages": _message_payload(thread),
        "pr": _pr_payload(thread),
        "latestPlan": None,
        "diffStats": None,
        "changedFiles": [],
    }


@dashboard_router.get("/me")
def dashboard_me() -> dict[str, Any]:
    return {
        "login": "lx-aicoding",
        "email": None,
        "avatar_url": None,
        "is_admin": True,
        "slack_oauth_enabled": False,
    }


@dashboard_router.get("/options")
def dashboard_options() -> dict[str, Any]:
    model = get_env("MAIN_MODEL", "deepseek-v4-pro")
    return {
        "models": [
            {
                "id": model,
                "label": model,
                "efforts": ["default"],
                "default_effort": "default",
                "supports_images": False,
            }
        ],
        "default_agent_model": model,
        "default_agent_reasoning_effort": "default",
        "default_agent_subagent_model": model,
        "default_agent_subagent_reasoning_effort": "default",
    }


@dashboard_router.get("/profile")
def dashboard_profile() -> dict[str, Any]:
    return {
        "login": "lx-aicoding",
        "email": None,
        "default_model": get_env("MAIN_MODEL", "deepseek-v4-pro"),
        "reasoning_effort": "default",
        "default_subagent_model": None,
        "subagent_reasoning_effort": None,
        "default_repo": "https://gitee.com/msb-goldbin/ai_coding",
        "base_branch": "master",
        "branch_prefix": "lx-aicoding",
        "auto_fix_ci": True,
        "create_prs": True,
        "review_draft_prs": False,
    }


@dashboard_router.get("/repos")
def dashboard_repos() -> dict[str, Any]:
    return {
        "installations": [],
        "repositories": [
            {
                "full_name": "https://gitee.com/msb-goldbin/ai_coding",
                "private": False,
            }
        ],
    }


@dashboard_router.get("/threads")
def dashboard_threads(limit: int = 50) -> list[dict[str, Any]]:
    return [_thread_payload(thread) for thread in list_tasks(limit=limit)]


@dashboard_router.post("/threads")
async def dashboard_create_thread(
    body: DashboardThreadCreateRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    repo_url = body.repo or "https://gitee.com/msb-goldbin/ai_coding"
    thread_id = initialize_task_record(repo_url=repo_url, prompt=body.prompt)
    background_tasks.add_task(
        run_task_safely,
        repo_url=repo_url,
        prompt=body.prompt,
        thread_id=thread_id,
    )
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=500, detail="task was not persisted")
    return _thread_payload(task)


@dashboard_router.get("/threads/{thread_id}")
def dashboard_thread_detail(thread_id: str) -> dict[str, Any]:
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    return _thread_payload(task)


@dashboard_router.post("/threads/{thread_id}/messages")
async def dashboard_send_message(
    thread_id: str,
    body: DashboardThreadMessageRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    repo_url = task.get("repo_url") or "https://gitee.com/msb-goldbin/ai_coding"
    initialize_task_record(repo_url=repo_url, prompt=body.content, thread_id=thread_id)
    background_tasks.add_task(
        run_task_safely,
        repo_url=repo_url,
        prompt=body.content,
        thread_id=thread_id,
    )
    updated = get_task(thread_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="task was not persisted")
    return _thread_payload(updated)


@dashboard_router.post("/threads/{thread_id}/cancel")
def dashboard_cancel_thread(thread_id: str) -> dict[str, Any]:
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    return _thread_payload(task)


@dashboard_router.post("/threads/{thread_id}/approve")
async def dashboard_approve_thread_plan(
    thread_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """确认最新技术方案，并在后台进入编码实施阶段。

    当前前端也可以通过发送“确认实施”消息触发同一流程。
    这个接口为后续增加“确认方案”按钮预留，不需要前端解析自然语言。
    """

    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    repo_url = task.get("repo_url") or "https://gitee.com/msb-goldbin/ai_coding"
    background_tasks.add_task(
        run_task_safely,
        repo_url=repo_url,
        prompt="确认实施",
        thread_id=thread_id,
    )
    return _thread_payload(task)


@dashboard_router.delete("/threads/{thread_id}", status_code=204)
def dashboard_delete_thread(thread_id: str) -> None:
    deleted = delete_task(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="thread not found")
    return None


@dashboard_router.get("/threads/{thread_id}/stream")
def dashboard_thread_stream(thread_id: str):
    """给 open-swe 前端提供最小 SSE 流。

    FastAPI 后端不暴露 LangGraph dev 服务，而是定期把业务 Store 中的
    thread 快照推给前端。普通 Agent 任务会通过官方 stream_events 写入
    run_events，所以这里每次推送的快照都会带上最新步骤信息。
    """

    async def event_iter():
        while True:
            task = get_task(thread_id)
            if task is None:
                yield f"data: {json.dumps({'event': 'error', 'data': {'detail': 'thread not found'}}, ensure_ascii=False)}\n\n"
                break

            payload = _thread_payload(task)
            yield f"data: {json.dumps({'event': 'thread.updated', 'data': payload}, ensure_ascii=False)}\n\n"
            if payload["status"] not in {"running"}:
                yield f"data: {json.dumps({'event': 'done', 'data': {}}, ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(1.5)

    return StreamingResponse(event_iter(), media_type="text/event-stream")


@dashboard_router.get("/schedules")
def dashboard_schedules() -> list[dict[str, Any]]:
    return []
