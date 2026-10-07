"""Opt-in integration tests against disposable PostgreSQL/Redis instances."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest


@pytest.mark.skipif(not os.environ.get('TEST_DOCKER_IMAGE'), reason='TEST_DOCKER_IMAGE not configured')
def test_real_container_isolation_and_writable_project(tmp_path, monkeypatch):
    from agent.backends.docker import DockerBackend
    monkeypatch.setenv('CODEPILOT_SANDBOX_IMAGE', os.environ['TEST_DOCKER_IMAGE'])
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'sentinel-must-not-enter-sandbox')
    backend = DockerBackend(tmp_path / 'sandbox')
    if os.name != 'nt':
        # Disposable fixture mount must be writable by the container's fixed UID.
        backend.projects_dir.chmod(0o777)
    backend.write('/projects/probe.py', '''import os, socket
from pathlib import Path
assert os.getuid() == 65534
assert 'DEEPSEEK_API_KEY' not in os.environ
assert not Path('/var/run/docker.sock').exists()
try:
    Path('/root-write').write_text('bad')
except OSError:
    pass
else:
    raise AssertionError('root filesystem writable')
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('network reachable')
Path('/projects/result.txt').write_text('isolated')
print('isolation verified')
''')
    result = backend.execute('python /projects/probe.py', timeout=15)
    assert result.exit_code == 0, result.output
    assert (backend.projects_dir / 'result.txt').read_text() == 'isolated'


@pytest.mark.skipif(not os.environ.get('TEST_POSTGRES_URL'), reason='TEST_POSTGRES_URL not configured')
def test_postgres_claim_fencing_and_checkpoint():
    import psycopg
    from psycopg.conninfo import make_conninfo
    from agent.orchestration.store import RunStore, LeaseLost
    from langgraph.checkpoint.postgres import PostgresSaver
    schema = 'test_' + uuid.uuid4().hex
    url = os.environ['TEST_POSTGRES_URL']
    # Each test uses a unique schema; never drop application tables.
    with psycopg.connect(url, autocommit=True, connect_timeout=5) as admin:
        admin.execute(f'CREATE SCHEMA {schema}')
        try:
            # The adapter appends server timeouts without replacing search_path from URL.
            test_url = url + ('&' if '?' in url else '?') + 'options=-csearch_path%3D' + schema
            def initialize(_):
                value = RunStore(test_url)
                value.close()
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(initialize, range(4)))
            store = RunStore(test_url)
            run = store.submit(repo_url='https://github.com/example/project.git', prompt='test')
            def claim(index):
                other = RunStore(test_url)
                try:
                    return other.claim(str(index))
                finally:
                    other.close()
            with ThreadPoolExecutor(max_workers=4) as pool:
                claims = [r for r in pool.map(claim, range(4)) if r]
            assert len(claims) == 1
            token = claims[0]['lease_token']
            from agent.orchestration.review_evidence import write, read
            write(store, claims[0], 'report', {'outcome': 'incomplete'})
            write(store, claims[0], 'report', {'outcome': 'changes_requested'})
            assert read(store, run['run_id'], 'report')['outcome'] == 'changes_requested'
            store.cancel(run['run_id'])
            store.finish(run['run_id'], token, 'completed')
            assert store.get_run(run['run_id'])['status'] == 'cancelled'
            with pytest.raises(LeaseLost):
                store.append_event(run['run_id'], token, 'late', {})
            store.close()
            with PostgresSaver.from_conn_string(test_url) as saver:
                saver.setup()
                from langgraph.checkpoint.base import empty_checkpoint
                config = {'configurable': {'thread_id': uuid.uuid4().hex, 'checkpoint_ns': ''}}
                saved = saver.put(config, empty_checkpoint(), {}, {})
                assert saver.get_tuple(saved) is not None
        finally:
            admin.execute(f'DROP SCHEMA {schema} CASCADE')


@pytest.mark.skipif(not os.environ.get('TEST_REDIS_URL'), reason='TEST_REDIS_URL not configured')
def test_redis_delivery_reclaim():
    import redis
    client = redis.Redis.from_url(os.environ['TEST_REDIS_URL'], decode_responses=True, socket_connect_timeout=3, socket_timeout=3)
    stream = 'codepilot-test-' + uuid.uuid4().hex
    try:
        client.xgroup_create(stream, 'workers', id='0', mkstream=True)
        key = client.xadd(stream, {'run_id': 'test-run'})
        assert client.xreadgroup('workers', 'dead', {stream: '>'}, count=1)
        reclaimed = client.xautoclaim(stream, 'workers', 'new', 0, '0-0')
        assert reclaimed[1][0][0] == key
        assert client.xack(stream, 'workers', key) == 1
    finally:
        client.delete(stream)
        client.close()
