from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
import uuid

from agent.core.settings import LOG_DIR, PROJECT_ROOT
from agent.orchestration.processes import process_options, stop_process_tree, attach_process_tree
from agent.orchestration.service import get_run_store
from agent.orchestration.store import LeaseLost
from agent.orchestration.transport import poll_notifications

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, store=None, *, lease_seconds=30, timeout=None, command=None):
        self.store = store or get_run_store()
        self.owner = f"{os.getpid()}-{uuid.uuid4()}"
        self.lease_seconds = lease_seconds
        self.timeout = timeout or float(os.environ.get("CODEPILOT_RUN_TIMEOUT", "1800"))
        self.command = command  # injected in process-level failure tests, never from HTTP input

    def run_once(self, stop: threading.Event | None = None) -> bool:
        self.store.recover_expired()
        run = self.store.claim(self.owner, self.lease_seconds)
        if not run:
            return False
        token, run_id = run["lease_token"], run["run_id"]
        env = {**os.environ, "CODEPILOT_RUN_ID": run_id, "CODEPILOT_LEASE_TOKEN": token,
               "PYTHONUTF8": "1", "CODEPILOT_DATABASE_URL": str(self.store.db_path)}
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        process = None
        status, error = "failed", None
        started = time.monotonic()
        try:
            with (LOG_DIR / f"worker-{run_id}.log").open("ab") as output:
                process = subprocess.Popen(
                    self.command or [sys.executable, "-m", "agent.orchestration.runner"],
                    cwd=PROJECT_ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
                    **process_options(),
                )
                attach_process_tree(process)
                while True:
                    current = self.store.heartbeat(run_id, token, self.lease_seconds)
                    if current["cancel_requested"]:
                        status = "cancelled"
                        break
                    if stop and stop.is_set():
                        status, error = "interrupted", "Worker 正在关闭；执行上下文已保留"
                        break
                    if time.monotonic() - started > self.timeout:
                        error = "运行超过硬超时上限"
                        if self.store.get_run(run_id)['execution_context']:
                            status = 'interrupted'
                            error += '；核对外部操作后可显式恢复 Checkpoint'
                        break
                    if process.poll() is not None:
                        status = "completed" if process.returncode == 0 else "failed"
                        if status == "failed":
                            error = f"执行进程退出码 {process.returncode}；请查看对应 Worker 日志"
                        break
                    time.sleep(min(0.25, self.lease_seconds / 4))
        except LeaseLost:
            # A stale supervisor must never change the new owner's state.
            return True
        except Exception:
            log.exception("Worker failure for run %s", run_id)
            error = "Worker 启动或监督失败"
        finally:
            if process is not None:
                stop_process_tree(process)
            if os.environ.get('CODEPILOT_EXECUTION_BACKEND') == 'docker':
                from agent.backends.docker import cleanup_run_containers
                cleanup_run_containers(run_id)
        if status != 'cancelled':
            try:
                self.store.append_event(run_id, token, 'run.metrics',
                                        {'duration_seconds': round(time.monotonic() - started, 3),
                                         'attempt': run['attempt'], 'outcome': status})
            except LeaseLost:
                pass
        self.store.finish(run_id, token, status, error)
        return True

    def serve(self, stop: threading.Event):
        while not stop.is_set():
            try:
                poll_notifications(self.owner)
                if not self.run_once(stop):
                    stop.wait(0.5)
            except Exception:
                log.exception("Worker loop failed; retrying after backoff")
                stop.wait(2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    worker = Worker()
    if args.once:
        worker.run_once()
    else:
        import signal
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        worker.serve(stop)


if __name__ == "__main__":
    main()
