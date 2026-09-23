from __future__ import annotations

import hashlib
import os


def identity() -> tuple[str, str] | None:
    run_id, token = os.environ.get("CODEPILOT_RUN_ID"), os.environ.get("CODEPILOT_LEASE_TOKEN")
    return (run_id, token) if run_id and token else None


def check_execution():
    current = identity()
    if current:
        from agent.orchestration.service import get_run_store
        get_run_store().assert_lease(*current)


def durable_event(event: str, payload: dict):
    current = identity()
    if current:
        from agent.orchestration.service import get_run_store
        get_run_store().append_event(*current, event, payload)


def external_operation(kind: str, parameters: dict, action):
    current = identity()
    if not current:
        return action()
    from agent.orchestration.service import get_run_store
    from agent.orchestration.store import encode
    operation_id = hashlib.sha256(encode([current[0], kind, parameters]).encode()).hexdigest()
    store = get_run_store()
    cached = store.operation_start(*current, operation_id)
    if cached is not None:
        return cached
    result = action()
    store.operation_finish(*current, operation_id, result)
    return result
