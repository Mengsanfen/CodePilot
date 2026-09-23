from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from agent.env_utils import get_env


@dataclass(frozen=True)
class GiteeRepo:
    owner: str
    repo: str
    clone_url: str


def parse_gitee_repo_url(repo_url: str) -> GiteeRepo:
    parsed = urlparse(repo_url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"gitee.com", "www.gitee.com"}:
        raise ValueError("第一版只支持 gitee.com 仓库地址")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"无法解析 Gitee 仓库地址: {repo_url}")
    owner = parts[0]
    repo = re.sub(r"\.git$", "", parts[1])
    return GiteeRepo(owner=owner, repo=repo, clone_url=f"https://gitee.com/{owner}/{repo}.git")


def authenticated_clone_url(repo: GiteeRepo) -> str:
    """返回普通 Gitee clone URL。

    函数名保留是为了兼容旧调用点。实际认证方式已按 open-swe 调整为：
    Git 命令使用普通 URL，LocalShellBackend 通过 GIT_ASKPASS 注入 GITEE_TOKEN。
    这样不会把 token 写入命令、日志或 .git/config。
    """

    return repo.clone_url


def get_gitee_token() -> str:
    """读取 Gitee 私人令牌，兼容 open-swe 的 SCM_GITEE_TOKEN。"""

    token = get_env("GITEE_TOKEN").strip() or get_env("SCM_GITEE_TOKEN").strip()
    if not token:
        raise RuntimeError("Missing required environment variable: GITEE_TOKEN or SCM_GITEE_TOKEN")
    return token


def mask_token(text: str) -> str:
    masked = text
    for token_name in ("GITEE_TOKEN", "SCM_GITEE_TOKEN"):
        token = get_env(token_name).strip()
        if token:
            masked = masked.replace(token, "***")
    return masked


def _existing_pr_from_error(text: str) -> dict | None:
    """从 Gitee 重复 PR 错误里提取已有 PR 地址。

    Gitee 在相同 head/base 已有 PR 时会返回 400，而不是幂等成功。
    对课程 Agent 来说，这种情况应该视为“PR 已存在，可复用”，否则重复演示
    同一分支时会把已经成功的任务误判成失败。
    """

    if "已存在相同源分支、目标分支" not in text:
        return None
    match = re.search(r"https://gitee\.com/[^\"<>\\\s]+/pulls/\d+", text)
    if not match:
        return None
    url = match.group(0)
    return {
        "html_url": url,
        "url": url,
        "reused": True,
        "message": "已复用相同源分支和目标分支的现有 Pull Request",
    }


def create_pull_request(
    *,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
) -> dict:
    api_base = get_env("GITEE_API_BASE_URL", "https://gitee.com/api/v5").rstrip("/")
    token = get_gitee_token()
    url = f"{api_base}/repos/{owner}/{repo}/pulls"
    payload = {
        "access_token": token,
        "title": title,
        "head": head,
        "base": base,
        "body": body,
    }
    with httpx.Client(timeout=30) as client:
        response = client.post(url, data=payload)
    if response.status_code >= 400:
        existing = _existing_pr_from_error(response.text)
        if existing is not None:
            return existing
        raise RuntimeError(f"Gitee 创建 PR 失败: {response.status_code} {response.text}")
    return response.json()


def post_pr_comment(*, owner: str, repo: str, number: int, body: str) -> dict:
    api_base = get_env("GITEE_API_BASE_URL", "https://gitee.com/api/v5").rstrip("/")
    token = get_gitee_token()
    url = f"{api_base}/repos/{owner}/{repo}/pulls/{number}/comments"
    with httpx.Client(timeout=30) as client:
        response = client.post(url, data={"access_token": token, "body": body})
    if response.status_code >= 400:
        raise RuntimeError(f"Gitee 发布 PR 评论失败: {response.status_code} {response.text}")
    return response.json()
