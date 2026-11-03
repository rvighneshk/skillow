import hashlib
import ipaddress
import socket
import json
import logging
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.models import ChatMessage, Comparison, Tutorial
from app.schemas import QuestionIn
from app.services import comparison as cmp
from app.services import jobs
from app.services import meta_client as meta
from app.services.diagrams import generate_diagram
from app.services.domains import demo_cases
from app.services.lesson_pipeline import native_url_for, start_processing
from app.services.qa import answer_question
from app.services.video_processor import probe_duration

logger = logging.getLogger("skillow.routes")
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
ALLOWED_VIDEO = {".mp4", ".mov", ".m4v"}
IMAGE_SUFFIX = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
MAX_IMAGE = 15 * 1024 * 1024
MB = 1024 * 1024
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _spool(source, folder: Path, max_bytes: int, too_big: str) -> tuple[Path, str]:
    tmp = folder / f".upload_{uuid.uuid4().hex}"
    digest, total = hashlib.sha256(), 0
    try:
        with tmp.open("wb") as out:
            while chunk := source.read(MB):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, too_big)
                digest.update(chunk)
                out.write(chunk)
        if total == 0:
            raise HTTPException(400, "The uploaded file is empty.")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, digest.hexdigest()


def _check_image(path: Path) -> str:
    try:
        with Image.open(path) as image:
            image.verify()
            fmt = image.format
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, "That file is not a readable JPG, PNG or WEBP image.")
    if fmt not in IMAGE_SUFFIX:
        raise HTTPException(400, "Upload a JPG, PNG or WEBP image.")
    return IMAGE_SUFFIX[fmt]


def _public_asset(url: str, destination: Path, max_bytes: int):
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or parsed.hostname != "raw.githubusercontent.com":
        raise HTTPException(400, "Demo assets must use public HTTPS raw.githubusercontent.com links.")
    tmp = destination.with_name(f".dl_{uuid.uuid4().hex}")
    try:
        with requests.get(url, stream=True, timeout=(15, 90)) as response:
            response.raise_for_status()
            if urlparse(response.url).hostname != "raw.githubusercontent.com":
                raise HTTPException(400, "The demo asset redirected to an unexpected host.")
            total = 0
            with tmp.open("wb") as out:
                for chunk in response.iter_content(MB):
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(413, "A configured demo asset is larger than the allowed size.")
                    out.write(chunk)
        if total == 0:
            raise HTTPException(400, "A configured demo asset is empty.")
        tmp.replace(destination)
    except HTTPException:
        raise
    except requests.RequestException as exc:
        logger.warning("Demo asset download failed: %s", exc)
        raise HTTPException(502, "Could not download a configured demo asset. Check the URL in config/demo_cases.json.")
    finally:
        tmp.unlink(missing_ok=True)


def _suffix(url: str, allowed: set, default: str) -> str:
    suffix = Path(url.split("?")[0]).suffix.lower()
    return suffix if suffix in allowed else default


def _demo_slug(video_path: str) -> str:
    parts = Path(video_path).name.split("_")
    return parts[1] if len(parts) > 2 and parts[0] == "demo" and parts[1] in demo_cases() else ""


def _demo_image_path(slug: str, case: dict) -> Path | None:
    url = str(case.get("setup_image_url") or "").strip()
    if not url:
        return None
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return settings.storage_dir / "uploads" / f"demo_{slug}_{digest}{_suffix(url, {'.jpg', '.jpeg', '.png', '.webp'}, '.jpg')}"


def _demo_image(slug: str, case: dict) -> Path | None:
    path = _demo_image_path(slug, case)
    if path and not path.exists():
        _public_asset(str(case["setup_image_url"]), path, MAX_IMAGE)
    return path


def _launch(db: Session, video_path: Path, title: str, filename: str, native_url: str | None, domain: str):
    tutorial = db.query(Tutorial).filter_by(video_path=str(video_path)).order_by(Tutorial.id.desc()).first()
    if tutorial and tutorial.status in ("completed", "queued", "processing"):
        if tutorial.status != "completed" and not jobs.running_for(("tutorial", tutorial.id)):
            start_processing(tutorial.id, native_url, domain)
        return tutorial
    if tutorial:
        tutorial.status, tutorial.error = "queued", None
    else:
        tutorial = Tutorial(title=title, filename=filename, video_path=str(video_path), status="queued")
        db.add(tutorial)
    db.commit()
    db.refresh(tutorial)
    start_processing(tutorial.id, native_url, domain)
    return tutorial


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    tutorials = db.query(Tutorial).order_by(Tutorial.created_at.desc()).limit(20).all()
    return templates.TemplateResponse(request, "dashboard.html", {"tutorials": tutorials, "demo_cases": demo_cases()})


@router.post("/demo/{slug}")
def start_demo(slug: str, db: Session = Depends(get_db)):
    case = demo_cases().get(slug)
    if not case:
        raise HTTPException(404, "Demo case not found.")
    video_url = str(case.get("video_url") or "").strip()
    if not video_url:
        raise HTTPException(409, f"The {slug} demo video is not configured. Add its URL in config/demo_cases.json.")
    digest = hashlib.sha256(video_url.encode("utf-8")).hexdigest()[:12]
    destination = settings.storage_dir / "videos" / f"demo_{slug}_{digest}{_suffix(video_url, ALLOWED_VIDEO, '.mp4')}"
    if not destination.exists():
        _public_asset(video_url, destination, settings.max_upload_mb * MB)
    _demo_image(slug, case)
    tutorial = _launch(db, destination, str(case.get("title") or f"{slug.title()} demo"),
                       f"{slug.title()} example video", native_url_for(str(destination), video_url),
                       str(case.get("domain") or "general"))
    return RedirectResponse(f"/tutorials/{tutorial.id}?demo={slug}", status_code=303)


def _download_video_url(url: str, folder: Path, max_bytes: int) -> tuple[Path, str, str]:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(400, "Use a public HTTPS direct video URL.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise HTTPException(400, "The video URL must resolve to a public internet address.")
    except socket.gaierror:
        raise HTTPException(400, "The video URL host could not be resolved.")
    tmp = folder / f".url_{uuid.uuid4().hex}"
    total = 0
    try:
        with requests.get(url.strip(), stream=True, timeout=(15, 120), allow_redirects=False) as response:
            if response.is_redirect:
                raise HTTPException(400, "Redirecting video URLs are not supported. Use the final direct file URL.")
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].lower()
            if not (content_type.startswith("video/") or content_type == "application/octet-stream"):
                raise HTTPException(400, "This URL does not appear to serve a video file.")
            suffix = Path(urlparse(response.url).path).suffix.lower()
            if suffix not in ALLOWED_VIDEO:
                suffix = { "video/mp4": ".mp4", "video/quicktime": ".mov" }.get(content_type, "")
            if suffix not in ALLOWED_VIDEO:
                raise HTTPException(400, "The direct video must be MP4, MOV or M4V.")
            digest = hashlib.sha256()
            with tmp.open("wb") as out:
                for chunk in response.iter_content(MB):
                    if not chunk: continue
                    total += len(chunk)
                    if total > max_bytes: raise HTTPException(413, f"Video exceeds {settings.max_upload_mb} MB.")
                    digest.update(chunk); out.write(chunk)
        if not total: raise HTTPException(400, "The video URL returned an empty file.")
        return tmp, digest.hexdigest(), suffix
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


@router.post("/tutorials")
def create_tutorial(title: str = Form(""), video: UploadFile | None = File(None), video_url: str = Form(""),
                    video_source_mode: str = Form("upload"), db: Session = Depends(get_db)):
    folder = settings.storage_dir / "videos"
    folder.mkdir(parents=True, exist_ok=True)
    if video_source_mode == "url":
        if not video_url.strip(): raise HTTPException(400, "Paste a direct video URL.")
        tmp, digest, suffix = _download_video_url(video_url, folder, settings.max_upload_mb * MB)
        filename = Path(urlparse(video_url).path).name or f"tutorial{suffix}"
    else:
        if not video or not video.filename: raise HTTPException(400, "Choose a video file to upload.")
        suffix = Path(video.filename or "").suffix.lower()
        if suffix not in ALLOWED_VIDEO: raise HTTPException(400, "Upload an MP4, MOV or M4V video.")
        tmp, digest = _spool(video.file, folder, settings.max_upload_mb * MB, f"Video exceeds {settings.max_upload_mb} MB.")
        filename = video.filename
    dest = folder / f"{digest[:24]}{suffix}"
    if dest.exists():
        tmp.unlink(missing_ok=True)
    else:
        tmp.replace(dest)
    if not probe_duration(str(dest)):
        if not db.query(Tutorial).filter_by(video_path=str(dest)).first():
            dest.unlink(missing_ok=True)
        raise HTTPException(400, "That video could not be read. Try an MP4 or MOV file.")
    tutorial = _launch(db, dest, title.strip() or Path(filename).stem[:200], filename or dest.name,
                       native_url_for(str(dest)), "")
    return RedirectResponse(f"/tutorials/{tutorial.id}", status_code=303)


@router.get("/tutorials/{tutorial_id}", response_class=HTMLResponse)
def tutorial_page(tutorial_id: int, request: Request, db: Session = Depends(get_db)):
    tutorial = db.get(Tutorial, tutorial_id)
    if not tutorial:
        raise HTTPException(404, "Tutorial not found.")
    lesson = json.loads(tutorial.lesson_json or "{}")
    slug = request.query_params.get("demo", "")
    slug = slug if slug in demo_cases() else _demo_slug(tutorial.video_path)
    image_path = _demo_image_path(slug, demo_cases().get(slug, {})) if slug else None
    try:
        start_at = max(0.0, float(request.query_params.get("t", 0)))
    except ValueError:
        start_at = 0.0
    return templates.TemplateResponse(request, "lesson.html", {
        "tutorial": tutorial, "lesson": lesson, "steps": tutorial.steps,
        "video_url": f"/media/videos/{Path(tutorial.video_path).name}", "start_at": start_at,
        "stage_meta": {s.get("step_number"): s for s in lesson.get("steps", [])},
        "demo_case": slug,
        "demo_image_url": f"/media/uploads/{image_path.name}" if image_path and image_path.exists() else None,
        "chat": db.query(ChatMessage).filter_by(tutorial_id=tutorial_id).order_by(ChatMessage.id.desc()).limit(10).all(),
        "demo_question": {"plumbing": "How to open my P-trap?", "cooking": "Why is my rice still soggy even after 10 minutes of cooking at low heat?", "arduino": "Why does my LED not lighting up?"}.get(slug),
    })


@router.get("/api/tutorials/{tutorial_id}/status")
def tutorial_status(tutorial_id: int, db: Session = Depends(get_db)):
    tutorial = db.get(Tutorial, tutorial_id)
    if not tutorial:
        raise HTTPException(404, "Not found")
    job = jobs.running_for(("tutorial", tutorial_id))
    return {"status": tutorial.status, "error": tutorial.error, "title": tutorial.title,
            "stages": job.snapshot()["stages"] if job else []}


@router.post("/api/tutorials/{tutorial_id}/ask")
def ask(tutorial_id: int, payload: QuestionIn, db: Session = Depends(get_db)):
    tutorial = db.get(Tutorial, tutorial_id)
    if not tutorial:
        raise HTTPException(404, "Tutorial not found")
    if tutorial.status != "completed":
        raise HTTPException(409, "Tutorial is not ready yet.")
    answer = answer_question(tutorial, payload.question, payload.current_timestamp)
    db.add(ChatMessage(tutorial_id=tutorial.id, question=payload.question, answer=answer))
    db.commit()
    return {"answer": answer}


def _find_cached(db: Session, tutorial: Tutorial, photo_path: Path, user_context: str = "") -> tuple[Comparison, dict] | None:
    current = cmp.lesson_hash(tutorial.lesson_json)
    for record in db.query(Comparison).filter_by(tutorial_id=tutorial.id, photo_path=str(photo_path)).order_by(Comparison.id.desc()):
        try:
            analysis = json.loads(record.analysis_json or "{}")
        except json.JSONDecodeError:
            continue
        if cmp.is_current(analysis, current) and analysis.get("user_context", "") == user_context.strip()[:1200]:
            return record, analysis
    return None


def _comparison_job(job: jobs.Job, tutorial_id: int, photo_path: Path, user_context: str = ""):
    db = SessionLocal()
    try:
        tutorial = db.get(Tutorial, tutorial_id)
        lesson = json.loads(tutorial.lesson_json or "{}")
        demo_cfg = demo_cases().get(_demo_slug(tutorial.video_path), {})
        demo_url = str(demo_cfg.get("video_url") or "") or None
        analysis = cmp.run_comparison(
            tutorial_id=tutorial.id, lesson=lesson, lesson_json=tutorial.lesson_json, video_path=tutorial.video_path,
            native_url=native_url_for(tutorial.video_path, demo_url), photo_path=photo_path,
            duration=float(tutorial.duration or 0), demo_cfg=demo_cfg, job=job, user_context=user_context, transcript=tutorial.transcript or "")
        analysis["photo_url"] = cmp.media_url(photo_path)
        with _lock_for(f"{tutorial_id}:{photo_path.name}"):
            found = _find_cached(db, tutorial, photo_path, user_context)
            record = found[0] if found else Comparison(tutorial_id=tutorial.id, photo_path=str(photo_path))
            record.analysis_json = json.dumps(analysis, ensure_ascii=False)
            db.add(record)
            db.commit()
            db.refresh(record)
        return {"comparison_id": record.id, "comparison_url": f"/tutorials/{tutorial.id}/comparison/{record.id}"}
    finally:
        db.close()


@router.post("/tutorials/{tutorial_id}/compare")
def compare(tutorial_id: int, photo: UploadFile | None = File(None), demo_case: str = Form(""),
            user_context: str = Form(""), db: Session = Depends(get_db)):
    tutorial = db.get(Tutorial, tutorial_id)
    if not tutorial:
        raise HTTPException(404, "Tutorial not found")
    if tutorial.status != "completed":
        raise HTTPException(409, "Wait until the tutorial analysis is complete.")
    uploads = settings.storage_dir / "uploads"
    if photo and photo.filename:
        tmp, digest = _spool(photo.file, uploads, MAX_IMAGE, "Image exceeds 15 MB.")
        try:
            suffix = _check_image(tmp)
        except HTTPException:
            tmp.unlink(missing_ok=True)
            raise
        path = uploads / f"learner_{tutorial.id}_{digest[:24]}{suffix}"
        if path.exists():
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(path)
    elif demo_case:
        case = demo_cases().get(demo_case)
        path = _demo_image(demo_case, case) if case else None
        if not path:
            raise HTTPException(409, "This demo has no setup photo configured.")
    else:
        raise HTTPException(400, "Upload a photo of your setup or choose the example photo.")

    cached = _find_cached(db, tutorial, path, user_context.strip()[:1200])
    if cached:
        return JSONResponse({"cached": True, "comparison_url": f"/tutorials/{tutorial.id}/comparison/{cached[0].id}"})
    tid = tutorial.id
    context = user_context.strip()[:1200]
    key = ("compare", tid, path.name, cmp.lesson_hash(tutorial.lesson_json), hashlib.sha256(context.encode()).hexdigest()[:12])
    job = jobs.start(key, cmp.STAGES, lambda j: _comparison_job(j, tid, path, context))
    return JSONResponse({"cached": False, "job_id": job.id, "stages": job.snapshot()["stages"]})


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "This job is no longer available. Submit the comparison again.")
    return job.snapshot()


@router.get("/tutorials/{tutorial_id}/comparison/{comparison_id}", response_class=HTMLResponse)
def comparison_page(tutorial_id: int, comparison_id: int, request: Request, db: Session = Depends(get_db)):
    tutorial, record = db.get(Tutorial, tutorial_id), db.get(Comparison, comparison_id)
    if not tutorial or not record or record.tutorial_id != tutorial_id:
        raise HTTPException(404, "Comparison not found.")
    try:
        analysis = json.loads(record.analysis_json or "{}")
    except json.JSONDecodeError:
        analysis = {"plain_language_summary": "This comparison could not be loaded."}
    lesson = json.loads(tutorial.lesson_json or "{}")
    demo_case = _demo_slug(tutorial.video_path)
    return templates.TemplateResponse(request, "comparison.html", {
        "tutorial": tutorial, "comparison": record, "analysis": analysis, "lesson": lesson,
        "demo_case": demo_case,
        "demo_question": {"plumbing": "How to open my P-trap?", "cooking": "Why is my rice still soggy even after 10 minutes of cooking at low heat?", "arduino": "Why does my LED not lighting up?"}.get(demo_case)})



def _saved_explainers(tutorial_id: int) -> list[dict]:
    folder, items = settings.storage_dir / "diagrams", []
    for meta_path in folder.glob(f"explainer_{tutorial_id}_*.json"):
        try:
            item = json.loads(meta_path.read_text(encoding="utf-8"))
            image = folder / item.get("image_file", "")
            if image.exists():
                item["image_url"] = f"/media/diagrams/{image.name}"
                items.append(item)
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(items, key=lambda i: i.get("created_at", ""), reverse=True)[:5]


@router.get("/tutorials/{tutorial_id}/explainer", response_class=HTMLResponse)
def explainer_page(tutorial_id: int, request: Request, db: Session = Depends(get_db)):
    tutorial = db.get(Tutorial, tutorial_id)
    if not tutorial:
        raise HTTPException(404, "Tutorial not found.")
    demo_case = request.query_params.get("demo", "")
    if demo_case not in {"plumbing", "cooking", "arduino"}:
        demo_case = _demo_slug(tutorial.video_path)
    return templates.TemplateResponse(request, "explainer.html", {
        "tutorial": tutorial, "cached_explainers": _saved_explainers(tutorial_id),
        "demo_question": {"plumbing": "How does the debris clog in P-trap?", "cooking": "Why do we need to add ghee?", "arduino": "Why do we need to use resistors?"}.get(demo_case)})


@router.post("/tutorials/{tutorial_id}/diagram")
def diagram(tutorial_id: int, prompt: str = Form(...), db: Session = Depends(get_db)):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime
    tutorial = db.get(Tutorial, tutorial_id)
    if not tutorial:
        raise HTTPException(404, "Tutorial not found")
    prompt = prompt.strip()[:500]
    if not prompt:
        raise HTTPException(400, "Enter a concept or tricky part to explain.")
    lesson_json = tutorial.lesson_json or "{}"
    key = hashlib.sha256(f"v4|{tutorial_id}|{cmp.lesson_hash(lesson_json)}|{prompt.casefold()}".encode()).hexdigest()[:24]
    folder = settings.storage_dir / "diagrams"
    image_path, meta_path = folder / f"explainer_{tutorial_id}_{key}.png", folder / f"explainer_{tutorial_id}_{key}.json"
    with _lock_for(f"explainer:{key}"):
        if image_path.exists() and meta_path.exists():
            try:
                saved = json.loads(meta_path.read_text(encoding="utf-8"))
                if saved.get("explanation"):
                    return {"image_url": f"/media/diagrams/{image_path.name}", "image_status": "ok", "explanation": saved["explanation"],
                            "prompt": prompt, "cached": True}
            except (OSError, json.JSONDecodeError):
                pass
        # Write first, then derive the image brief from the exact explanation. This prevents
        # parallel, unrelated text/image generations from drifting apart.
        question = (f"Answer this learner question in the context of the complete tutorial: {prompt}. "
                    "Return well-formed Markdown with a useful heading, direct answer, short paragraphs, "
                    "numbered steps or bullets where useful, and a concise safety/uncertainty note. "
                    "Explain the exact concept/action the companion visual should teach. Separate tutorial "
                    "evidence from general knowledge. Never invent details.")
        explanation = answer_question(tutorial, question, None)
        lesson = json.loads(lesson_json)
        image_prompt = (
            "Create ONE focused instructional visual that corresponds directly to the written explanation below. "
            "Show the specific parts, physical relationship, action, direction or sequence the explanation describes. "
            "Use the tutorial domain, not a generic illustration. Do not create a schematic, wiring diagram, blueprint, "
            "collage or unrelated troubleshooting guide unless the explanation explicitly asks for that exact form. "
            "No words, letters, numbers, pseudo-text or tiny labels; leave clear space for programmatic annotations. "
            "If the explanation says a condition cannot be verified, depict a check/inspection rather than asserting it. "
            "Do not contradict the written answer.\n"
            f"LEARNER QUESTION: {prompt}\nTUTORIAL: {tutorial.title}\n"
            f"LESSON STEPS: {json.dumps(lesson.get('steps', []), ensure_ascii=False)[:8000]}\n"
            f"WRITTEN ANSWER: {explanation[:9000]}"
        )
        image_error = None
        try:
            generate_diagram(image_prompt, str(image_path))
            image_url, image_status = f"/media/diagrams/{image_path.name}", "ok"
        except Exception:
            image_path.unlink(missing_ok=True)
            image_url, image_status = None, "failed"
            image_error = "The visual could not be generated this time; the complete written answer is available."
        meta_path.write_text(json.dumps({
            "prompt": prompt, "explanation": explanation, "image_file": image_path.name if image_url else "",
            "image_status": image_status, "created_at": datetime.utcnow().isoformat()}, ensure_ascii=False), encoding="utf-8")
    return {"image_url": image_url, "image_status": image_status, "image_error": image_error,
            "explanation": explanation, "prompt": prompt, "cached": False}
