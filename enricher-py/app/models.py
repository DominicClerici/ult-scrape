from enum import Enum

from pydantic import BaseModel


class JobStatus(str, Enum):
    PENDING = "pending"
    WORKING = "working"
    DONE = "done"
    NO_MATCH = "no_match"
    FAILED = "failed"


class Job(BaseModel):
    tab_id: str
    route: str
    status: JobStatus
    attempts: int = 0
    next_attempt_at: float = 0.0
    claimed_at: float | None = None
    worker_id: str | None = None
    query: str | None = None
    chosen_video_id: str | None = None
    last_error: str | None = None
    created_at: float
    updated_at: float
