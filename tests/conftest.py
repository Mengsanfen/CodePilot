import os
import tempfile
from pathlib import Path
import pytest

_root = Path(tempfile.mkdtemp(prefix="codepilot-tests-"))
os.environ.update({"CODEPILOT_LOAD_ENV": "false", "CODEPILOT_EMBEDDED_WORKER": "false",
                   "CODEPILOT_DATA_DIR": str(_root / "data"),
                   "CODEPILOT_LOG_DIR": str(_root / "logs"),
                   "AI_WORKSPACE_ROOT": str(_root / "workspace")})
for key in ("CODEPILOT_DATABASE_URL", "CODEPILOT_RUN_ID", "CODEPILOT_LEASE_TOKEN"):
    os.environ.pop(key, None)


@pytest.fixture
def store(tmp_path):
    from agent.orchestration.store import RunStore
    value = RunStore(tmp_path / "store.sqlite")
    yield value
    value.close()


@pytest.fixture
def client(store, monkeypatch):
    from agent.core import graph, events
    from agent.orchestration import service
    service.get_run_store.cache_clear()
    monkeypatch.setattr(service, "DATABASE_TARGET", store.db_path)
    monkeypatch.setattr(graph, "_store", store)
    monkeypatch.setattr(events, "_event_store", store)
    from agent.app import app
    from fastapi.testclient import TestClient
    with TestClient(app) as value:
        yield value
    service.get_run_store().close()
    service.get_run_store.cache_clear()
