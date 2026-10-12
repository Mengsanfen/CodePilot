"""Faults cross real process/DB/container boundaries; provider acceptance is simulated."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

import httpx
import pytest

from agent.orchestration.store import RunStore, LeaseLost, RunConflict
from agent.orchestration.approvals import propose, approve, list_operations
from agent.orchestration.processes import process_options, attach_process_tree, stop_process_tree

FIXTURE = Path(__file__).with_name('fixture_runner.py')


def eventually(predicate, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Fixture did not reach its expected state before deadline')


@pytest.fixture(params=['sqlite', 'postgres'])
def crash_store(request, tmp_path):
    if request.param == 'sqlite':
        store = RunStore(tmp_path / 'runs.sqlite')
        yield store
        store.close()
        return
    url = os.environ.get('TEST_POSTGRES_URL')
    if not url:
        pytest.skip('TEST_POSTGRES_URL not configured')
    import psycopg
    schema = 'fault_' + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True, connect_timeout=5) as admin:
        admin.execute(f'CREATE SCHEMA {schema}')
        try:
            target = url + ('&' if '?' in url else '?') + 'options=-csearch_path%3D' + schema
            store = RunStore(target)
            try:
                yield store
            finally:
                store.close()
        finally:
            admin.execute(f'DROP SCHEMA {schema} CASCADE')


def test_worker_crash_checkpoint_resume(crash_store, tmp_path, monkeypatch, record_property):
    store = crash_store
    from agent.api import run_routes
    from agent.app import app
    from fastapi.testclient import TestClient
    monkeypatch.setattr(run_routes, 'get_run_store', lambda: store)
    client = TestClient(app)
    run = store.submit(repo_url='https://github.com/example/fault-fixture', prompt='checkpoint fixture')
    env = {**os.environ, 'CODEPILOT_DATABASE_URL': str(store.db_path), 'CODEPILOT_LOAD_ENV': 'false',
           'CODEPILOT_LOG_DIR': str(tmp_path / 'logs'), 'PYTHONUTF8': '1'}
    processes = []
    def launch():
        with (tmp_path / 'supervisor.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, str(FIXTURE), 'worker', str(tmp_path)],
                env=env, stdout=log, stderr=subprocess.STDOUT, **process_options())
        attach_process_tree(process)
        processes.append(process)
        return process
    try:
        first = launch()
        eventually(lambda: (tmp_path / 'ready-1.json').exists())
        receipt = json.loads((tmp_path / 'ready-1.json').read_text(encoding='utf-8'))
        # Wait for a committed checkpoint, not just a node's in-memory state.
        from agent.core.persistence import make_checkpointer
        monkeypatch.setenv('CODEPILOT_DATABASE_URL', str(store.db_path))
        saver = make_checkpointer(tmp_path / 'checkpoint.sqlite')
        try:
            config = {'configurable': {'thread_id': run['thread_id']}}
            eventually(lambda: (saver.get(config) or {}).get('channel_values', {}).get('prepared'))
        finally:
            saver.conn.close()
        old_token = receipt['lease_token']
        crashed_at = time.monotonic()
        # Windows venv launchers may have a different PID. Kill the actual
        # supervisor whose PID is recorded by Worker.owner, not a launcher.
        owner_pid = int(receipt['owner'].split('-', 1)[0])
        os.kill(owner_pid, signal.SIGTERM if os.name == 'nt' else signal.SIGKILL)
        first.wait(timeout=10)
        eventually(lambda: store.recover_expired() == 1, timeout=10)
        assert store.get_run(run['run_id'])['status'] == 'interrupted'
        endpoint = f"/api/runs/{run['run_id']}"
        assert client.post(endpoint + '/resume', json={'external_effects_checked': False}).status_code == 409
        # Actual grace period; no database clock manipulation.
        eventually(lambda: store._time() > store.get_run(run['run_id'])['lease_until'] + 5, timeout=10)
        assert client.post(endpoint + '/resume', json={'external_effects_checked': True}).status_code == 202
        second = launch()
        eventually(lambda: (tmp_path / 'ready-2.json').exists())
        new_token = store.get_run(run['run_id'])['lease_token']
        assert new_token != old_token
        with pytest.raises(LeaseLost):
            store.finish(run['run_id'], old_token, 'completed')
        with pytest.raises(LeaseLost):
            store.append_event(run['run_id'], old_token, 'stale-write', {})
        (tmp_path / 'finish').write_text('release', encoding='utf-8')
        assert second.wait(timeout=20) == 0
        assert store.get_run(run['run_id'])['status'] == 'completed'
        assert (tmp_path / 'preparations.txt').read_text(encoding='utf-8').splitlines() == ['prepared']
        assert len(list_operations(store, run['run_id'])) == 1
        events = client.get(endpoint + '/events').json()
        assert sum(e['event'] == 'operation.proposed' for e in events) == 1
        assert [e['payload']['attempt'] for e in events if e['event'] == 'run.started'] == [1, 2]
        replay = []
        cursor = 0
        while batch := client.get(endpoint + '/events', params={'after': cursor, 'limit': 2}).json():
            replay.extend(batch)
            cursor = batch[-1]['seq']
        assert replay == events
        cursor = events[len(events) // 2]['seq']
        stream = client.get(endpoint + '/stream', headers={'Last-Event-ID': str(cursor)})
        streamed = [payload for line in stream.text.splitlines() if line.startswith('data: ')
                    and 'seq' in (payload := json.loads(line[6:]))]
        assert streamed == [event for event in events if event['seq'] > cursor]
        for key, value in {'resume_success': True, 'duplicate_proposal_count': 0,
                           'lease_fencing_violation_count': 0, 'event_replay_consistency': True,
                           'recovery_seconds': round(time.monotonic() - crashed_at, 3),
                           'database': 'postgres' if store.is_postgres else 'sqlite'}.items():
            record_property(key, value)
    finally:
        for process in processes:
            stop_process_tree(process)
        client.close()


def test_redis_notification_loss_keeps_database_task(store, monkeypatch, record_property):
    from agent.orchestration import transport, service
    from agent.orchestration.worker import Worker
    attempts = []
    class LostRedis:
        def xadd(self, *args, **kwargs):
            attempts.append('xadd')
            raise ConnectionError('injected notification loss')
    monkeypatch.setattr(transport, 'client', lambda: LostRedis())
    monkeypatch.setattr(service, 'get_run_store', lambda: store)
    run = service.submit_run(repo_url='https://github.com/example/fault-fixture', prompt='durable queue')
    assert attempts == ['xadd']
    assert store.get_run(run['run_id'])['status'] == 'queued'
    # Worker claims the real DB row without receiving any Redis message.
    assert Worker(store, command=[sys.executable, '-c', 'pass']).run_once()
    assert store.get_run(run['run_id'])['status'] == 'completed'
    record_property('notify_failed_but_completed', True)


def test_pr_accepted_then_response_timeout_is_not_retried(store, monkeypatch, record_property):
    from agent.tools import scm
    accepted = []
    head = 'a' * 40
    def provider(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'object': {'sha': head}} if '/git/ref/' in request.url.path else [])
        assert request.url.path.endswith('/pulls')
        accepted.append(json.loads(request.content))
        raise httpx.ReadTimeout('provider accepted request, response lost', request=request)
    monkeypatch.setattr(scm, 'github_client', lambda: httpx.Client(base_url='https://api.github.com',
                                                               transport=httpx.MockTransport(provider)))
    run = store.submit(repo_url='https://github.com/example/fault-fixture', prompt='PR fixture')
    claimed = store.claim('fault-worker')
    op = propose(store, run['run_id'], claimed['lease_token'], 'create_pr',
                 {'provider': 'github', 'owner': 'example', 'repo': 'fault-fixture', 'head': 'codepilot/fault',
                  'base': 'main', 'title': 'fixture', 'body': '', 'expected_head_sha': head})['operation_id']
    store.finish(run['run_id'], claimed['lease_token'], 'completed')
    with pytest.raises(httpx.ReadTimeout):
        approve(store, op, op, scm.execute_approved)
    assert list_operations(store, run['run_id'])[0]['status'] == 'uncertain'
    with pytest.raises(RunConflict):
        approve(store, op, op, scm.execute_approved)
    assert len(accepted) == 1
    record_property('provider_accepted_posts', len(accepted))
    record_property('duplicate_external_effect_count', len(accepted) - 1)
    record_property('uncertain_blocks_retry', True)


@pytest.mark.skipif(not os.environ.get('TEST_DOCKER_IMAGE'), reason='TEST_DOCKER_IMAGE not configured')
def test_sandbox_secret_network_and_session_negative(tmp_path, monkeypatch, record_property):
    from agent.backends.docker import DockerBackend
    monkeypatch.setenv('CODEPILOT_SANDBOX_IMAGE', os.environ['TEST_DOCKER_IMAGE'])
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-secret-not-for-container')
    monkeypatch.delenv('CODEPILOT_HOST_WORKSPACE', raising=False)
    (tmp_path / '.env').write_text('HOST_ONLY=synthetic-canary', encoding='utf-8')
    other = tmp_path / 'other-session'; other.mkdir()
    (other / 'canary.txt').write_text('other-session-only', encoding='utf-8')
    backend = DockerBackend(tmp_path / 'current-session')
    if os.name != 'nt':
        backend.projects_dir.chmod(0o777)
    probe = '''import json, os, socket
from pathlib import Path
results = {}
for name, path in {'host_env': '/projects/../../.env', 'other_session': '/projects/../../other-session/canary.txt',
                   'docker_socket': '/var/run/docker.sock'}.items():
    try:
        Path(path).read_bytes()
    except OSError:
        results[name] = True
    else:
        results[name] = False
results['secret_env'] = 'DEEPSEEK_API_KEY' not in os.environ
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError:
    results['outbound_network'] = True
else:
    results['outbound_network'] = False
results['non_root'] = os.getuid() == 65534
try:
    Path('/root-write-test').write_text('must fail')
except OSError:
    results['readonly_root'] = True
else:
    results['readonly_root'] = False
assert all(results.values()), results
print(json.dumps(results))
'''
    assert backend.write('/projects/probe.py', probe).error is None
    result = backend.execute('python /projects/probe.py', timeout=15)
    assert result.exit_code == 0, result.output
    results = json.loads(result.output)
    assert len(results) == 7 and all(results.values())
    for key, value in results.items():
        record_property('expected_denied_' + key, value)
