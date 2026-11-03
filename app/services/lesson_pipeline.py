import json
import logging
from pathlib import Path

from app.config import settings
from app.database import SessionLocal
from app.models import LessonStep, Tutorial
from app.services import jobs
from app.services.lesson_analysis import STAGES, analyze_tutorial
from app.services.meta_client import public_error

logger = logging.getLogger("skillow.pipeline")


def native_url_for(video_path: str, demo_url: str | None = None) -> str | None:
    if demo_url:
        return demo_url
    if settings.public_base_url:
        return f"{settings.public_base_url.rstrip('/')}/media/videos/{Path(video_path).name}"
    return None


def process_tutorial(tutorial_id: int, native_url: str | None, domain_hint: str, job: jobs.Job):
    db = SessionLocal()
    try:
        tutorial = db.get(Tutorial, tutorial_id)
        if not tutorial:
            return
        tutorial.status, tutorial.error = "processing", None
        db.commit()
        work_dir = settings.storage_dir / "frames" / str(tutorial.id)
        for stale in work_dir.glob("frame_*.jpg"):
            stale.unlink(missing_ok=True)
        try:
            transcript, lesson, duration = analyze_tutorial(
                tutorial.video_path, tutorial.title, native_url, domain_hint, work_dir, job)
        except Exception as exc:
            logger.warning("Tutorial %s failed: %s", tutorial_id, exc, exc_info=True)
            tutorial.status, tutorial.error = "failed", public_error(exc)
            db.commit()
            raise
        tutorial.duration, tutorial.transcript = duration, transcript
        tutorial.lesson_json = json.dumps(lesson, ensure_ascii=False)
        tutorial.title = lesson.get("title") or tutorial.title
        tutorial.steps.clear()
        for step in lesson["steps"]:
            db.add(LessonStep(
                tutorial_id=tutorial.id, step_number=step["step_number"], title=step["title"],
                instruction=step["instruction"], start_seconds=step["start_seconds"],
                end_seconds=step["end_seconds"], visual_observation=step["visual_observation"],
                safety="\n".join(step["safety"])))
        tutorial.status = "completed"
        db.commit()
    finally:
        db.close()


def start_processing(tutorial_id: int, native_url: str | None, domain_hint: str) -> jobs.Job:
    return jobs.start(("tutorial", tutorial_id), STAGES,
                      lambda job: process_tutorial(tutorial_id, native_url, domain_hint, job))
