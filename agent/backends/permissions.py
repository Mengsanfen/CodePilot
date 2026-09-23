from __future__ import annotations

import re
from pathlib import Path


class WorkspacePermissionError(PermissionError):
    pass


def assert_path_inside(path: Path, root: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path == resolved_root or resolved_root in resolved_path.parents:
        return resolved_path
    raise WorkspacePermissionError(f"Path is outside workspace: {resolved_path}")


def normalize_safe_command(command: str) -> str:
    """校验 Agent 准备执行的本地命令。

    课程版第一版只允许少量教学需要的命令族：
    python/py、pytest、pip、git、dir、type、ruff。

    模型经常会在命令末尾追加 `2>&1` 或 `| tail -5`。
    前者是为了合并 stderr，后者是 Unix 查看末尾输出的习惯。
    课程版在 Python 中已经捕获 stdout/stderr，也会把完整输出返回给模型，
    所以这里剥离这两个尾部片段，既兼容模型习惯，又不放开任意管道/重定向能力。
    """

    normalized = command.strip()
    normalized = re.sub(r"\s+\|\s*tail\s+-?\d+\s*$", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+2>&1\s*$", "", normalized)
    lowered = normalized.lower()
    first_word = normalized.split(maxsplit=1)[0].lower() if normalized else ""
    allowed_commands = {"python", "py", "pytest", "pip", "git", "dir", "type", "ruff"}
    if first_word not in allowed_commands:
        raise WorkspacePermissionError(f"Command is not allowed: {command}")

    shell_operators = [
        "&&",
        "||",
        "|",
        "&",
        ";",
        ">",
        "<",
        "`",
        "$(",
        "\n",
        "\r",
    ]
    if any(operator in normalized for operator in shell_operators):
        raise WorkspacePermissionError(f"Blocked shell operator in command: {command}")
    blocked = [
        "format ",
        "shutdown",
        "restart-computer",
        "remove-item",
        "remove-item -recurse",
        "rm -rf",
        "reg delete",
        "del ",
        "del /s",
        "rmdir ",
        "rmdir /s",
        "cipher /w",
    ]
    if any(token in lowered for token in blocked):
        raise WorkspacePermissionError(f"Blocked dangerous command: {command}")
    return normalized


def ensure_safe_command(command: str) -> None:
    """兼容旧调用方的命令校验函数。"""

    normalize_safe_command(command)


_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


def ensure_safe_git_branch(branch: str) -> str:
    if not _BRANCH_RE.fullmatch(branch):
        raise WorkspacePermissionError(f"Invalid git branch name: {branch}")
    if ".." in branch or branch.endswith("/") or branch.endswith(".lock") or "@{" in branch:
        raise WorkspacePermissionError(f"Invalid git branch name: {branch}")
    return branch


def ensure_safe_git_message(message: str) -> str:
    """把模型生成的提交信息归一化为安全的单行 commit message。

    模型经常会生成多行提交说明，甚至包含 JSON 示例中的双引号。
    Git 本身支持复杂 message，但课程版当前通过 Windows shell 执行 git commit，
    所以这里把 message 压缩成单行，并移除 shell 风险字符。
    """

    normalized = " ".join(message.split())
    normalized = normalized.replace('"', "'")
    for token in ["&", "|", ";", "<", ">", "`", "$("]:
        normalized = normalized.replace(token, " ")
    normalized = " ".join(normalized.split())
    if not normalized:
        return "LX-AICODING generated changes"
    return normalized[:200]
