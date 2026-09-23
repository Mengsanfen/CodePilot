from __future__ import annotations

import uuid
from typing import Any

from langchain_core.tools import tool

from agent.core.graph import get_store
from agent.tools.runtime_context import get_runtime_thread_id


@tool
def add_review_finding(
    file: str,
    line: int | None,
    severity: str,
    title: str,
    description: str,
) -> dict[str, str]:
    """把代码审查发现记录到本地 SQLite Store。"""

    thread_id = get_runtime_thread_id()
    if not thread_id:
        return {"status": "error", "error": "缺少 thread_id，无法记录审查发现。"}

    finding_id = f"finding-{uuid.uuid4().hex[:8]}"
    get_store().add_finding(
        finding_id=finding_id,
        thread_id=thread_id,
        file=file,
        line=line,
        severity=severity,
        title=title,
        description=description,
    )
    return {"id": finding_id, "status": "open"}


@tool
def list_review_findings() -> list[dict[str, Any]]:
    """列出当前 thread 的代码审查发现。"""

    thread_id = get_runtime_thread_id()
    if not thread_id:
        return [{"status": "error", "error": "缺少 thread_id，无法读取审查发现。"}]
    return get_store().list_findings(thread_id)
