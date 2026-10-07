"""Read PR identity over HTTPS; fetch objects only from the validated base repository."""
import re
import httpx
from agent.env_utils import get_env
from agent.tools.gitee_api import parse_gitee_repo_url


def pull_request_snapshot(repo_url: str, number: int, *, client=None) -> dict:
    repo = parse_gitee_repo_url(repo_url)
    if repo.provider != 'github' or number <= 0:
        raise ValueError('PR 审查目前支持 GitHub 仓库及正整数 PR 编号')
    headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    token = get_env('GITHUB_TOKEN') or get_env('GH_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    own_client = client is None
    client = client or httpx.Client(base_url='https://api.github.com', headers=headers, timeout=20)
    try:
        response = client.get(f'/repos/{repo.owner}/{repo.repo}/pulls/{number}')
        if response.is_error:
            raise ValueError(f'无法读取 GitHub PR：HTTP {response.status_code}；私有仓库或限流时请配置 GITHUB_TOKEN')
        data = response.json()
        base, head = data['base']['sha'], data['head']['sha']
        if not all(re.fullmatch(r'[a-f0-9]{40}', sha) for sha in (base, head)):
            raise ValueError('PR 返回了非法提交标识')
        if data['base']['repo']['full_name'].casefold() != f'{repo.owner}/{repo.repo}'.casefold():
            raise ValueError('PR 仓库身份不匹配')
        return {'kind': 'pr_review', 'number': number, 'base_sha': base, 'head_sha': head,
                'repo_url': repo.clone_url, 'url': f'https://github.com/{repo.owner}/{repo.repo}/pull/{number}'}
    except (httpx.HTTPError, KeyError, TypeError) as exc:
        raise ValueError('GitHub PR 请求失败或响应不完整，请检查连接并重试') from exc
    finally:
        if own_client:
            client.close()
