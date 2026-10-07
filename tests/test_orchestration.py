import concurrent.futures
import json
import sys
import threading
import time
import pytest
from agent.orchestration.store import LeaseLost, RunConflict, RunStore
from agent.orchestration.worker import Worker

REPO = "https://github.com/example/project.git"


def submit(store, **kwargs):
    return store.submit(repo_url=REPO, prompt="分析项目", **kwargs)


def test_submission_is_durable_and_idempotent(store):
    first = submit(store, request_key="one")
    assert submit(store, request_key="one")["run_id"] == first["run_id"]
    with pytest.raises(RunConflict):
        store.submit(repo_url=REPO, prompt="different", request_key="one")
    other = RunStore(store.db_path)
    assert other.get_run(first["run_id"])["status"] == "queued"
    other.close()


def test_worker_reaps_descendants_after_parent_exits(store, tmp_path):
    marker = tmp_path / 'escaped.txt'
    child = f"import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).write_text('escaped')"
    parent = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
    submit(store)
    Worker(store, command=[sys.executable, '-c', parent]).run_once()
    time.sleep(2.2)
    assert not marker.exists()


def test_same_thread_cannot_overlap(store):
    run = submit(store)
    with pytest.raises(RunConflict):
        submit(store, thread_id=run["thread_id"])


def test_multiple_workers_claim_once(store):
    run = submit(store)
    def claim(index):
        other = RunStore(store.db_path)
        try:
            return other.claim(str(index))
        finally:
            other.close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(claim, range(6)))
    assert [r["run_id"] for r in results if r] == [run["run_id"]]


def test_expiry_fences_old_worker_and_requires_explicit_resume(store):
    run = submit(store)
    claimed = store.claim("old", lease_seconds=-10)
    assert store.recover_expired() == 1
    with pytest.raises(LeaseLost):
        store.finish(run["run_id"], claimed["lease_token"], "completed")
    assert store.claim("new") is None
    with pytest.raises(RunConflict):
        store.resume(run["run_id"])
    store._conn.execute("UPDATE execution_runs SET execution_context=? WHERE run_id=?",
                        (json.dumps({"task_kind": "planning"}), run["run_id"]))
    store._conn.commit()
    store.resume(run["run_id"])
    resumed = store.claim("new")
    assert resumed["resume_checkpoint"] == 1 and resumed["lease_token"] != claimed["lease_token"]


def test_events_are_append_only_and_replay_from_cursor(store):
    run = submit(store)
    claimed = store.claim("w")
    cursor = store.events(run["run_id"])[-1]["seq"]
    store.append_event(run["run_id"], claimed["lease_token"], "test", {"text": "a"})
    store.finish(run["run_id"], claimed["lease_token"], "completed")
    assert [e["event"] for e in store.events(run["run_id"], cursor)] == ["test", "run.completed"]


def test_cancel_queued_and_running(store):
    run = submit(store)
    assert store.cancel(run["run_id"])["status"] == "cancelled"
    assert store.claim("w") is None
    run = submit(store)
    claimed = store.claim("w")
    assert store.cancel(run["run_id"])["status"] == "cancelling"
    store.finish(run["run_id"], claimed["lease_token"], "completed")
    assert store.get_run(run["run_id"])["status"] == "cancelled"


def test_uncertain_external_write_is_not_repeated(store):
    run = submit(store)
    claimed = store.claim("w")
    args = (run["run_id"], claimed["lease_token"], "operation")
    assert store.operation_start(*args) is None
    with pytest.raises(RunConflict):
        store.operation_start(*args)
    store.operation_finish(*args, {"number": 42})
    assert store.operation_start(*args) == {"number": 42}


def test_hard_timeout_kills_blocked_child(store):
    run = submit(store)
    worker = Worker(store, timeout=0.2, command=[sys.executable, "-c", "import time; time.sleep(60)"])
    started = time.monotonic()
    worker.run_once()
    assert time.monotonic() - started < 10
    assert store.get_run(run["run_id"])["status"] == "failed"


def test_cancellation_reaches_process_supervisor(store):
    run = submit(store)
    worker = Worker(store, command=[sys.executable, "-c", "import time; time.sleep(60)"])
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    deadline = time.monotonic() + 5
    while store.get_run(run["run_id"])["status"] == "queued" and time.monotonic() < deadline:
        time.sleep(0.01)
    store.cancel(run["run_id"])
    thread.join(10)
    assert not thread.is_alive()
    assert store.get_run(run["run_id"])["status"] == "cancelled"


def test_api_submit_conflict_cancel_and_replay(client):
    body = {"repo_url": REPO, "prompt": "分析项目"}
    response = client.post("/api/tasks", json=body, headers={"Idempotency-Key": "one"})
    assert response.status_code == 202
    run = response.json()
    assert "lease_token" not in run
    assert client.post("/api/tasks", json=body, headers={"Idempotency-Key": "one"}).json()["run_id"] == run["run_id"]
    assert client.post("/api/tasks", json={**body, "thread_id": run["thread_id"]}).status_code == 409
    assert client.delete(f"/dashboard/api/threads/{run['thread_id']}").status_code == 409
    assert client.post(f"/api/runs/{run['run_id']}/cancel").json()["status"] == "cancelled"
    events = client.get(f"/api/runs/{run['run_id']}/events").json()
    stream = client.get(f"/api/runs/{run['run_id']}/stream", headers={"Last-Event-ID": str(events[0]["seq"])})
    assert "run.cancelled" in stream.text and "run.queued" not in stream.text
    assert client.get("/api/runs/missing").status_code == 404
