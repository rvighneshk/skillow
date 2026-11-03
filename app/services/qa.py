import json
import logging

from app.config import settings
from app.services import meta_client as meta
from app.services.video_processor import frame_at

logger = logging.getLogger("skillow.qa")


def _nearby_frames(tutorial, steps: list[dict], timestamp: float | None) -> list:
    frame_dir = settings.storage_dir / "frames" / str(tutorial.id)
    if timestamp is not None:
        moments = [timestamp]
    else:
        moments = [float(s.get("key_moment_seconds") or s.get("start_seconds") or 0) for s in steps[:3]]
    frames = []
    for moment in moments:
        try:
            frames.append(frame_at(tutorial.video_path, moment, str(frame_dir)))
        except Exception as exc:
            logger.info("No frame for Q&A at %ss: %s", moment, exc)
    return frames


def answer_question(tutorial, question: str, timestamp: float | None) -> str:
    lesson = json.loads(tutorial.lesson_json or "{}")
    steps = lesson.get("steps", [])
    if timestamp is not None:
        relevant = [s for s in steps if float(s.get("start_seconds", 0)) - 15 <= timestamp <= float(s.get("end_seconds", 0)) + 20]
        if not relevant:
            relevant = sorted(steps, key=lambda s: abs(float(s.get("start_seconds", 0)) - timestamp))[:2]
    else:
        relevant = steps[:6]
    prompt = f"""
You are Skillow's practical learning assistant. Your job is to help the learner understand
and apply a tutorial, not merely quote its transcript.

Answer the actual question directly. You may combine:
1. What the instructor says or demonstrates in this specific tutorial.
2. What is visible in the nearby frames.
3. Reliable general knowledge that explains the underlying tool, component, technique, or
   principle, even when the tutorial does not explicitly explain it.

Clearly distinguish these sources. Use headings such as **In this tutorial** and
**Why this matters generally** when useful. Never invent a quote or claim the video shows
something that it does not. If the tutorial does not cover the exact detail, say so briefly,
then still provide a useful general explanation where possible. Avoid repetitive disclaimers.

Return well-formed Markdown with useful ## headings, paragraphs, bullets or numbered steps,
**bold** emphasis, and fenced code only when genuinely useful. Never output raw HTML.
Mention a timestamp only when supported by the supplied timeline. Do not overstate certainty
or compatibility. Flag gas, mains electrical, pressurised, structural, concealed pipework,
or major-leak work for a qualified professional. Never guarantee a repair.

Tutorial: {tutorial.title}
Question: {question}
Current video time: {timestamp} seconds
Relevant structured stages: {json.dumps(relevant, ensure_ascii=False)}
Complete lesson context: {json.dumps(lesson, ensure_ascii=False)}
Full spoken transcript: {tutorial.transcript or 'Not available.'}
Attached images, when present, are tutorial frames near the relevant moment.
"""
    content = [meta.text_part(prompt)] + [meta.image_part(p) for p in _nearby_frames(tutorial, relevant, timestamp)]
    return meta.responses_text(content, timeout=180)
