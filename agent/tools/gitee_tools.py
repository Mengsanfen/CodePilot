from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from agent.core.events import record_event
from agent.core.graph import get_store
from agent.tools.gitee_api import create_pull_request, post_pr_comment
from agent.tools.runtime_context import get_runtime_thread_id, runtime_is_read_only_task

logger = logging.getLogger("agent.run.gitee")


@tool
def open_gitee_pull_request(
    owner: str,
    repo: str,
    head: str,
    base: str = "master",
    title: str = "LX-AICODING generated changes",
    body: str = "由 LX-AICODING 自动生成。",
) -> dict[str, Any]:
    """为已经推送到 Gitee 的分支创建或复用 Pull Request。"""

    if runtime_is_read_only_task():
        return {
            "ok": False,
            "error": "当前任务是只读任务，不能创建 Pull Request。请先向用户输出方案或分析结论，等待确认实施。",
        }

    thread_id = get_runtime_thread_id()
    logger.info(
        "准备创建 Gitee PR：owner=%s repo=%s head=%s base=%s title=%s",
        owner,
        repo,
        head,
        base,
        title,
    )
    if thread_id:
        record_event(thread_id, "gitee:pr", "创建或复用 Pull Request", kind="fetch", status="in_progress")

    pr = create_pull_request(owner=owner, repo=repo, head=head, base=base, title=title, body=body)
    pr_url = pr.get("html_url") or pr.get("url") or ""

    if thread_id:
        get_store().update_thread_status(thread_id, "pr_created", pr_url=pr_url, branch_name=head)
        record_event(
            thread_id,
            "gitee:pr",
            "创建或复用 Pull Request",
            kind="fetch",
            status="completed",
            detail=pr_url,
        )

    if pr.get("reused"):
        logger.info("Gitee PR 已存在，复用已有 PR：thread_id=%s pr_url=%s", thread_id, pr_url)
    else:
        logger.info("Gitee PR 创建完成：thread_id=%s pr_url=%s", thread_id, pr_url)
    return {"ok": True, "pr_url": pr_url, "raw": pr}


@tool
def publish_gitee_pr_comment(owner: str, repo: str, number: int, body: str) -> dict[str, Any]:
    """向指定 Gitee Pull Request 发布普通评论。"""

    logger.info("准备发布 Gitee PR 评论：owner=%s repo=%s number=%s", owner, repo, number)
    return post_pr_comment(owner=owner, repo=repo, number=number, body=body)
