from __future__ import annotations

from agent.core.graph import get_store
from agent.reviewer_findings import ReviewFinding, finding_from_row, format_findings_comment


def list_findings(thread_id: str):
    return get_store().list_findings(thread_id)


def list_review_findings(thread_id: str) -> list[ReviewFinding]:
    return [finding_from_row(row) for row in list_findings(thread_id)]


def format_review_comment(thread_id: str) -> str:
    return format_findings_comment(list_review_findings(thread_id))
