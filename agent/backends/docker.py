"""Disposable, network-disabled command containers; controller owns file access."""
from __future__ import annotations

import os
import subprocess
import uuid

from deepagents.backends.protocol import ExecuteResponse
from agent.backends.local_shell import LocalShellBackend
from agent.orchestration.context import check_execution
from agent.security import redact


def cleanup_run_containers(run_id: str):
    import re
    if not re.fullmatch(r'[a-f0-9-]{36}', run_id):
        raise ValueError('invalid run id')
    result = subprocess.run(['docker', 'ps', '-aq', '--filter', f'label=codepilot.run_id={run_id}'],
                            capture_output=True, text=True, timeout=15, check=True)
    ids = result.stdout.split()
    if ids:
        subprocess.run(['docker', 'rm', '-f', *ids], capture_output=True, timeout=20, check=True)


class DockerBackend(LocalShellBackend):
    def container_command(self, command: str, name: str, timeout: int) -> list[str]:
        # Only this conversation's projects are mounted. No socket, DB, keys or other sessions.
        host_root = os.environ.get("CODEPILOT_HOST_WORKSPACE")
        if host_root:
            from pathlib import Path
            relative = self.projects_dir.relative_to(Path(os.environ["CODEPILOT_WORKSPACE_ROOT"]))
            source = host_root.rstrip("/\\") + "/" + relative.as_posix()
        else:
            source = str(self.projects_dir)
        if "," in source:
            raise ValueError("Docker workspace path cannot contain commas")
        return ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only",
                "--label", "codepilot.run_id=" + os.environ.get('CODEPILOT_RUN_ID', 'manual'),
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128",
                "--memory", os.environ.get("CODEPILOT_SANDBOX_MEMORY", "1g"), "--cpus", "1",
                "--user", "65534:65534", "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
                "--mount", f"type=bind,src={source},dst=/projects", "--workdir", "/projects",
                "--env", "HOME=/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1",
                os.environ.get("CODEPILOT_SANDBOX_IMAGE", "codepilot-sandbox:local"),
                "timeout", "--signal=KILL", str(timeout), "sh", "-c", command]

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        check_execution()
        if self.read_only:
            return ExecuteResponse(output="只读 Agent 禁止执行 shell；请使用文件检索工具。", exit_code=126)
        limit = min(timeout or 120, 300)
        name = "codepilot-" + uuid.uuid4().hex
        try:
            result = subprocess.run(self.container_command(command, name, limit), capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=limit + 15)
            output = redact(result.stdout + result.stderr)
            return ExecuteResponse(output=output[:100000], exit_code=result.returncode,
                                   truncated=len(output) > 100000)
        except subprocess.TimeoutExpired:
            return ExecuteResponse(output="容器命令超时", exit_code=124)
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)

    def run(self, command: str, cwd: str = ".", timeout: int = 300):
        raise PermissionError("容器模式禁止旧版主机 shell 接口；请使用 execute 或受控 Git 工具")
