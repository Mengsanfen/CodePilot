import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.orchestration.approvals import propose, approve, list_operations
from agent.orchestration.operations import digest
from agent.orchestration.store import RunConflict, RunStore


def ready(store, kind='comment_pr'):
    run = store.submit(repo_url='https://github.com/example/repo', prompt='implement')
    claimed = store.claim('worker')
    item = propose(store, run['run_id'], claimed['lease_token'], kind, {'body': 'reviewed content'})
    store.finish(run['run_id'], claimed['lease_token'], 'completed')
    return run, item['operation_id']


def test_tampered_parameters_are_rejected_before_external_call(store):
    _, op = ready(store)
    with store.transaction():
        store._conn.execute('UPDATE operation_approvals SET parameters=? WHERE operation_id=?',
                            (json.dumps({'body': 'different content'}), op))
    called = []
    with pytest.raises(RunConflict, match='指纹'):
        approve(store, op, op, lambda *args: called.append(args))
    assert called == []


def test_concurrent_approval_claims_once_and_emits_state_transitions(store):
    run, op = ready(store)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def action(*args):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return {'id': 1}
    def execute():
        other = RunStore(store.db_path)
        try:
            return approve(other, op, op, action)
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(execute)
        assert entered.wait(5)
        try:
            with pytest.raises(RunConflict):
                execute()
        finally:
            release.set()
        assert first.result() == {'id': 1}
    assert len(calls) == 1
    events = [e['event'] for e in store.events(run['run_id']) if e['event'].startswith('operation.')]
    assert events == ['operation.proposed', 'operation.approved', 'operation.executing', 'operation.completed']


def test_legacy_comment_proposal_keeps_original_digest(store):
    run, op = ready(store)
    old = digest(run['run_id'], 'post_comment', {'body': 'reviewed content'})
    with store.transaction():
        store._conn.execute("UPDATE operation_approvals SET operation_id=?,kind='post_comment',status='pending' WHERE operation_id=?", (old, op))
    item = list_operations(store, run['run_id'])[0]
    assert item['kind'] == 'comment_pr' and item['status'] == 'proposed'
    assert item['expected_digest'] == old
    assert approve(store, old, old, lambda kind, _: {'kind': kind}) == {'kind': 'comment_pr'}


def test_approved_but_not_executing_requires_explicit_request(store):
    run, op = ready(store)
    with store.transaction():
        store._conn.execute("UPDATE operation_approvals SET status='approved' WHERE operation_id=?", (op,))
    assert list_operations(store, run['run_id'])[0]['status'] == 'approved'
    assert approve(store, op, op, lambda *_: {'id': 2}) == {'id': 2}


def test_unknown_operation_kind_is_rejected(store):
    with pytest.raises(ValueError):
        ready(store, kind='arbitrary_shell')


@pytest.mark.parametrize('matches', [True, False])
def test_gitee_publish_checks_approved_commit(monkeypatch, matches):
    from agent.tools import scm, gitee_api
    from agent.backends import worktrees
    sha = 'a' * 40
    monkeypatch.setattr(worktrees, 'git', lambda *args, **kwargs:
                        f"{sha if matches else 'b' * 40}\trefs/heads/codepilot/test")
    calls = []
    monkeypatch.setattr(gitee_api, 'create_pull_request', lambda **kwargs: calls.append(kwargs) or {'id': 1})
    params = {'provider': 'gitee', 'owner': 'example', 'repo': 'repo', 'head': 'codepilot/test',
              'base': 'main', 'title': 'test', 'body': '', 'expected_head_sha': sha}
    if matches:
        assert scm.execute_approved('create_pr', params) == {'id': 1}
    else:
        with pytest.raises(RuntimeError, match='提交'):
            scm.execute_approved('create_pr', params)
    assert len(calls) == int(matches)
