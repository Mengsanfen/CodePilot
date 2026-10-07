from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from agent.orchestration.service import get_run_store, public_run
from agent.orchestration.store import RunConflict
from agent.orchestration.reviews import findings, confirm_repairs

review_router = APIRouter(prefix='/api')


class ReviewRequest(BaseModel):
    repo_url: str
    pr_number: int = Field(gt=0)


@review_router.post('/reviews', status_code=202)
def submit_review(body: ReviewRequest, idempotency_key: str | None = Header(None, max_length=200)):
    from agent.tools.github_review import pull_request_snapshot
    from agent.orchestration.transport import notify_run
    try:
        spec = pull_request_snapshot(body.repo_url, body.pr_number)
        run = get_run_store().submit(repo_url=spec['repo_url'],
            prompt=f"只读审查 PR #{body.pr_number}，记录有证据、可定位的缺陷，不修改文件。",
            request_key=idempotency_key, spec=spec)
        notify_run(run['run_id'])
        return public_run(run)
    except RunConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@review_router.get('/runs/{run_id}/findings')
def get_findings(run_id: str):
    store = get_run_store()
    if not store.get_run(run_id):
        raise HTTPException(404, 'run not found')
    return findings(store, run_id)


class RepairRequest(BaseModel):
    finding_ids: list[str] = Field(min_length=1, max_length=50)
    expected_digest: str
    verification_command: str = Field(min_length=1, max_length=2000)


@review_router.get('/runs/{run_id}/review-report')
def review_report(run_id: str):
    from agent.orchestration.review_evidence import read
    store = get_run_store()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, 'run not found')
    return {'run_id': run_id, 'execution_status': run['status'], 'report': read(store, run_id, 'report')}


@review_router.post('/runs/{run_id}/repairs', status_code=202)
def repair_findings(run_id: str, body: RepairRequest):
    from agent.orchestration.transport import notify_run
    try:
        run = confirm_repairs(get_run_store(), run_id, body.finding_ids, body.expected_digest, body.verification_command)
        notify_run(run['run_id'])
        return public_run(run)
    except KeyError as exc:
        raise HTTPException(404, 'run not found') from exc
    except RunConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@review_router.get('/runs/{run_id}/metrics')
def run_metrics(run_id: str):
    store = get_run_store()
    if not store.get_run(run_id):
        raise HTTPException(404, 'run not found')
    cursor, usage, stages, durations = 0, {}, [], []
    while batch := store.events(run_id, cursor, 1000):
        for item in batch:
            p = item['payload']
            if item['event'] == 'model.usage':
                usage[p['call_id']] = p
            elif item['event'].startswith('stage.'):
                stages.append({'event': item['event'], **p})
            elif item['event'] == 'run.metrics':
                durations.append(p)
        cursor = batch[-1]['seq']
    return {'run_id': run_id, 'model_calls': len(usage),
            'input_tokens': sum(p['input_tokens'] for p in usage.values()),
            'output_tokens': sum(p['output_tokens'] for p in usage.values()),
            'missing_usage_calls': sum(not p['usage_available'] for p in usage.values()),
            'stages': stages, 'attempts': durations, 'trace_context': store.trace_context(run_id)}
