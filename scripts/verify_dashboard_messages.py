from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from uuid import uuid4
import json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.api.dashboard_routes import _message_payload
from agent.api.dashboard_routes import _user_visible_text
from agent.store.sqlite_store import LocalSqliteStore


def _todo_statuses(thread: dict) -> list[str]:
    return [
        todo.get("status")
        for message in _message_payload(thread)
        for chunk in message.get("chunks", [])
        if chunk.get("kind") == "todo"
        for todo in chunk.get("todos", [])
    ]


def main() -> None:
    thread = {
        "thread_id": "dashboard-message-test",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:10+00:00",
        "title": "先帮我解析目录结构",
        "user_prompt": "第二个问题",
        "repo_url": "https://gitee.com/msb-goldbin/ai_coding",
        "latest_run_status": "completed",
        "latest_run": None,
        "run_events": [],
        "messages": [
            {
                "message_id": "u1",
                "author": "user",
                "content": "先帮我解析目录结构",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "message_id": "a1",
                "author": "agent",
                "content": "目录结构分析结果",
                "created_at": "2026-01-01T00:00:01+00:00",
            },
            {
                "message_id": "u2",
                "author": "user",
                "content": "再给我一个方案",
                "created_at": "2026-01-01T00:00:02+00:00",
            },
            {
                "message_id": "a2",
                "author": "agent",
                "content": "方案正文",
                "created_at": "2026-01-01T00:00:03+00:00",
            },
        ],
    }
    messages = _message_payload(thread)
    if any(chunk.get("kind") == "todo" for message in messages for chunk in message.get("chunks", [])):
        raise AssertionError("没有 write_todos 时不应该输出 todo chunk")
    visible_text = "\n".join(
        chunk["text"]
        for message in messages
        for chunk in message.get("chunks", [])
        if chunk.get("kind") == "text"
    )
    if "任务计划" in visible_text:
        raise AssertionError("没有 write_todos 时不应该显示固定任务计划")
    for expected in ["先帮我解析目录结构", "目录结构分析结果", "再给我一个方案", "方案正文"]:
        if expected not in visible_text:
            raise AssertionError(f"dashboard 消息缺少：{expected}")

    todo_event = {
        "id": "dashboard-message-test:todos:1",
        "kind": "todo",
        "title": "任务清单",
        "status": "in_progress",
        "detail": json.dumps(
            {
                "todos": [
                    {"content": "读取需求", "status": "completed"},
                    {"content": "编写方案", "status": "in_progress"},
                    {"content": "等待确认", "status": "pending"},
                ]
            },
            ensure_ascii=False,
        ),
    }
    completed_statuses = _todo_statuses({**thread, "latest_run_status": "completed", "run_events": [todo_event]})
    if completed_statuses != ["completed", "completed", "completed"]:
        raise AssertionError(f"completed 状态应该把残留 todo 置为 completed，实际：{completed_statuses}")
    approval_statuses = _todo_statuses({**thread, "latest_run_status": "awaiting_approval", "run_events": [todo_event]})
    if approval_statuses != ["completed", "completed", "completed"]:
        raise AssertionError(f"awaiting_approval 状态应该把残留 todo 置为 completed，实际：{approval_statuses}")
    failed_statuses = _todo_statuses({**thread, "latest_run_status": "failed", "run_events": [todo_event]})
    if failed_statuses != ["completed", "in_progress", "pending"]:
        raise AssertionError(f"failed 状态不应该强制完成 todo，实际：{failed_statuses}")

    running_thread = {
        **thread,
        "thread_id": "dashboard-streaming-test",
        "latest_run_status": "running",
        "messages": [{"message_id": "u1", "author": "user", "content": "生成方案", "created_at": "2026-01-01T00:00:00+00:00"}],
        "run_events": [
            {
                "id": "dashboard-streaming-test:stream:message",
                "kind": "other",
                "title": "正在分析需求",
                "status": "in_progress",
                "detail": json.dumps({"text": "## 技术方案\n\n正在逐步输出"}),
            },
            {
                "id": "dashboard-streaming-test:stream:assistant:1",
                "kind": "other",
                "title": "智能体输出 1",
                "status": "in_progress",
                "detail": json.dumps({"text": "## 技术方案\n\n第一段方案"}),
            }
        ],
    }
    running_text = "\n".join(
        chunk["text"]
        for message in _message_payload(running_thread)
        for chunk in message.get("chunks", [])
        if chunk.get("kind") == "text"
    )
    if "正在生成内容" not in running_text or "正在逐步输出" not in running_text:
        raise AssertionError("running 状态应该展示模型流式正文")
    running_tool_titles = [
        chunk.get("title")
        for message in _message_payload(running_thread)
        for chunk in message.get("chunks", [])
        if chunk.get("kind") == "tool-execution"
    ]
    if any("智能体输出" in str(title) for title in running_tool_titles):
        raise AssertionError("running 状态不应该展示 assistant 输出折叠块")

    mixed_stream_thread = {
        **thread,
        "thread_id": "dashboard-mixed-stream-test",
        "latest_run_status": "running",
        "messages": [{"message_id": "u1", "author": "user", "content": "生成方案", "created_at": "2026-01-01T00:00:00+00:00"}],
        "run_events": [
            {
                "id": "dashboard-mixed-stream-test:stream:message",
                "kind": "other",
                "title": "正在生成内容",
                "status": "in_progress",
                "detail": json.dumps(
                    {
                        "text": (
                            "I'll inspect the repository and then produce the design.\n"
                            "Let me check the routes and data files first.\n\n"
                            "## 部门管理模块技术方案\n\n"
                            "一、功能目标\n\n"
                            "- 新增部门列表和部门详情页面\n"
                            "- 用户只能属于一个部门"
                        )
                    },
                    ensure_ascii=False,
                ),
            }
        ],
    }
    mixed_text = "\n".join(
        chunk["text"]
        for message in _message_payload(mixed_stream_thread)
        for chunk in message.get("chunks", [])
        if chunk.get("kind") == "text"
    )
    if "部门管理模块技术方案" not in mixed_text or "I'll inspect" in mixed_text:
        raise AssertionError(f"混合流式文本应该只展示中文方案正文，实际：{mixed_text}")
    if "已隐藏非中文过程描述" in mixed_text:
        raise AssertionError(f"混合流式文本不应该整体被隐藏，实际：{mixed_text}")

    final_thread = {
        **running_thread,
        "latest_run_status": "awaiting_approval",
        "messages": [
            {"message_id": "u1", "author": "user", "content": "生成方案", "created_at": "2026-01-01T00:00:00+00:00"},
            {"message_id": "a1", "author": "agent", "content": "最终技术方案", "created_at": "2026-01-01T00:00:01+00:00"},
        ],
        "run_events": [],
    }
    final_text = "\n".join(
        chunk["text"]
        for message in _message_payload(final_thread)
        for chunk in message.get("chunks", [])
        if chunk.get("kind") == "text"
    )
    if "正在逐步输出" in final_text:
        raise AssertionError("最终状态不应该继续展示临时流式正文")
    if "最终技术方案" not in final_text:
        raise AssertionError("最终状态应该展示已持久化方案正文")
    final_tool_titles = [
        chunk.get("title")
        for message in _message_payload(final_thread)
        for chunk in message.get("chunks", [])
        if chunk.get("kind") == "tool-execution"
    ]
    if any("智能体输出" in str(title) for title in final_tool_titles):
        raise AssertionError("最终状态不应该展示 assistant 输出折叠块")

    long_chinese = (
        "仓库已定位到 projects/ai_coding。现在扫描项目结构，找出所有 API 接口定义。"
        "项目结构清晰，main.py 应该是 API 接口所在文件。"
        "我已经读取核心文件，现在对路由装饰器进行系统梳理。"
        "接下来会汇总接口并运行必要测试。"
    )
    formatted = _user_visible_text(long_chinese)
    if "\n\n" not in formatted:
        raise AssertionError(f"长中文 assistant 输出应该自动分段：{formatted}")
    if "projects/ai_coding" not in formatted or "main.py" not in formatted:
        raise AssertionError("文本分段不应破坏路径或文件名")

    markdown_text = "## 技术方案\n\n- 读取 main.py\n- 运行 python -m pytest"
    if _user_visible_text(markdown_text) != markdown_text:
        raise AssertionError("已有 Markdown 结构不应被重新分段")

    with tempfile.TemporaryDirectory() as tmpdir:
        store = LocalSqliteStore(Path(tmpdir) / "store.sqlite")
        thread_id = f"dedupe-{uuid4()}"
        store.upsert_thread(thread_id=thread_id, title="dedupe", latest_run_status="running")
        store.add_thread_message(message_id="u1", thread_id=thread_id, author="user", content="同一个问题")
        store.add_thread_message(message_id="u2", thread_id=thread_id, author="user", content="同一个问题")
        user_messages = [m for m in store.list_thread_messages(thread_id) if m["author"] == "user"]
        if len(user_messages) != 1:
            raise AssertionError(f"user 消息去重失败：{len(user_messages)}")
        store._conn.close()

    print("dashboard message verification passed")


if __name__ == "__main__":
    main()
