import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import settings
from app.services import tutorial_analyzer as ta
from app.services.jobs import Job
from app.services.video_processor import extract_audio_wav, extract_frames, probe_duration

logger = logging.getLogger("skillow.analysis")

STAGES = [("voice", "Transcribing the spoken instructions"),
          ("video", "Understanding the tutorial video"),
          ("verify", "Checking each stage against the footage")]


def _transcribe(video_path: str, work_dir: Path, duration: float, job: Job) -> str:
    wav = work_dir / "tutorial_audio.wav"
    job.stage("voice", "running")
    try:
        extract_audio_wav(video_path, str(wav))
        if wav.stat().st_size > 32 * 1024 * 1024 or duration > 600:
            job.stage("voice", "skipped", "Audio is too long for transcription")
            return ""
        text = ta.extract_transcript_text(ta.transcribe_audio(str(wav)))
        job.stage("voice", "done" if text else "skipped", "" if text else "No speech detected")
        return text
    except Exception as exc:
        logger.warning("Transcription unavailable: %s", exc)
        job.stage("voice", "skipped", "Transcription unavailable")
        return ""
    finally:
        wav.unlink(missing_ok=True)


def analyze_tutorial(video_path: str, title: str, native_url: str | None, domain_hint: str,
                     work_dir: Path, job: Job) -> tuple[str, dict, float]:
    duration = probe_duration(video_path)
    if not duration:
        raise ta.meta.MetaError("This video could not be read. Try an MP4 or MOV file.")
    work_dir.mkdir(parents=True, exist_ok=True)
    transcript, lesson, native_note = "", None, ""
    with ThreadPoolExecutor(max_workers=2) as pool:
        voice = pool.submit(_transcribe, video_path, work_dir, duration, job)
        native = None
        if native_url and settings.native_video:
            job.stage("video", "running", "Muse Spark is watching the whole video")
            native = pool.submit(ta.analyze_native, native_url, title, duration, domain_hint)
        if native:
            try:
                summary, lesson = native.result()
            except Exception as exc:
                logger.warning("Native video analysis failed, using frames: %s", exc)
                native_note = ta.meta.public_error(exc)
        voice_text = voice.result()
    if lesson is None:
        job.stage("video", "running", "Analysing sampled frames" + (" (native video unavailable)" if native_url else ""))
        frames, duration, interval = extract_frames(video_path, str(work_dir), max_frames=18)
        summary, lesson = ta.analyze_frames(frames, title, voice_text, duration, interval, domain_hint)
        if native_note:
            lesson["_native_error"] = native_note
    job.stage("video", "done", "Native video understanding" if lesson["_analysis_source"] == "native_video"
              else "Sampled frames")
    job.stage("verify", "running")
    lesson["verification"] = ta.verify_stages(video_path, lesson, voice_text or summary, str(work_dir))
    check = lesson["verification"]
    job.stage("verify", "done" if check.get("available") else "skipped",
              f"{check.get('supported', 0)} of {check.get('checked', 0)} stages confirmed" if check.get("available")
              else "Verification unavailable")
    return voice_text or summary, lesson, duration
