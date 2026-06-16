from typing import List

from pydantic import BaseModel, Field


class WorkerResult(BaseModel):
    """Implementation evidence produced by the active worker agent."""

    task_id: str
    diff: str
    linter_output: str
    changed_files: List[str] = Field(default_factory=list)
    final_message: str = ""
