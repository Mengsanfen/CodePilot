"""Structured Git actions; sandbox commands never receive publication credentials."""
from __future__ import annotations
import os
import re
import subprocess
from pathlib import Path
from langchain_core.tools import tool
from agent.orchestration.context import check_execution, external_operation
from agent.tools.review_context import current_repo, git_read
from agent.tools.runtime_context import get_runtime_configurable, runtime_is_read_only_task


@tool
def prepare_git_commit(files: list[str], message: str) -> dict:
    """将明确列出的变更文件提交到会话分支，不推送；禁止密钥文件和越界路径。"""
    check_execution()
    if runtime_is_read_only_task():
        raise PermissionError('只读任务不可提交')
    if not files or len(files) > 100 or not message.strip() or len(message) > 500:
        raise ValueError('请提供 1–100 个文件与简洁的提交说明')
    root = current_repo()
    for name in files:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or not (root / name).resolve().is_relative_to(root.resolve()):
            raise ValueError('文件路径越界')
        if any(p.startswith('.env') or p in {'.git', '.secrets'} or p.endswith(('.pem', '.key')) for p in path.parts):
            raise ValueError('禁止提交敏感文件')
        if (root / name).is_dir():
            raise ValueError('必须逐个列出文件，不能提交整个目录')
        if not (root / name).exists():
            # Only tracked deletions are valid missing files. Disable Git pathspec globs.
            git_read(root, '--literal-pathspecs', 'ls-files', '--error-unmatch', '--', name)
    if git_read(root, 'diff', '--cached', '--name-only').strip():
        raise ValueError('暂存区已有变更，请先检查，避免提交未指定文件')
    git_read(root, '--literal-pathspecs', 'add', '--', *files)
    git_read(root, '-c', 'user.name=CodePilot', '-c', 'user.email=codepilot@localhost',
             '-c', 'commit.gpgsign=false', 'commit', '-m', message, '--no-verify')
    return {'commit': git_read(root, 'rev-parse', 'HEAD').strip(),
            'summary': git_read(root, 'show', '--stat', '--oneline', 'HEAD', '--')}


@tool
def request_git_push(branch: str) -> dict:
    """提出当前固定提交的推送申请，仅允许 codepilot/ 分支，等待用户批准后执行。"""
    check_execution()
    if runtime_is_read_only_task():
        raise PermissionError('只读任务不可推送')
    if not re.fullmatch(r'codepilot/[a-zA-Z0-9_-]{1,80}', branch):
        raise ValueError('仅允许 codepilot/<名称> 分支')
    root, config = current_repo(), get_runtime_configurable()
    return external_operation('git_push', {'provider': 'git', 'repo_url': config['repo_url'],
        'thread_id': config['thread_id'], 'branch': branch,
        'commit': git_read(root, 'rev-parse', 'HEAD').strip(),
        'summary': git_read(root, 'show', '--stat', '--oneline', 'HEAD', '--')[:10000]}, None)


def execute_push(parameters: dict) -> dict:
    import base64
    import hashlib
    from agent.core.settings import WORKSPACE_ROOT
    from agent.env_utils import get_env
    from agent.security import command_environment
    from agent.tools.gitee_api import parse_gitee_repo_url
    target = parse_gitee_repo_url(parameters['repo_url'])
    # Bare object storage works from both the Windows Worker and Linux API mount;
    # worktree .git files contain host-specific absolute paths and must not be used here.
    root = WORKSPACE_ROOT / 'mirrors' / (hashlib.sha256(target.clone_url.encode()).hexdigest()[:24] + '.git')
    commit, branch = parameters['commit'], parameters['branch']
    if not re.fullmatch(r'[a-f0-9]{40,64}', commit) or not re.fullmatch(r'codepilot/[a-zA-Z0-9_-]{1,80}', branch):
        raise ValueError('invalid push target')
    env = command_environment()
    token = (get_env('GITHUB_TOKEN') or get_env('GH_TOKEN')) if target.provider == 'github' else get_env('GITEE_TOKEN')
    if not token:
        raise RuntimeError('推送需要配置对应 GitHub/Gitee token')
    auth = base64.b64encode(('oauth2:' + token).encode()).decode()
    env.update(GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_1=f'http.https://{target.provider}.com/.extraheader',
               GIT_CONFIG_VALUE_1='Authorization: Basic ' + auth)
    result = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '--git-dir', str(root),
                             'push', target.clone_url, f'{commit}:refs/heads/{branch}'],
                            env=env, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('推送失败，请检查远端分支状态和权限；不会强制覆盖分支')
    return {'repo_url': target.clone_url, 'branch': branch, 'commit': commit}
