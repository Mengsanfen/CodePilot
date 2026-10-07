import concurrent.futures
import json
import subprocess
import httpx
import pytest
from agent.orchestration.reviews import record_finding, findings, confirm_repairs
from agent.orchestration.store import RunConflict, RunStore
from agent.tools.review_context import diff_snapshot, validate_finding, git_read
from agent.tools.github_review import pull_request_snapshot
from agent.core.repo_memory import build_manifest, save_task_memory, task_history


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    def git(*args):
        subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
    git('init')
    git('config', 'core.autocrlf', 'false')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@local')
    (root / 'README.md').write_text('# Test repository\n')
    (root / 'app.py').write_text('value = 1\n')
    git('add', '.')
    git('commit', '-m', 'initial')
    git('remote', 'add', 'origin', 'https://github.com/example/fixture.git')
    return root, git


def review(store):
    run = store.submit(repo_url='https://github.com/example/fixture.git', prompt='review')
    run = store.claim('test')
    snapshot = {'digest': 'approved-diff', 'head': 'a' * 40}
    payload = {'file': 'app.py', 'line': 1, 'severity': 'high', 'title': 'Bug', 'description': 'Evidence'}
    finding_id = record_finding(store, run['run_id'], run['lease_token'], snapshot, payload)
    assert record_finding(store, run['run_id'], run['lease_token'], snapshot, payload) == finding_id
    store.finish(run['run_id'], run['lease_token'], 'completed')
    return run, finding_id


def test_confirm_selection_is_atomic_idempotent_and_rejects_changed_snapshot(store):
    run, finding_id = review(store)
    with pytest.raises(RunConflict):
        confirm_repairs(store, run['run_id'], [finding_id], 'changed', 'python test.py')
    def submit(_):
        other = RunStore(store.db_path)
        try:
            return confirm_repairs(other, run['run_id'], [finding_id], 'approved-diff', 'python test.py')['run_id']
        finally:
            other.close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(submit, range(4)))
    assert len(set(ids)) == 1
    assert store.spec(ids[0])['findings'][0]['file'] == 'app.py'
    assert findings(store, run['run_id'])[0]['fix_run_id'] == ids[0]
    with pytest.raises(RunConflict):
        confirm_repairs(store, run['run_id'], [finding_id], 'approved-diff', 'different command')


def test_acceptance_failure_rolls_back_enqueue(store, monkeypatch):
    run, finding_id = review(store)
    original = store._event
    def fail(run, event, payload):
        if event == 'review.accepted':
            raise RuntimeError('transaction fault')
        original(run, event, payload)
    monkeypatch.setattr(store, '_event', fail)
    with pytest.raises(RuntimeError):
        confirm_repairs(store, run['run_id'], [finding_id], 'approved-diff', 'python test.py')
    assert store.latest(run['thread_id'])['run_id'] == run['run_id']
    assert findings(store, run['run_id'])[0]['decision'] is None


def test_pr_identity_and_provider_validation():
    def handler(request):
        assert request.url.path == '/repos/example/fixture/pulls/7'
        return httpx.Response(200, json={'base': {'sha': 'a'*40, 'repo': {'full_name': 'example/fixture'}},
                                       'head': {'sha': 'b'*40}})
    with httpx.Client(base_url='https://api.github.com', transport=httpx.MockTransport(handler)) as client:
        result = pull_request_snapshot('https://github.com/example/fixture', 7, client=client)
        assert result['head_sha'] == 'b'*40
        with pytest.raises(ValueError):
            pull_request_snapshot('https://gitee.com/example/fixture', 7, client=client)


def test_real_git_pr_refs_pin_revision_and_reject_race(repo, tmp_path):
    from agent.backends.worktrees import prepare_worktree
    root, git = repo
    base = git_read(root, 'rev-parse', 'HEAD').strip()
    (root / 'app.py').write_text('value = 2\n')
    git('add', '.')
    git('commit', '-m', 'PR change')
    head = git_read(root, 'rev-parse', 'HEAD').strip()
    git('update-ref', 'refs/pull/7/head', head)
    spec = {'number': 7, 'base_sha': base, 'head_sha': head}
    work = prepare_worktree(tmp_path / 'base', tmp_path / 'session', str(root), 'fixture', 'review', review=spec)
    assert git_read(work, 'rev-parse', 'HEAD').strip() == head
    with pytest.raises(ValueError, match='head'):
        prepare_worktree(tmp_path / 'base', tmp_path / 'stale', str(root), 'fixture', 'stale', review={**spec, 'head_sha': base})


def test_untracked_files_and_stale_repair_are_checked(repo):
    from agent.orchestration.review_flow import validate_repair_snapshot
    root, _ = repo
    (root / 'new.py').write_text('answer = 42\n')
    snapshot = validate_finding(root, 'new.py', 1, 'medium')
    spec = {'digest': snapshot['digest'], 'head_sha': snapshot['head']}
    validate_repair_snapshot(root, spec)
    (root / 'new.py').write_text('answer = 43\n')
    with pytest.raises(RunConflict):
        validate_repair_snapshot(root, spec)


def test_diff_tools_do_not_bypass_sensitive_file_policy(repo):
    root, git = repo
    (root / '.env').write_text('SECRET=old')
    git('add', '.env')
    git('commit', '-m', 'sensitive fixture')
    (root / '.env').write_text('SECRET=private-sentinel')
    (root / 'private.pem').write_text('private-key-sentinel')
    (root / 'public.py').write_text('answer=42\n')
    snapshot = diff_snapshot(root)
    assert 'private-sentinel' not in snapshot['diff'] and 'private-key-sentinel' not in snapshot['diff']
    assert snapshot['changed_lines']['public.py'] == [1]


def test_incremental_context_and_historical_evidence(repo, store, tmp_path):
    root, git = repo
    cold = build_manifest(root, tmp_path / 'cache')
    (root / 'app.py').write_text('value = 3\n')
    git('add', '.')
    git('commit', '-m', 'code only')
    warm = build_manifest(root, tmp_path / 'cache')
    assert cold['sources_read'] == 1 and warm['sources_read'] == 0 and warm['sources_reused'] == 1
    run = store.submit(repo_url='https://github.com/example/fixture.git', prompt='implement')
    run = store.claim('worker')
    store.add_thread_message(message_id='evidence', thread_id=run['thread_id'], author='agent', content='Implemented value = 3')
    save_task_memory(store, run, root)
    assert task_history(store, run['repo_url'], warm['head']) == []
    store.finish(run['run_id'], run['lease_token'], 'completed')
    memory = task_history(store, run['repo_url'], warm['head'])[0]
    assert memory['evidence']['message_id'] == 'evidence' and memory['same_commit']
    assert task_history(store, 'https://github.com/other/repo.git', warm['head']) == []


def test_review_api_requires_confirmed_ids_and_propagates_trace(client, store):
    run, finding_id = review(store)
    route = f"/api/runs/{run['run_id']}"
    response = client.post(route + '/repairs', json={'finding_ids': [finding_id], 'expected_digest': 'approved-diff', 'verification_command': 'python test.py'})
    assert response.status_code == 202
    carrier = store.trace_context(response.json()['run_id'])
    assert carrier['traceparent'].split('-')[1] == response.headers['x-trace-id']
    assert client.get(route + '/findings').json()[0]['decision'] == 'accepted'
    assert client.get('/api/runs/missing/findings').status_code == 404


def test_scoring_counts_duplicates_false_positives_and_misses():
    from agent.evaluation import score_review, aggregate
    score = score_review([{'file':'a','line':1}, {'file':'a','line':1}, {'file':'b','line':2}],
                         [{'file':'a','line':1}, {'file':'a','line':3}])
    assert score == {'true_positives':1, 'false_positives':2, 'false_negatives':1}
    metrics = aggregate([{'category':'review', 'passed':False, 'seconds':1, **score}])
    assert metrics['review_location_precision'] == 1/3 and metrics['review_location_recall'] == 0.5


def test_timeout_preserves_checkpoint_for_bounded_explicit_resume(store):
    import sys
    from agent.orchestration.worker import Worker
    run = store.submit(repo_url='https://github.com/example/fixture.git', prompt='long running')
    child = "import os,time; from agent.orchestration.store import RunStore; s=RunStore(os.environ['CODEPILOT_DATABASE_URL']); s.set_context(os.environ['CODEPILOT_RUN_ID'],os.environ['CODEPILOT_LEASE_TOKEN'],{'task_kind':'analysis'}); time.sleep(10)"
    Worker(store, command=[sys.executable, '-c', child], timeout=2).run_once()
    assert store.get_run(run['run_id'])['status'] == 'interrupted'
    assert store.resume(run['run_id'])['resume_checkpoint'] == 1
    again = store.claim('second')
    store.finish(run['run_id'], again['lease_token'], 'interrupted')
    with store.transaction():
        store._conn.execute('UPDATE execution_runs SET attempt=3 WHERE run_id=?', (run['run_id'],))
    with pytest.raises(RunConflict, match='上限'):
        store.resume(run['run_id'])


def test_adjudication_requires_every_exact_finding():
    import hashlib
    from agent.evaluation import adjudicate
    finding = {'file':'a', 'line':1, 'description':'boundary failure'}
    report = {'cases':[{'id':'case', 'findings':[finding]}]}
    with pytest.raises(ValueError):
        adjudicate(report, [])
    digest = hashlib.sha256(json.dumps(finding, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    item = {'case_id':'case', 'index':0, 'finding_sha256':digest, 'valid':True, 'evidence':'repro: f(0) fails'}
    assert adjudicate(report, [item])['valid_defect_rate'] == 1
    with pytest.raises(ValueError):
        adjudicate(report, [item, item])


def test_invalid_tool_arguments_receive_error_reply_before_retry():
    from langchain.agents import create_agent
    from langchain.agents.middleware import ModelCallLimitMiddleware
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from agent.core.middleware.invalid_tool_calls import InvalidToolCallMiddleware
    class Model(FakeMessagesListChatModel):
        def bind_tools(self, *args, **kwargs):
            return self
    model = Model(responses=[AIMessage(content='', invalid_tool_calls=[
        {'name':'ReviewOutput', 'args':'{"file": "line": 6}', 'id':'invalid-1', 'error':'bad json'}]),
        AIMessage(content='recovered')])
    agent = create_agent(model=model, middleware=[InvalidToolCallMiddleware(), ModelCallLimitMiddleware(run_limit=3, exit_behavior='error')])
    result = agent.invoke({'messages':[{'role':'user', 'content':'review'}]})
    assert result['messages'][-1].content == 'recovered'
    replies = [m for m in result['messages'] if isinstance(m, ToolMessage)]
    assert len(replies) == 1 and replies[0].tool_call_id == 'invalid-1' and replies[0].status == 'error'


def test_review_report_requires_full_current_snapshot_evidence(repo, store):
    from agent.orchestration.review_evidence import finalize, write
    root, _ = repo
    run = store.submit(repo_url='https://github.com/example/fixture.git', prompt='review')
    run = store.claim('reviewer')
    snapshot = diff_snapshot(root)
    assert finalize(store, run, snapshot, 'review.analyze', 'No issues')['outcome'] == 'incomplete'
    proof = {**snapshot, 'truncated': False}
    write(store, run, 'review.analyze.read', proof)
    assert finalize(store, run, snapshot, 'review.analyze', 'No issues')['outcome'] == 'no_findings'
    write(store, run, 'review.analyze.read', {**proof, 'truncated': True})
    assert 'diff_truncated' in finalize(store, run, snapshot, 'review.analyze', 'No issues')['reasons']
    (root / 'app.py').write_text('value = 2\n')
    assert 'missing_or_stale_diff_read' in finalize(store, run, diff_snapshot(root), 'review.analyze', 'No issues')['reasons']


def test_report_keeps_execution_success_separate_from_findings(repo, store, client):
    from agent.orchestration.review_evidence import finalize, write
    root, _ = repo
    (root / 'app.py').write_text('value = 0\n')
    snapshot = diff_snapshot(root)
    run = store.submit(repo_url='https://github.com/example/fixture.git', prompt='review')
    run = store.claim('reviewer')
    write(store, run, 'review.analyze.read', {**snapshot, 'truncated': False})
    fid = record_finding(store, run['run_id'], run['lease_token'], snapshot,
                         {'file': 'app.py', 'line': 1, 'title': 'Wrong value', 'severity': 'high', 'description': 'Value must be positive'})
    report = finalize(store, run, snapshot, 'review.analyze', 'One defect')
    assert report['outcome'] == 'changes_requested' and report['finding_ids'] == [fid]
    store.finish(run['run_id'], run['lease_token'], 'completed')
    response = client.get(f"/api/runs/{run['run_id']}/review-report").json()
    assert response['execution_status'] == 'completed' and response['report']['outcome'] == 'changes_requested'
    assert client.get('/api/runs/missing/review-report').status_code == 404


def test_excluded_files_invalidate_approval_and_prevent_clean_report(repo, store):
    from agent.orchestration.review_evidence import finalize, write
    root, _ = repo
    (root / '.env').write_text('SECRET=first')
    snapshot = diff_snapshot(root)
    (root / '.env').write_text('SECRET=second')
    changed = diff_snapshot(root)
    assert snapshot['digest'] != changed['digest'] and 'SECRET' not in changed['diff']
    run = store.submit(repo_url='https://github.com/example/fixture.git', prompt='review')
    run = store.claim('reviewer')
    write(store, run, 'review.analyze.read', {**changed, 'truncated': False})
    report = finalize(store, run, changed, 'review.analyze', 'No issues')
    assert report['outcome'] == 'incomplete' and report['reasons'] == ['files_excluded']


def test_verification_receipt_survives_reopen_and_invalidates_on_change(repo, store):
    from types import SimpleNamespace
    from agent.orchestration.review_evidence import verify_once
    root, _ = repo
    run = store.submit(repo_url='https://github.com/example/fixture.git', prompt='repair')
    run = store.claim('worker')
    class Backend:
        calls = 0
        def execute(self, command, timeout):
            self.calls += 1
            return SimpleNamespace(exit_code=0, output='passed')
    backend = Backend()
    receipt = verify_once(store, run, root, backend, 'test')
    other = RunStore(store.db_path)
    try:
        assert verify_once(other, run, root, backend, 'test') == receipt
    finally:
        other.close()
    assert backend.calls == 1
    (root / 'app.py').write_text('value = 5\n')
    verify_once(store, run, root, backend, 'test')
    verify_once(store, run, root, backend, 'different command')
    assert backend.calls == 3


def test_failed_or_source_mutating_verification_cannot_attest_final_tree(repo, store):
    from types import SimpleNamespace
    from agent.orchestration.review_evidence import verify_once, finalize, write
    root, _ = repo
    run = store.submit(repo_url='https://github.com/example/fixture.git', prompt='repair')
    run = store.claim('worker')
    class Backend:
        calls = 0
        def execute(self, command, timeout):
            self.calls += 1
            if command == 'fail':
                return SimpleNamespace(exit_code=1, output='failed')
            (root / 'app.py').write_text('value = 6\n')
            return SimpleNamespace(exit_code=0, output='rewritten')
    backend = Backend()
    for _ in range(2):
        with pytest.raises(RuntimeError):
            verify_once(store, run, root, backend, 'fail')
    assert backend.calls == 2
    receipt = verify_once(store, run, root, backend, 'rewrite')
    assert receipt['workspace_changed']
    snapshot = diff_snapshot(root)
    write(store, run, 'repair.re_review.read', {**snapshot, 'truncated': False})
    report = finalize(store, run, snapshot, 'repair.re_review', 'No issues', receipt)
    assert report['outcome'] == 'incomplete' and 'verification_failed_or_stale' in report['reasons']


def test_expired_worker_cannot_write_review_receipts(store):
    from agent.orchestration.review_evidence import write, read
    from agent.orchestration.store import LeaseLost
    run = store.submit(repo_url='https://github.com/example/fixture.git', prompt='review')
    run = store.claim('worker')
    with pytest.raises(LeaseLost):
        write(store, {**run, 'lease_token': 'old-token'}, 'report', {'outcome': 'no_findings'})
    assert read(store, run['run_id'], 'report') is None


def test_resume_after_completed_review_skips_verification_and_reviewer(repo, store, monkeypatch):
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage
    from agent.core import runtime, model, repo_memory
    from agent import server
    from agent.backends.docker import DockerBackend
    from agent.orchestration.review_flow import run_flow
    from agent.orchestration.review_evidence import write
    root, _ = repo
    run = store.submit(repo_url='https://github.com/example/fixture.git', prompt='repair')
    run = store.claim('worker')
    snapshot = diff_snapshot(root)
    spec = {'kind': 'repair', 'digest': snapshot['digest'], 'head_sha': snapshot['head'],
            'findings': [], 'verification_command': 'check'}
    counts = {'execute': 0, 'review': 0, 'implement': 0}
    class Backend(DockerBackend):
        def __init__(self):
            pass
        def execute(self, *args, **kwargs):
            counts['execute'] += 1
            return SimpleNamespace(exit_code=0, output='passed')
    class Reviewer:
        def invoke(self, *args, **kwargs):
            counts['review'] += 1
            write(store, run, 'repair.re_review.read', {**snapshot, 'truncated': False})
            return {'messages': [AIMessage(content='No issues')]}
    def implement(**kwargs):
        counts['implement'] += 1
        return {'messages': [{'type': 'ai', 'content': 'Implemented'}]}
    checkpoint = SimpleNamespace(get_state=lambda _: SimpleNamespace(values={'messages': [AIMessage(content='Implemented')]}, next=()))
    monkeypatch.setattr(server, 'ensure_backend_for_thread', lambda _: Backend())
    monkeypatch.setattr(server, 'readonly_subagent', lambda *args, **kwargs: {'runnable': Reviewer()})
    monkeypatch.setattr(model, 'make_main_model', lambda: None)
    monkeypatch.setattr(runtime, '_build_agent_for_runtime', lambda **kwargs: checkpoint)
    monkeypatch.setattr(runtime, 'run_agent_with_event_stream', implement)
    monkeypatch.setattr(repo_memory, 'save_task_memory', lambda *args: None)
    run_flow(store, run, spec, root)
    run_flow(store, {**run, 'resume_checkpoint': 1}, spec, root)
    assert counts == {'execute': 1, 'review': 1, 'implement': 1}
