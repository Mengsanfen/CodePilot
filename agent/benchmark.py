"""Versioned benchmark contracts and metrics; no model or repository execution."""
from __future__ import annotations

from enum import StrEnum
from statistics import median
from typing import Literal
import math

from pydantic import BaseModel, ConfigDict, Field


class Failure(StrEnum):
    MODEL_REASONING = 'MODEL_REASONING'
    TOOL_ARGUMENT = 'TOOL_ARGUMENT'
    TOOL_EXECUTION = 'TOOL_EXECUTION'
    CONTEXT_MISSING = 'CONTEXT_MISSING'
    TIMEOUT = 'TIMEOUT'
    SANDBOX = 'SANDBOX'
    DEPENDENCY = 'DEPENDENCY'
    CHECKPOINT = 'CHECKPOINT'
    LEASE_LOST = 'LEASE_LOST'
    EXTERNAL_EFFECT_UNCERTAIN = 'EXTERNAL_EFFECT_UNCERTAIN'
    VERIFICATION_FAILED = 'VERIFICATION_FAILED'
    REVIEW_FALSE_POSITIVE = 'REVIEW_FALSE_POSITIVE'


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Budget(StrictModel):
    max_seconds: int = Field(ge=10, le=600)
    max_model_calls: int = Field(ge=1, le=30)


class Edit(StrictModel):
    path: str
    before: str
    after: str


class Verification(StrictModel):
    command: str
    hidden_files: list[str]


class BenchmarkTask(StrictModel):
    id: str = Field(pattern=r'^[a-z0-9-]+$')
    repo: str
    base_sha: str = Field(pattern=r'^[a-f0-9]{40}$')
    type: Literal['bugfix', 'feature', 'testgen', 'review', 'repoqa']
    prompt: str
    verification: Verification
    budget: Budget
    input_edits: list[Edit] = []
    oracle_edits: list[Edit] = []
    mutants: list[Edit] = []
    expected_findings: list[dict] = []
    qa: dict | None = None
    oracle_test: str | None = None


def summarize(records: list[dict]) -> dict:
    coding = [r for r in records if r['type'] in {'bugfix', 'feature', 'testgen'}]
    review = [r for r in records if r['type'] == 'review']
    clean = [r for r in review if r.get('expected_defects') == 0 and r.get('delivered')]
    times = sorted(r['seconds'] for r in records)
    tp = sum(r.get('true_positives', 0) for r in review)
    fp = sum(r.get('false_positives', 0) for r in review)
    fn = sum(r.get('false_negatives', 0) for r in review)
    ratio = lambda n, d: n / d if d else None
    failures = {}
    for record in records:
        if record.get('failure'):
            key = Failure(record['failure']).value
            failures[key] = failures.get(key, 0) + 1
    return {
        'cases': len(records), 'passed': sum(r['passed'] for r in records),
        'task_success_rate': ratio(sum(r['passed'] for r in records), len(records)),
        'coding_success_rate': ratio(sum(r['passed'] for r in coding), len(coding)),
        'first_attempt_success_rate': ratio(sum(r['passed'] and r.get('attempt', 1) == 1 for r in records), len(records)),
        'median_latency_seconds': median(times) if times else None,
        'p95_latency_seconds': times[max(0, math.ceil(len(times) * .95) - 1)] if times else None,
        **{key: sum(r.get(key, 0) for r in records) for key in
           ('input_tokens', 'output_tokens', 'model_calls', 'tool_calls', 'files_read')},
        'usage_complete': all(r.get('usage_complete', False) for r in records),
        'review_location_precision': ratio(tp, tp + fp),
        'review_location_recall': ratio(tp, tp + fn),
        'review_clean_case_false_positive_rate': ratio(sum(r.get('false_positives', 0) > 0 for r in clean), len(clean)),
        'review_delivery_rate': ratio(sum(r.get('delivered', False) for r in review), len(review)),
        'review_valid_finding_rate': None,
        'review_valid_finding_note': 'Location matches are not semantic adjudication. Evidence-bound judgments required.',
        'failure_counts': failures,
        'reliability': None,  # Dedicated fault experiments, never inferred from coding passes.
    }
