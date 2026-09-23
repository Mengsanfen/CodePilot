from __future__ import annotations

from functools import lru_cache

from agent.core.settings import DATABASE_TARGET
from agent.orchestration.store import RunConflict, RunStore
from agent.tools.gitee_api import parse_gitee_repo_url


@lru_cache(maxsize=1)
def get_run_store() -> RunStore:
    return RunStore(DATABASE_TARGET)


def public_run(run: dict) -> dict:
    return {key: run.get(key) for key in (
        "run_id", "thread_id", "repo_url", "status", "attempt", "cancel_requested",
        "created_at", "updated_at", "error", "resume_checkpoint",
    )}


def submit_run(*, repo_url: str, prompt: str, thread_id: str | None = None,
               request_key: str | None = None) -> dict:
    if not prompt.strip() or len(prompt) > 20000:
        raise ValueError("任务内容不能为空且不能超过 20000 字符")
    if thread_id and (len(thread_id) > 128 or not all(c.isalnum() or c in '-_' for c in thread_id)):
        raise ValueError("非法 thread_id")
    if request_key and len(request_key) > 200:
        raise ValueError("Idempotency-Key 过长")
    repo_url = parse_gitee_repo_url(repo_url).clone_url
    try:
        run = get_run_store().submit(repo_url=repo_url, prompt=prompt, thread_id=thread_id, request_key=request_key)
    except Exception as exc:
        # Unique indexes remain the authority during cross-process races.
        if type(exc).__name__ in {"IntegrityError", "UniqueViolation"}:
            raise RunConflict("并发提交冲突，请使用相同 Idempotency-Key 重试") from exc
        raise
    from agent.orchestration.transport import notify_run
    notify_run(run["run_id"])
    return run
