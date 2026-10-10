"""Provider-aware publication, invoked only after immutable-operation approval."""
from __future__ import annotations

import httpx

from agent.env_utils import get_env
from agent.orchestration.context import external_operation
from agent.tools.gitee_api import parse_gitee_repo_url
from agent.tools.runtime_context import get_runtime_configurable, runtime_is_read_only_task


def repository(owner: str, repo: str):
    url = get_runtime_configurable().get('repo_url') or get_env('DEFAULT_REPO_URL')
    target = parse_gitee_repo_url(url) if url else parse_gitee_repo_url(f'https://gitee.com/{owner}/{repo}')
    if (target.owner, target.repo) != (owner, repo):
        raise PermissionError('禁止向当前任务之外的仓库发布')
    if runtime_is_read_only_task():
        raise PermissionError('只读任务禁止发布')
    return target


def github_client():
    token = get_env('GITHUB_TOKEN') or get_env('GH_TOKEN')
    if not token:
        raise RuntimeError('GitHub 发布需要在本地 .env 配置 GITHUB_TOKEN')
    return httpx.Client(base_url='https://api.github.com', timeout=30, headers={
        'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    })


def create_pr(**parameters):
    target = repository(parameters['owner'], parameters['repo'])
    from agent.tools.review_context import current_repo, git_read
    import re
    if not re.fullmatch(r'codepilot/[a-zA-Z0-9_-]{1,80}', parameters['head']):
        raise ValueError('只允许从 codepilot/ 分支提出 PR')
    parameters['expected_head_sha'] = git_read(current_repo(), 'rev-parse', 'HEAD').strip()
    return external_operation('create_pr', {'provider': target.provider, **parameters}, None)


def post_comment(**parameters):
    target = repository(parameters['owner'], parameters['repo'])
    return external_operation('comment_pr', {'provider': target.provider, **parameters}, None)


def execute_approved(kind: str, parameters: dict) -> dict:
    from agent.orchestration.operations import kind_of
    kind = kind_of(kind).value
    if kind == 'git_push':
        from agent.tools.git_control import execute_push
        return execute_push(parameters)
    p = dict(parameters)
    provider = p.pop('provider')
    expected_head = p.pop('expected_head_sha', None)
    if provider == 'gitee':
        from agent.tools.gitee_api import create_pull_request, post_pr_comment
        if kind == 'create_pr':
            from agent.backends.git_environment import git_environment
            from agent.backends.worktrees import git
            import re
            if not expected_head or not re.fullmatch(r'codepilot/[a-zA-Z0-9_-]{1,80}', p['head']):
                raise ValueError('PR 提案缺少固定提交或分支不合法，请重新提出申请')
            target = parse_gitee_repo_url(f"https://gitee.com/{p['owner']}/{p['repo']}")
            ref = 'refs/heads/' + p['head']
            remote = git('ls-remote', '--heads', target.clone_url, ref, env=git_environment('gitee'))
            if remote.split() != [expected_head, ref]:
                raise RuntimeError('远端分支提交与审批快照不一致，请核对推送结果')
            return create_pull_request(**p)
        if kind == 'comment_pr':
            return post_pr_comment(**p)
    elif provider == 'github':
        with github_client() as client:
            endpoint = f"/repos/{p['owner']}/{p['repo']}"
            if kind == 'create_pr':
                remote = client.get(endpoint + '/git/ref/heads/' + p['head'])
                if remote.is_error or remote.json().get('object', {}).get('sha') != expected_head:
                    raise RuntimeError('远端分支提交与审批快照不一致，请先核对已批准的推送结果')
                existing = client.get(endpoint + '/pulls', params={'state': 'open',
                    'head': f"{p['owner']}:{p['head']}", 'base': p['base']})
                if existing.is_error:
                    raise RuntimeError(f'GitHub 查询失败：HTTP {existing.status_code}')
                if existing.json():
                    return {**existing.json()[0], 'reused': True}
                response = client.post(endpoint + '/pulls', json={k: p[k] for k in ('head', 'base', 'title', 'body')} | {'draft': True})
            elif kind == 'comment_pr':
                response = client.post(endpoint + f"/issues/{p['number']}/comments", json={'body': p['body']})
            else:
                raise ValueError('unknown publication kind')
            if response.is_error:
                raise RuntimeError(f'GitHub 发布失败：HTTP {response.status_code}')
            return response.json()
    raise ValueError('unsupported publication')
