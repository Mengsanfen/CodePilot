"""One supervised process per run. Configure workspace before importing runtime."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import threading
from pathlib import Path


def watchdog(stop, store, run_id, token):
    while not stop.wait(0.5):
        try:
            store.assert_lease(run_id, token)
        except Exception:
            # Fail closed on loss of supervisor, cancellation or database connectivity.
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(os.getpid()), "/T", "/F"],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(os.getpgrp(), signal.SIGKILL)
            os._exit(130)


def main():
    from agent.env_utils import load_environment
    load_environment()
    from agent.orchestration.service import get_run_store
    store = get_run_store()
    run_id, token = os.environ["CODEPILOT_RUN_ID"], os.environ["CODEPILOT_LEASE_TOKEN"]
    run = store.assert_lease(run_id, token)
    from agent.core import settings
    base = settings.WORKSPACE_ROOT
    workspace = base / "sessions" / hashlib.sha256(run["thread_id"].encode()).hexdigest()[:24]
    os.environ["AI_WORKSPACE_ROOT"] = str(workspace)
    os.environ["LOCAL_SHELL_WORKSPACE"] = str(workspace)
    settings.WORKSPACE_ROOT = workspace
    settings.PROJECTS_DIR = workspace / "projects"
    settings.SKILLS_DIR = workspace / "skills"
    stop = threading.Event()
    from agent.orchestration.store import RunStore
    watcher_store = RunStore(store.db_path)
    watcher = threading.Thread(target=watchdog, args=(stop, watcher_store, run_id, token), daemon=True)
    watcher.start()
    try:
        from agent.backends.local_shell import LocalShellBackend
        from agent.backends.worktrees import prepare_worktree
        from agent.tools.gitee_api import parse_gitee_repo_url
        from agent.core import runtime
        backend = LocalShellBackend(str(workspace))
        import shutil
        shutil.copytree(settings.PROJECT_ROOT / "agent" / "skills", workspace / "skills", dirs_exist_ok=True)
        repo = parse_gitee_repo_url(run["repo_url"])
        prepare_worktree(base, workspace, repo.clone_url, repo.repo, run["thread_id"], env=backend._execution_env())
        if run["resume_checkpoint"]:
            context = json.loads(run["execution_context"])
            agent = runtime._build_agent_for_runtime(thread_id=run["thread_id"],
                                                     task_kind=context["task_kind"], repo_url=run["repo_url"])
            state = agent.get_state({"configurable": {"thread_id": run["thread_id"]}})
            if not state or not state.values:
                raise RuntimeError("没有可恢复的 Checkpoint，禁止当作新任务重新执行")
            if state.next:
                result = runtime.run_agent_with_event_stream(agent=agent, thread_id=run["thread_id"],
                                                             content=None, task_kind=context["task_kind"])
                messages = result["messages"]
            else:
                messages = [runtime._message_to_dict(message) for message in state.values.get("messages", [])]
            text = (runtime._extract_best_plan_text(messages) if context["task_kind"] == "planning"
                    else runtime._extract_final_assistant_text(messages))
            if not text:
                raise RuntimeError("恢复后没有可展示的结果")
            planning = context["task_kind"] == "planning"
            store.add_thread_message(message_id=f"recovered:{run_id}", thread_id=run["thread_id"], run_id=run_id,
                                     author="agent", content=text, metadata={"task_kind": context["task_kind"],
                                     "awaiting_confirmation": planning, "source_prompt": run["prompt"]})
        else:
            runtime.run_agent_task(repo_url=run["repo_url"], prompt=run["prompt"], thread_id=run["thread_id"])
    finally:
        stop.set()
        watcher.join(timeout=2)
        watcher_store.close()


if __name__ == "__main__":
    main()
