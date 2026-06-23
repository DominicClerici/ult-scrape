from enum import Enum

from pydantic import BaseModel


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class ServiceState(str, Enum):
    STARTING = "starting"
    LOGGING_IN = "logging_in"
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class Job(BaseModel):
    id: str
    tab_id: str
    url: str
    status: JobStatus
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    next_attempt_at: float = 0.0
    force: bool = False
    created_at: float
    updated_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    output_dir: str | None = None


class EnqueueRequest(BaseModel):
    url_or_route: str
    priority: int = 0
    force: bool = False


class BulkEnqueueRequest(BaseModel):
    items: list[EnqueueRequest]


class StatusResponse(BaseModel):
    state: ServiceState
    current_job_id: str | None
    queue_depth: int
    counts: dict[str, int]
    paused: bool
    logged_in: bool
