"""A private Git worktree per conversation; shared mirrors are locked during setup."""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path, timeout=120):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        started = time.monotonic()
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() - started > timeout:
                    raise TimeoutError("仓库镜像锁等待超时")
                time.sleep(0.1)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def git(*args: str, env=None) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=env, timeout=120)
    if result.returncode:
        # URLs/credentials can appear in stderr; callers expose only a generic failure.
        raise RuntimeError("Git 工作区准备失败，请检查仓库访问权限和默认分支")
    return result.stdout.strip()


def prepare_worktree(base: Path, workspace: Path, repo_url: str, repo_name: str, thread_id: str, *, env=None) -> Path:
    key = hashlib.sha256(repo_url.encode()).hexdigest()[:24]
    mirror = base / "mirrors" / (key + ".git")
    target = workspace / "projects" / repo_name
    with file_lock(base / "locks" / (key + ".lock")):
        if (target / ".git").exists():
            return target  # preserve uncommitted edits and the approved plan's baseline
        if not mirror.exists():
            mirror.parent.mkdir(parents=True, exist_ok=True)
            git("clone", "--mirror", repo_url, str(mirror), env=env)
        else:
            git("--git-dir", str(mirror), "fetch", "origin", env=env)
        branch = "codepilot/" + hashlib.sha256(thread_id.encode()).hexdigest()[:16]
        target.parent.mkdir(parents=True, exist_ok=True)
        git("--git-dir", str(mirror), "worktree", "add", "-b", branch, str(target), "HEAD", env=env)
    return target
