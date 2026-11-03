import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="skillow-job")
_lock = threading.Lock()
_jobs: dict[str, "Job"] = {}
_by_key: dict[tuple, str] = {}
_TTL = 1800


class Job:
    def __init__(self, key: tuple, stages: list[tuple[str, str]]):
        self.id = uuid.uuid4().hex
        self.key = key
        self.stages = [{"key": k, "label": label, "state": "pending", "detail": ""} for k, label in stages]
        self.status = "running"
        self.result = None
        self.error = None
        self.finished = None
        self._lock = threading.Lock()

    def stage(self, key: str, state: str, detail: str = ""):
        with self._lock:
            for item in self.stages:
                if item["key"] == key:
                    item["state"] = state
                    if detail:
                        item["detail"] = detail

    def snapshot(self) -> dict:
        with self._lock:
            return {"job_id": self.id, "status": self.status, "stages": [dict(s) for s in self.stages],
                    "result": self.result, "error": self.error}


def _purge():
    cutoff = time.time() - _TTL
    for job_id in [j for j, job in _jobs.items() if job.finished and job.finished < cutoff]:
        job = _jobs.pop(job_id)
        if _by_key.get(job.key) == job_id:
            _by_key.pop(job.key, None)


def start(key: tuple, stages: list[tuple[str, str]], fn) -> Job:
    with _lock:
        _purge()
        existing = _jobs.get(_by_key.get(key, ""))
        if existing and existing.status == "running":
            return existing
        job = Job(key, stages)
        _jobs[job.id] = job
        _by_key[key] = job.id

    def run():
        try:
            job.result = fn(job)
            job.status = "done"
        except Exception as exc:
            from app.services.meta_client import public_error
            job.error = public_error(exc)
            job.status = "failed"
            for item in job.stages:
                if item["state"] == "running":
                    item["state"] = "failed"
        finally:
            job.finished = time.time()

    _executor.submit(run)
    return job


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def running_for(key: tuple) -> Job | None:
    with _lock:
        job = _jobs.get(_by_key.get(key, ""))
        return job if job and job.status == "running" else None
