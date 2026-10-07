from __future__ import annotations

import hashlib
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

    from agent.tools.review_context import current_repo, validate_finding
    from agent.orchestration.context import check_execution
    check_execution()
    try:
        snapshot = validate_finding(current_repo(), file, line, severity)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    identity = f"{thread_id}:{snapshot['digest']}:{file}:{line}:{title.strip().lower()}"
    finding_id = "finding-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    from agent.orchestration.context import identity as run_identity
    current = run_identity()
    if current:
        from agent.orchestration.service import get_run_store
        from agent.orchestration.reviews import record_finding
        finding_id = record_finding(get_run_store(), *current, snapshot,
            {'file': file, 'line': line, 'severity': severity, 'title': title, 'description': description})
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
