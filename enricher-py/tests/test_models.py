from app.errors import PermanentEnrichError, TransientEnrichError
from app.models import Job, JobStatus


def test_status_values():
    assert JobStatus.PENDING == "pending"
    assert {s.value for s in JobStatus} == {
        "pending", "working", "done", "no_match", "failed"
    }


def test_job_model_defaults():
    j = Job(tab_id="eagles/hotel-california-guitar-pro-382996",
            route="eagles/hotel-california-guitar-pro-382996",
            status=JobStatus.PENDING, created_at=1.0, updated_at=1.0)
    assert j.attempts == 0
    assert j.claimed_at is None


def test_error_hierarchy():
    assert issubclass(TransientEnrichError, Exception)
    assert issubclass(PermanentEnrichError, Exception)
