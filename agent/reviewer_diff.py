from __future__ import annotations

from agent.backends.local_shell import LocalShellBackend


def get_local_diff(backend: LocalShellBackend, repo_dir: str, base: str = "HEAD") -> str:
    """Return the local git diff that the reviewer should inspect."""
    result = backend.run(f"git diff --unified=80 {base}", cwd=repo_dir)
    if result.exit_code != 0:
        raise RuntimeError(result.stderr or result.stdout or "git diff failed")
    return result.stdout
