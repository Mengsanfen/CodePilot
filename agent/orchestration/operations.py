"""Typed publication records. Hash the stored envelope before using its contents."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from enum import StrEnum
from typing import Any

from agent.orchestration.store import RunConflict, encode


class OperationKind(StrEnum):
    GIT_PUSH = 'git_push'
    CREATE_PR = 'create_pr'
    COMMENT_PR = 'comment_pr'


class OperationStatus(StrEnum):
    PROPOSED = 'proposed'
    APPROVED = 'approved'
    EXECUTING = 'executing'
    COMPLETED = 'completed'
    UNCERTAIN = 'uncertain'


def kind_of(value: str) -> OperationKind:
    # Legacy records retain their raw kind in the digest; never rewrite that hash.
    return OperationKind('comment_pr' if value == 'post_comment' else value)


def digest(run_id: str, kind: str, parameters: dict) -> str:
    return hashlib.sha256(encode([run_id, kind, parameters]).encode()).hexdigest()


@dataclass(frozen=True)
class Operation:
    operation_id: str
    run_id: str
    kind: OperationKind
    parameters: dict[str, Any]
    expected_digest: str
    status: OperationStatus
    result: dict | None

    @classmethod
    def from_row(cls, row) -> Operation:
        parameters = json.loads(row['parameters'])
        expected = digest(row['run_id'], row['kind'], parameters)
        if row['operation_id'] != expected:
            raise RunConflict('操作记录的参数指纹已改变，拒绝执行；请核对数据完整性')
        try:
            return cls(row['operation_id'], row['run_id'], kind_of(row['kind']), parameters,
                       expected, OperationStatus('proposed' if row['status'] == 'pending' else row['status']),
                       json.loads(row['result']) if row['result'] else None)
        except ValueError as exc:
            raise RunConflict('未知操作类型或状态，拒绝执行') from exc

    def public(self) -> dict:
        return asdict(self)
