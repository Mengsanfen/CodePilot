from __future__ import annotations

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    repo_url: str = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=20000)
    thread_id: str | None = None
