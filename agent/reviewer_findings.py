from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewFinding:
    file: str
    line: int | None
    severity: str
    title: str
    description: str


def format_findings_comment(findings: list[ReviewFinding]) -> str:
    if not findings:
        return "LX-AICODING Reviewer 未发现需要阻塞合并的问题。"

    lines = ["LX-AICODING Reviewer 发现以下问题："]
    for index, finding in enumerate(findings, start=1):
        location = finding.file if finding.line is None else f"{finding.file}:{finding.line}"
        lines.extend(
            [
                "",
                f"{index}. [{finding.severity}] {finding.title}",
                f"   位置：{location}",
                f"   风险：{finding.description}",
            ]
        )
    return "\n".join(lines)


def finding_from_row(row: dict) -> ReviewFinding:
    return ReviewFinding(
        file=row["file"],
        line=row.get("line"),
        severity=row["severity"],
        title=row["title"],
        description=row["description"],
    )
