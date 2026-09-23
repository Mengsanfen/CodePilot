from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.backends.workspace import Workspace
from agent.core.repo_mapping import (
    discover_repo_mapping,
    normalize_gitee_repo_url,
    remote_matches_repo,
)
from agent.store import LocalSqliteStore


def _write_git_config(repo_dir: Path, remote_url: str) -> None:
    """创建最小 .git/config，用于验证 remote 匹配逻辑。"""

    git_dir = repo_dir / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "config").write_text(
        f'[remote "origin"]\n\turl = {remote_url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n',
        encoding="utf-8",
    )


def main() -> None:
    """验证 Gitee 仓库与 projects 目录的映射发现和保存逻辑。"""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        workspace_root = root / "workspace"
        projects = workspace_root / "projects"
        for name in ["runtimes", "policies", "reviews", "logs", "tmp", ".secrets"]:
            (workspace_root / name).mkdir(parents=True, exist_ok=True)
        projects.mkdir(parents=True, exist_ok=True)

        store = LocalSqliteStore(root / "store.sqlite")
        scan_store: LocalSqliteStore | None = None
        try:
            workspace = Workspace(workspace_root)

            repo_url = "https://oauth2:token@gitee.com/msb-goldbin/ai_coding.git"
            normalized = normalize_gitee_repo_url(repo_url)
            if normalized != "https://gitee.com/msb-goldbin/ai_coding.git":
                raise AssertionError(f"仓库地址标准化失败: {normalized}")
            if not remote_matches_repo(repo_url, normalized):
                raise AssertionError("带 token 的 remote 应能匹配标准仓库地址")

            default_repo = projects / "ai_coding"
            _write_git_config(default_repo, "https://gitee.com/msb-goldbin/ai_coding.git")
            result = discover_repo_mapping(repo_url=normalized, workspace=workspace, store=store)
            if result.project_dir != "projects/ai_coding" or not result.remote_matched:
                raise AssertionError(f"按仓库名发现失败: {result}")

            mapping = store.get_repo_mapping(normalized)
            if not mapping or mapping["project_dir"] != "projects/ai_coding":
                raise AssertionError(f"映射未保存: {mapping}")

            # 清空 store 后验证扫描 projects 下其它目录可以找到 remote 匹配项。
            scan_store = LocalSqliteStore(root / "store-scan.sqlite")
            default_git = default_repo / ".git" / "config"
            default_git.unlink()
            other_repo = projects / "course-demo"
            _write_git_config(other_repo, "https://gitee.com/msb-goldbin/ai_coding.git")
            scan_result = discover_repo_mapping(repo_url=normalized, workspace=workspace, store=scan_store)
            if scan_result.project_dir != "projects/course-demo" or scan_result.source != "projects_scan":
                raise AssertionError(f"扫描 projects 发现失败: {scan_result}")

            # 即使特色目录下有同名内容，也不应该被当成项目目录扫描。
            runtime_repo = workspace_root / "runtimes" / "ai_coding"
            _write_git_config(runtime_repo, "https://gitee.com/msb-goldbin/ai_coding.git")
            mappings = scan_store.list_repo_mappings()
            if any(str(item["project_dir"]).startswith("runtimes") for item in mappings):
                raise AssertionError(f"特色目录不应写入仓库映射: {mappings}")
        finally:
            store.close()
            if scan_store is not None:
                scan_store.close()

    print("repo mapping verification passed")


if __name__ == "__main__":
    main()
