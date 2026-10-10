from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.orchestration.service import get_run_store, public_run
from agent.orchestration.store import RunConflict, TERMINAL

run_router = APIRouter(prefix="/api/runs")


@run_router.get('/{run_id}/operations')
def operations(run_id: str):
    get_run(run_id)
    from agent.orchestration.approvals import list_operations
    try:
        return list_operations(get_run_store(), run_id)
    except RunConflict as exc:
        raise HTTPException(409, str(exc)) from exc


class ApproveRequest(BaseModel):
    expected_digest: str


@run_router.post('/{run_id}/operations/{operation_id}/approve')
def approve_operation(run_id: str, operation_id: str, body: ApproveRequest):
    from agent.orchestration.approvals import approve
    from agent.tools.scm import execute_approved
    if not any(item['operation_id'] == operation_id for item in operations(run_id)):
        raise HTTPException(404, 'operation not found')
    try:
        return approve(get_run_store(), operation_id, body.expected_digest, execute_approved)
    except RunConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        from agent.security import redact
        raise HTTPException(502, redact(str(exc))) from exc


@run_router.get("/{run_id}")
def get_run(run_id: str):
    run = get_run_store().get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return public_run(run)


@run_router.post("/{run_id}/cancel", status_code=202)
def cancel_run(run_id: str):
    try:
        return public_run(get_run_store().cancel(run_id))
    except KeyError as exc:
        raise HTTPException(404, "run not found") from exc


class ResumeRequest(BaseModel):
    external_effects_checked: bool = False


@run_router.post("/{run_id}/resume", status_code=202)
def resume_run(run_id: str, body: ResumeRequest):
    if not body.external_effects_checked:
        raise HTTPException(409, "请先核对 Git/PR 等外部操作，再明确确认恢复")
    try:
        return public_run(get_run_store().resume(run_id))
    except KeyError as exc:
        raise HTTPException(404, "run not found") from exc
    except RunConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@run_router.get("/{run_id}/events")
def events(run_id: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    get_run(run_id)
    return get_run_store().events(run_id, after, limit)


@run_router.get("/{run_id}/stream")
def stream(run_id: str, request: Request, after: int = Query(0, ge=0),
           last_event_id: str | None = Header(None)):
    get_run(run_id)
    try:
        cursor = max(after, int(last_event_id or 0))
    except ValueError as exc:
        raise HTTPException(400, "Last-Event-ID 必须是整数游标") from exc

    async def iterate():
        nonlocal cursor
        while not await request.is_disconnected():
            # Synchronous DB drivers must not block the ASGI event loop.
            batch = await asyncio.to_thread(get_run_store().events, run_id, cursor)
            for event in batch:
                cursor = event["seq"]
                yield f"id: {cursor}\nevent: {event['event']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            run = await asyncio.to_thread(get_run_store().get_run, run_id)
            if not batch and run["status"] in TERMINAL:
                yield "event: done\ndata: {}\n\n"
                break
            if not batch:
                yield ": keepalive\n\n"
                await asyncio.sleep(0.5)
    return StreamingResponse(iterate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
