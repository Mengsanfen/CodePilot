import json
import os
import subprocess
from pathlib import Path
import pytest

from agent.backends.local_shell import LocalShellBackend
from agent.backends.docker import DockerBackend
from agent.backends.worktrees import prepare_worktree
from agent.core.repo_memory import build_manifest
from agent.orchestration.approvals import propose, approve, list_operations
from agent.orchestration.store import RunConflict
from agent.security import command_environment, redact
from agent.tools.review_context import validate_finding, git_read


def test_negative_coding_constraints_stay_analysis():
    from agent.core.task_intent import classify_task_kind
    assert classify_task_kind('分析仓库，不修改文件，不提交，不运行测试') == 'analysis'
    assert classify_task_kind('实现登录功能，不提交') == 'coding'


def test_compiled_reviewer_cannot_write_even_when_model_requests_it(tmp_path):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from agent.server import readonly_subagent
    class ToolModel(FakeMessagesListChatModel):
        def bind_tools(self, *args, **kwargs):
            return self
    model = ToolModel(responses=[
        AIMessage(content='', tool_calls=[{'name': 'write_file', 'args': {'file_path': '/projects/escape.py', 'content': 'unsafe'}, 'id': 'attempt'}]),
        AIMessage(content='review finished')])
    backend = LocalShellBackend(tmp_path / 'review')
    spec = readonly_subagent(backend, model, reviewer=True)
    result = spec['runnable'].invoke({'messages': [HumanMessage(content='review')]})
    assert not (backend.projects_dir / 'escape.py').exists()
    assert any('read-only' in str(m.content) for m in result['messages'])


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'origin'
    root.mkdir()
    def git(*args):
        subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
    git('init')
    git('config', 'core.autocrlf', 'false')
    git('config', 'user.email', 'test@local')
    git('config', 'user.name', 'Test')
    (root / 'README.md').write_text('# Fixture\n', encoding='utf-8')
    (root / 'app.py').write_text('answer = 1\n', encoding='utf-8')
    git('add', '.')
    git('commit', '-m', 'fixture')
    git('remote', 'add', 'origin', 'https://github.com/example/fixture.git')
    return root


def test_readonly_cannot_mutate_or_execute(tmp_path):
    backend = LocalShellBackend(tmp_path / 'session', read_only=True)
    assert backend.write('/projects/x.py', 'x').error
    assert backend.upload_files([('/projects/x.py', b'x')])[0].error
    with pytest.raises(PermissionError):
        backend.execute('echo unsafe')
    with pytest.raises(PermissionError):
        backend.run('echo unsafe')


def test_secrets_and_traversal_are_invisible(tmp_path):
    backend = LocalShellBackend(tmp_path / 'session')
    (backend.projects_dir / '.env').write_text('PRIVATE_SENTINEL')
    assert backend.read('/projects/.env').error
    assert backend.read('/../outside').error
    assert not backend.grep('PRIVATE_SENTINEL', '/').matches
    assert not any('.secrets' in p['path'] for p in backend.ls('/').entries)


def test_command_environment_has_no_secrets(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-secret-123456789')
    monkeypatch.setenv('CODEPILOT_DATABASE_URL', 'postgresql://user:password@db/app')
    assert 'DEEPSEEK_API_KEY' not in command_environment()
    assert 'CODEPILOT_DATABASE_URL' not in command_environment()
    assert redact('fake-secret-123456789') == '[REDACTED]'


def test_container_contract(tmp_path):
    backend = DockerBackend(tmp_path / 'session')
    command = backend.container_command('python --version', 'codepilot-test', 30)
    assert command[command.index('--network') + 1] == 'none'
    assert '--read-only' in command and '--cap-drop' in command
    assert '--privileged' not in command
    assert not any('docker.sock' in item for item in command)


def test_worktrees_preserve_edits_and_isolate_conversations(repository, tmp_path):
    base = tmp_path / 'work'
    first = prepare_worktree(base, base / 's1', str(repository), 'fixture', 'first')
    second = prepare_worktree(base, base / 's2', str(repository), 'fixture', 'second')
    (first / 'app.py').write_text('answer = 2\n')
    assert (second / 'app.py').read_text() == 'answer = 1\n'
    assert prepare_worktree(base, base / 's1', str(repository), 'fixture', 'first') == first
    assert (first / 'app.py').read_text() == 'answer = 2\n'


def test_manifest_cache_and_dirty_invalidation(repository, tmp_path):
    cache = tmp_path / 'cache'
    first = build_manifest(repository, cache)
    assert first['sources'] and not first['cache_hit']
    assert build_manifest(repository, cache)['cache_hit']
    (repository / 'app.py').write_text('answer = 2\n')
    assert build_manifest(repository, cache)['dirty']


def test_finding_requires_real_changed_line(repository):
    (repository / 'app.py').write_text('answer = 2\n')
    assert validate_finding(repository, 'app.py', 1, 'high')['digest']
    for path, line in [('app.py', 99), ('README.md', 1), ('../app.py', 1)]:
        with pytest.raises(ValueError):
            validate_finding(repository, path, line, 'high')


def test_commit_cannot_stage_directory_or_glob(repository, monkeypatch):
    from agent.tools import git_control
    monkeypatch.setattr(git_control, 'current_repo', lambda: repository)
    monkeypatch.setattr(git_control, 'runtime_is_read_only_task', lambda: False)
    (repository / '.env').write_text('private sentinel')
    for path in ['.', '*.py', '.env']:
        with pytest.raises(ValueError):
            git_control.prepare_git_commit.func(files=[path], message='test')
    assert git_read(repository, 'diff', '--cached', '--name-only').strip() == ''


def proposal(store):
    run = store.submit(repo_url='https://github.com/example/project.git', prompt='implement')
    claimed = store.claim('worker')
    item = propose(store, run['run_id'], claimed['lease_token'], 'create_pr', {'title': 'exact title'})
    return run, claimed, item['operation_id']


def test_approval_is_bound_and_executes_only_once(store):
    run, claimed, operation_id = proposal(store)
    calls = []
    def action(kind, params):
        calls.append(params)
        return {'url': 'https://example.test/pr/1'}
    with pytest.raises(RunConflict):
        approve(store, operation_id, operation_id, action)
    store.finish(run['run_id'], claimed['lease_token'], 'completed')
    with pytest.raises(RunConflict):
        approve(store, operation_id, 'wrong-digest', action)
    assert approve(store, operation_id, operation_id, action)['url']
    assert approve(store, operation_id, operation_id, action)['url']
    assert calls == [{'title': 'exact title'}]


def test_uncertain_approval_cannot_republish(store):
    run, claimed, operation_id = proposal(store)
    store.finish(run['run_id'], claimed['lease_token'], 'completed')
    def failure(*args):
        raise TimeoutError('remote may have accepted the request')
    with pytest.raises(TimeoutError):
        approve(store, operation_id, operation_id, failure)
    with pytest.raises(RunConflict):
        approve(store, operation_id, operation_id, lambda *_: {})
    assert list_operations(store, run['run_id'])[0]['status'] == 'uncertain'


@pytest.mark.parametrize('remote_sha,expected_posts', [('changed', 0), ('approved', 1)])
def test_github_pr_approval_checks_head_and_creates_draft(monkeypatch, remote_sha, expected_posts):
    import httpx
    from agent.tools import scm
    requests = []
    def handler(request):
        requests.append(request)
        if '/git/ref/' in request.url.path:
            return httpx.Response(200, json={'object': {'sha': remote_sha}})
        if request.method == 'GET':
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={'html_url': 'https://example.test/pr/1', 'draft': True})
    monkeypatch.setattr(scm, 'github_client', lambda: httpx.Client(
        base_url='https://api.github.com', transport=httpx.MockTransport(handler)))
    params = {'provider': 'github', 'owner': 'example', 'repo': 'project',
              'head': 'codepilot/test', 'base': 'main', 'title': 'Approved title',
              'body': 'Approved body', 'expected_head_sha': 'approved'}
    if remote_sha != 'approved':
        with pytest.raises(RuntimeError, match='快照不一致'):
            scm.execute_approved('create_pr', params)
    else:
        assert scm.execute_approved('create_pr', params)['draft']
    posts = [r for r in requests if r.method == 'POST']
    assert len(posts) == expected_posts
    if posts:
        assert json.loads(posts[0].content)['draft'] is True
