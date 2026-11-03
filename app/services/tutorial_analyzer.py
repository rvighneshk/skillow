import json
import logging
import re
import uuid
from pathlib import Path

from app.config import settings
from app.services import meta_client as meta
from app.services.domains import PHASES, clean_domain
from app.services.video_processor import frame_at

logger = logging.getLogger("skillow.analyzer")
ANALYSIS_VERSION = 3

SCHEMA = """{
 "domain":"plumbing|cooking|electronics|general",
 "title":"...", "summary":"...", "learning_objectives":["..."], "prerequisites":["..."], "tools":["..."], "materials":["..."], "safety_warnings":["..."],
 "transcript":"Concise summary of what the instructor says. No invented quotes.",
 "steps":[{"step_number":1,"title":"...","instruction":"Detailed explanation of this stage",
   "substeps":["Atomic action 1", "Atomic action 2"], "spoken_instruction":"Detailed relevant narration",
   "phase":"preparation|assembly|intermediate|completion|demonstration",
   "start_seconds":0,"end_seconds":0,"key_moment_seconds":0,
   "visual_observation":"What is visible at the key moment, or 'Not clear'.",
   "visible_items":["objects clearly visible at the key moment"],
   "safety":["..."],"confidence":0.0}]
}"""

RULES = """Rules:
- Timestamps are seconds from the start of the video. key_moment_seconds is the single best moment inside a stage that shows that stage's state (never a title card or intro).
- Provide 2-5 learning objectives and genuine prerequisites. Identify EVERY distinct meaningful action the instructor performs or explains; do not force a fixed step count. A short tutorial may have only a few steps; a 5-10 minute tutorial may need 10, 15, 20 or more. Use as many as the actual instructional content warrants (up to 30 top-level stages). Never merge separate actions merely to fit a target number.
- Keep stages chronological and focused on one coherent action or action group. Within a stage, provide ordered atomic substeps for smaller hand movements, setup actions, adjustments, intermediate actions and checks that would otherwise be lost. Avoid both overly broad summaries and meaningless splitting of continuous motion.
- Write detailed, learner-facing instructions: what to do, how it is done, what to observe, relevant spoken explanation/reason, and what changes/result should occur. Preserve the instructor's useful details and caveats; do not reduce the tutorial to headlines. Capture spoken instruction verbatim only when certain; otherwise accurately paraphrase it.
- Provide stage start/end/key-moment timestamps as accurately as possible. Timestamp substeps when supported; otherwise leave their timestamps null. If unsure of a timestamp use null and lower confidence. Never invent tools, parts, quantities, measurements, dialogue or actions.
- Report only what the video shows or says. Distinguish observed actions from inferred explanations and clearly mark uncertainty. Do not claim a hidden action happened just because it is typical.
- Include concrete task-relevant hazards and preventive actions. Distinguish hazards visible/stated in the video from standard precautions. Never certify that a task is correct or safe. Include intermediate and final verification/checks wherever the tutorial demonstrates or explains them.
- Capture each stage's spoken instruction, tools/materials used, expected outcome and verification check where supported. Do not invent missing information. Keep all relevant details rather than summarizing away important steps."""


def transcribe_audio(audio_path: str) -> dict:
    request_payload = {"mode": "DIARIZATION", "model": settings.muse_voice_model, "audioEncoding": "WAV"}
    with open(audio_path, "rb") as audio, meta.gate:
        response = meta.session.post(
            meta.url(f"/asr/transcribe?sessionId={uuid.uuid4()}"),
            headers={"Authorization": f"Bearer {settings.model_api_key}", "Accept": "application/json"},
            files={"request": (None, json.dumps(request_payload), "application/json"),
                   "audio": (Path(audio_path).name, audio, "audio/wav")},
            timeout=(10, 240))
    if response.status_code >= 400:
        logger.warning("ASR failed: %s %s", response.status_code, response.text[:400])
        raise meta.MetaError(f"Speech transcription was rejected ({response.status_code}).")
    try:
        return response.json()
    except ValueError:
        return {"text": response.text}


def extract_transcript_text(payload) -> str:
    found = []

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.lower() in {"transcript", "text"} and isinstance(child, str) and child.strip():
                    found.append(child.strip())
                elif isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return "\n".join(dict.fromkeys(found))


def _num(value):
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _strings(value, limit=12) -> list[str]:
    return [str(x).strip() for x in (value or []) if str(x).strip()][:limit] if isinstance(value, list) else []


def normalise_lesson(data: dict, duration: float, source: str, domain_hint: str = "") -> dict:
    limit = duration * 1.05 if duration else None
    steps = []
    for index, raw in enumerate((data.get("steps") or [])[:30], 1):
        if not isinstance(raw, dict):
            continue
        times = {}
        for field in ("start_seconds", "end_seconds", "key_moment_seconds"):
            value = _num(raw.get(field))
            times[field] = None if value is None or value < 0 or (limit and value > limit) else value
        start, end, key = times["start_seconds"], times["end_seconds"], times["key_moment_seconds"]
        known = any(v is not None for v in (start, end, key))
        if start is None:
            start = key if key is not None else (end if end is not None else 0.0)
        if end is None or end < start:
            end = start
        if key is None or not start <= key <= max(end, start):
            key = (start + end) / 2
        confidence = _num(raw.get("confidence"))
        phase = str(raw.get("phase") or "").strip().lower()
        instruction = str(raw.get("instruction") or "").strip()
        substeps = _strings(raw.get("substeps"), 20)
        # Keep the detailed action sequence visible in the existing lesson UI as well as structured in JSON.
        if substeps:
            instruction = instruction + ("\n\n**Actions in this stage:**\n" if instruction else "**Actions in this stage:**\n") + "\n".join(f"{i}. {action}" for i, action in enumerate(substeps, 1))
        steps.append({
            "step_number": index,
            "title": str(raw.get("title") or f"Step {index}").strip()[:160],
            "instruction": instruction,
            "substeps": substeps,
            "spoken_instruction": str(raw.get("spoken_instruction") or "").strip()[:1200],
            "tools_used": _strings(raw.get("tools_used"), 8),
            "materials_used": _strings(raw.get("materials_used"), 8),
            "expected_outcome": str(raw.get("expected_outcome") or "").strip()[:800],
            "verification_check": str(raw.get("verification_check") or "").strip()[:800],
            "phase": phase if phase in PHASES else "demonstration",
            "start_seconds": round(start, 1), "end_seconds": round(end, 1), "key_moment_seconds": round(key, 1),
            "timing_known": known,
            "visual_observation": str(raw.get("visual_observation") or "").strip(),
            "visible_items": _strings(raw.get("visible_items"), 8),
            "safety": _strings(raw.get("safety"), 5),
            "confidence": round(min(1.0, max(0.0, confidence if confidence is not None else 0.5)), 2),
            "verified": None,
        })
    steps.sort(key=lambda s: (not s["timing_known"], s["start_seconds"]))
    for index, step in enumerate(steps, 1):
        step["step_number"] = index
    if not steps:
        raise meta.MetaError("No practical stages could be identified in this video.")
    domain = clean_domain(domain_hint or data.get("domain"))
    warnings = _strings(data.get("safety_warnings"), 8)
    safety_source = "model_assessed" if warnings else "baseline"
    if not warnings:
        defaults = {
            "electronics": ["Disconnect USB or external power before changing wiring; reconnect only after checking polarity and connections.", "Use only the voltage and current ratings specified for the board and components. Avoid short circuits and exposed conductive contacts.", "If a component becomes hot, smells unusual, or behaves unexpectedly, disconnect power and inspect before continuing."],
            "plumbing": ["If the drain is connected to a shared, pressurised, damaged, or unfamiliar system, stop and seek qualified help.", "Place a stable container below the trap and expect retained water or debris when loosening fittings.", "Avoid overtightening plastic slip nuts; check for leaks with a controlled water test before using the cabinet normally."],
            "cooking": ["Use heat-resistant protection when handling hot cookware, steam, or boiling liquids; keep hands and face clear of steam.", "Use stable cookware and appropriate heat settings. Keep flammable items and loose clothing away from the flame or heating element.", "Check food is cooked adequately for the ingredients and method; do not rely on appearance alone when food safety requires a temperature check."]
        }
        warnings = defaults.get(domain, ["Pause before any step involving heat, electricity, sharp tools, chemicals, or moving parts; use suitable protection and follow equipment instructions.", "Stop if the setup differs materially from the tutorial or you cannot identify a component or hazard."])
    return {
        "title": str(data.get("title") or "").strip(),
        "summary": str(data.get("summary") or "").strip(),
        "learning_objectives": _strings(data.get("learning_objectives"), 6),
        "prerequisites": _strings(data.get("prerequisites"), 8),
        "domain": domain,
        "tools": _strings(data.get("tools")), "materials": _strings(data.get("materials")),
        "safety_warnings": warnings, "safety_source": safety_source,
        "steps": steps, "_analysis_source": source, "_analysis_version": ANALYSIS_VERSION,
    }


def analyze_native(video_url: str, title: str, duration: float, domain_hint: str = "") -> tuple[str, dict]:
    prompt = (f'You are Skillow, a careful practical-tutorial analyst. Watch the whole tutorial video "{title}" '
              f"({duration:.0f} seconds) as one continuous process and describe its practical stages.\n"
              f"Return ONLY valid JSON with this schema:\n{SCHEMA}\n{RULES}")
    content = [meta.text_part(prompt), {"type": "input_video", "video_url": video_url}]
    data = meta.responses_json(content, timeout=420)
    transcript = str(data.pop("transcript", "") or "")
    return transcript, normalise_lesson(data, duration, "native_video", domain_hint)


def analyze_frames(frames: list[Path], title: str, voice_transcript: str, duration: float,
                   interval: int, domain_hint: str = "") -> tuple[str, dict]:
    selected = frames if len(frames) <= 12 else [frames[round(i * (len(frames) - 1) / 11)] for i in range(12)]
    notes = []
    for frame in selected:
        match = re.search(r"frame_(\d+)", frame.stem)
        notes.append({"frame": frame.stem, "approx_seconds": max(0, (int(match.group(1)) - 1) * interval) if match else 0})
    prompt = (f'You are Skillow, a careful practical-tutorial analyst. The attached images are sampled frames '
              f'from the tutorial "{title}" in chronological order with approximate timestamps: {json.dumps(notes)}.\n'
              f"Spoken transcript when available:\n{(voice_transcript.strip() or 'Not available.')[:14000]}\n"
              f"Return ONLY valid JSON with this schema:\n{SCHEMA}\n{RULES}\n"
              "- Use the frame timestamps as anchors and do not assume every action is visible between frames.")
    content = [meta.text_part(prompt)] + [meta.image_part(f, 1120) for f in selected]
    data = meta.responses_json(content, timeout=360)
    transcript = str(data.pop("transcript", "") or "")
    lesson = normalise_lesson(data, duration, "frames", domain_hint)
    lesson["_frame_interval_seconds"] = interval
    return transcript, lesson


def verify_stages(video_path: str, lesson: dict, transcript: str, work_dir: str) -> dict:
    candidates = [s for s in lesson["steps"] if s["timing_known"]][:8]
    pairs = []
    for step in candidates:
        try:
            pairs.append((step, frame_at(video_path, step["key_moment_seconds"], work_dir)))
        except Exception as exc:
            logger.info("No verification frame for step %s: %s", step["step_number"], exc)
    if not pairs:
        return {"available": False, "reason": "No frames could be extracted for checking."}
    claims = [{"step_number": s["step_number"], "title": s["title"], "instruction": s["instruction"],
               "key_moment_seconds": s["key_moment_seconds"], "visible_items": s["visible_items"],
               "visual_observation": s["visual_observation"]} for s, _ in pairs]
    prompt = ("You are auditing a draft tutorial analysis. Each attached image is the frame at the claimed key moment "
              "of one stage, in the same order as the claims below.\n"
              f"Claims: {json.dumps(claims, ensure_ascii=False)}\n"
              f"Instructor transcript: {(transcript or 'Not available.')[:6000]}\n"
              "For each claim decide whether the frame and transcript support it. Return ONLY JSON:\n"
              '{"checks":[{"step_number":1,"verdict":"supported|partly|unsupported","confidence":0.0,'
              '"unsupported_items":["listed visible_items that are NOT visible"],"note":"short reason"}]}\n'
              "Be strict: a title card, a face-only shot or a different action means unsupported.")
    try:
        data = meta.responses_json([meta.text_part(prompt)] + [meta.image_part(p, 900) for _, p in pairs], timeout=240)
    except Exception as exc:
        logger.warning("Stage verification unavailable: %s", exc)
        return {"available": False, "reason": "The verification pass could not complete."}
    by_number = {s["step_number"]: s for s in lesson["steps"]}
    tally = {"supported": 0, "partly": 0, "unsupported": 0}
    removed = 0
    for check in data.get("checks") or []:
        step = by_number.get(int(check.get("step_number") or 0)) if str(check.get("step_number") or "").isdigit() else None
        verdict = str(check.get("verdict") or "").lower()
        if not step or verdict not in tally:
            continue
        tally[verdict] += 1
        confidence = _num(check.get("confidence"))
        gone = {str(x).strip().lower() for x in _strings(check.get("unsupported_items"))}
        before = len(step["visible_items"])
        step["visible_items"] = [x for x in step["visible_items"] if x.lower() not in gone]
        removed += before - len(step["visible_items"])
        step["verified"] = {"supported": True, "partly": "partial", "unsupported": False}[verdict]
        step["verification_note"] = str(check.get("note") or "")[:240]
        if verdict == "partly" and confidence is not None:
            step["confidence"] = round(min(step["confidence"], max(0.0, min(1.0, confidence))), 2)
        elif verdict == "unsupported":
            step["confidence"] = round(step["confidence"] * 0.4, 2)
    return {"available": True, "checked": len(pairs), **tally, "items_removed": removed,
            "method": "frame at each key moment + transcript"}
