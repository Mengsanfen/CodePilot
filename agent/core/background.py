from __future__ import annotations

import logging

from agent.core.runtime import (
    is_pull_only_task,
    is_workspace_listing_task,
    run_agent_task,
    run_pull_only_task,
    run_workspace_listing_task,
)

logger = logging.getLogger("agent.run.background")


def run_task_safely(*, repo_url: str, prompt: str, thread_id: str) -> None:
    """后台执行 dashboard 触发的任务。

    FastAPI 的 BackgroundTasks 会在响应返回后调用这个函数。
    这里必须吞掉异常，因为真正的失败状态已经由 runtime 写入 Store；
    如果异常继续冒泡，只会污染 Uvicorn 后台任务日志，前端也拿不到更有用的信息。
    """

    try:
        if is_workspace_listing_task(prompt):
            run_workspace_listing_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)
        elif is_pull_only_task(prompt):
            run_pull_only_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)
        else:
            run_agent_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)
    except Exception:
        logger.exception("后台任务执行失败：thread_id=%s", thread_id)
