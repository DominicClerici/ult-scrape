from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.models import (
    BulkEnqueueRequest,
    DiscoveryRun,
    DiscoveryStartRequest,
    EnqueueRequest,
    Job,
    StatusResponse,
)
from app.normalize import normalize_tab

router = APIRouter()


def _repo(request: Request):
    return request.app.state.repo


def _worker(request: Request):
    return request.app.state.worker


def _settings(request: Request):
    return request.app.state.settings


async def require_api_key(
    request: Request, x_api_key: str | None = Header(default=None)
):
    key = request.app.state.settings.api_key
    if key and x_api_key != key:
        raise HTTPException(status_code=401, detail="invalid api key")


@router.get("/healthz")
async def healthz():
    return {"ok": True}


@router.get("/status", response_model=StatusResponse)
async def status(request: Request, _=Depends(require_api_key)):
    repo, worker = _repo(request), _worker(request)
    return StatusResponse(
        state=worker.state,
        current_job_id=worker.current_job_id,
        queue_depth=await repo.queue_depth(),
        counts=await repo.counts(),
        paused=await repo.is_paused(),
        logged_in=await worker.browser.is_logged_in(),
    )


@router.get("/jobs", response_model=list[Job])
async def list_jobs(
    request: Request, status: str | None = None,
    limit: int = 50, offset: int = 0, _=Depends(require_api_key),
):
    return await _repo(request).list(status=status, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str, request: Request, _=Depends(require_api_key)):
    job = await _repo(request).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/jobs", response_model=Job)
async def enqueue(
    req: EnqueueRequest, request: Request, _=Depends(require_api_key)
):
    try:
        tab_id, url = normalize_tab(req.url_or_route)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    repo, worker, settings = _repo(request), _worker(request), _settings(request)
    job = await repo.enqueue(
        tab_id=tab_id, url=url, priority=req.priority,
        force=req.force, max_attempts=settings.max_attempts,
    )
    worker.notify_enqueued()
    return job


@router.post("/jobs/bulk", response_model=list[Job])
async def enqueue_bulk(
    req: BulkEnqueueRequest, request: Request, _=Depends(require_api_key)
):
    repo, worker, settings = _repo(request), _worker(request), _settings(request)
    out = []
    for item in req.items:
        try:
            tab_id, url = normalize_tab(item.url_or_route)
        except ValueError:
            continue
        out.append(await repo.enqueue(
            tab_id=tab_id, url=url, priority=item.priority,
            force=item.force, max_attempts=settings.max_attempts,
        ))
    worker.notify_enqueued()
    return out


@router.delete("/jobs")
async def clear_queue(request: Request, _=Depends(require_api_key)):
    count = await _repo(request).cancel_all_queued()
    return {"canceled": count}


@router.delete("/jobs/{job_id}")
async def dequeue(job_id: str, request: Request, _=Depends(require_api_key)):
    repo = _repo(request)
    if await repo.get(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not await repo.cancel(job_id):
        raise HTTPException(status_code=409, detail="job is not queued")
    return {"canceled": job_id}


@router.post("/jobs/{job_id}/retry", response_model=Job)
async def retry(job_id: str, request: Request, _=Depends(require_api_key)):
    repo, worker = _repo(request), _worker(request)
    if await repo.get(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not await repo.retry(job_id):
        raise HTTPException(status_code=409, detail="job is not failed")
    worker.notify_enqueued()
    return await repo.get(job_id)


@router.post("/pause")
async def pause(request: Request, _=Depends(require_api_key)):
    await _repo(request).set_paused(True)
    return {"paused": True}


@router.post("/resume")
async def resume(request: Request, _=Depends(require_api_key)):
    await _repo(request).set_paused(False)
    _worker(request).request_resume()
    return {"paused": False}


@router.post("/discover", response_model=DiscoveryRun)
async def discover_start(
    req: DiscoveryStartRequest, request: Request, _=Depends(require_api_key)
):
    repo, worker = _repo(request), _worker(request)
    if await repo.count_active_jobs() > 0:
        raise HTTPException(status_code=409, detail="queue not empty")
    params = req.model_dump(exclude_none=True)
    run = await repo.request_discovery(params)
    if run is None:
        raise HTTPException(status_code=409, detail="discovery already active")
    worker.notify_enqueued()
    return run


@router.get("/discover", response_model=list[DiscoveryRun])
async def discover_list(request: Request, limit: int = 20, _=Depends(require_api_key)):
    return await _repo(request).list_discovery_runs(limit=limit)


@router.post("/discover/enqueue", response_model=list[Job])
async def discover_enqueue(request: Request, _=Depends(require_api_key)):
    repo, worker, settings = _repo(request), _worker(request), _settings(request)
    routes = await repo.discovered_routes(exclude_succeeded=True)
    out = []
    for tab_id, url in routes:
        out.append(await repo.enqueue(
            tab_id=tab_id, url=url, max_attempts=settings.max_attempts,
        ))
    if out:
        worker.notify_enqueued()
    return out


@router.get("/discover/{run_id}", response_model=DiscoveryRun)
async def discover_get(run_id: str, request: Request, _=Depends(require_api_key)):
    run = await _repo(request).get_discovery_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="discovery run not found")
    return run


@router.post("/discover/{run_id}/cancel")
async def discover_cancel(run_id: str, request: Request, _=Depends(require_api_key)):
    repo = _repo(request)
    if await repo.get_discovery_run(run_id) is None:
        raise HTTPException(status_code=404, detail="discovery run not found")
    if not await repo.request_discovery_cancel(run_id):
        raise HTTPException(status_code=409, detail="discovery run not cancelable")
    return {"canceled": run_id}
