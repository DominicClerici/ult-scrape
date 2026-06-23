from app.models import (
    EnqueueRequest,
    Job,
    JobStatus,
    ServiceState,
    StatusResponse,
)


def test_job_status_values():
    assert JobStatus.QUEUED == "queued"
    assert JobStatus.SUCCEEDED == "succeeded"
    assert ServiceState.RUNNING == "running"


def test_enqueue_request_defaults():
    req = EnqueueRequest(url_or_route="a/b-1")
    assert req.priority == 0
    assert req.force is False


def test_job_round_trip():
    job = Job(
        id="x",
        tab_id="a/b-1",
        url="https://tabs.ultimate-guitar.com/tab/a/b-1",
        status=JobStatus.QUEUED,
        priority=0,
        attempts=0,
        max_attempts=3,
        next_attempt_at=0.0,
        force=False,
        created_at=1.0,
        updated_at=1.0,
    )
    assert job.started_at is None
    assert job.status is JobStatus.QUEUED


def test_status_response():
    resp = StatusResponse(
        state=ServiceState.IDLE,
        current_job_id=None,
        queue_depth=2,
        counts={"queued": 2},
        paused=False,
        logged_in=True,
    )
    assert resp.queue_depth == 2
