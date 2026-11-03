import hashlib
import json
import logging
import time
from pathlib import Path

from app.config import settings
from app.services import meta_client as meta
from app.services.domains import profile
from app.services.jobs import Job
from app.services.segmentation import analyse_image, choose_labels

logger = logging.getLogger("skillow.compare")
VERSION = 7
STAGES = [("understand", "Understanding the complete tutorial"),
          ("moment", "Preparing the whole-procedure comparison"),
          ("setup", "Checking visible learner evidence"),
          ("compare", "Comparing progress with the procedure"),
          ("guide", "Verifying the next safe action")]


def lesson_hash(lesson_json: str) -> str:
    return hashlib.sha256((lesson_json or "").encode("utf-8")).hexdigest()[:16]


def is_current(analysis: dict, current_hash: str) -> bool:
    return (analysis.get("analysis_version") == VERSION and analysis.get("complete") is True
            and analysis.get("lesson_hash") == current_hash)


def media_url(path) -> str:
    return "/media/" + Path(path).resolve().relative_to(settings.storage_dir.resolve()).as_posix()


def _clamp(value, default=0.0) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


def _steps(lesson: dict) -> list[dict]:
    steps = []
    for raw in lesson.get("steps") or []:
        step = dict(raw)
        start = float(step.get("start_seconds") or 0)
        end = float(step.get("end_seconds") or start)
        step.setdefault("phase", "demonstration")
        step.setdefault("timing_known", True)
        step.setdefault("visible_items", [])
        step.setdefault("verified", None)
        step["key_moment_seconds"] = float(step.get("key_moment_seconds") or (start + end) / 2)
        steps.append(step)
    return steps


def compare_entire_tutorial(native_url: str | None, lesson: dict, photo: Path,
                            user_context: str = "", transcript: str = "") -> dict:
    """Compare the learner against the complete procedure, never a selected frame."""
    steps = _steps(lesson)
    structured = {
        "title": lesson.get("title"), "summary": lesson.get("summary"),
        "learning_objectives": lesson.get("learning_objectives", []),
        "prerequisites": lesson.get("prerequisites", []), "tools": lesson.get("tools", []),
        "materials": lesson.get("materials", []), "safety_warnings": lesson.get("safety_warnings", []),
        "steps": [{
            "step_number": x["step_number"], "title": x["title"], "phase": x["phase"],
            "instruction": x.get("instruction", ""), "spoken_instruction": x.get("spoken_instruction", ""),
            "visual_observation": x.get("visual_observation", ""),
            "tools_used": x.get("tools_used", []), "materials_used": x.get("materials_used", []),
            "visible_items": x.get("visible_items", []), "safety": x.get("safety", []),
            "expected_outcome": x.get("expected_outcome", ""),
            "verification_check": x.get("verification_check", ""),
            "start_seconds": x.get("start_seconds"), "end_seconds": x.get("end_seconds")
        } for x in steps]
    }
    review_mode = "full_video_plus_structured_lesson" if native_url and settings.native_video else "structured_lesson_transcript_and_photo"
    prompt = f"""You are Skillow's whole-procedure understanding agent.
Compare the learner's current situation photo and their written description against the COMPLETE tutorial understanding below.
Review mode: {review_mode}. If no video input is attached, explicitly state that the review is based on the extracted structured lesson and available transcript, not a direct full-video replay.
Do not use frame similarity, object overlap, or one matched timestamp as the comparison basis.
Use the tutorial's ordered sequence, spoken instructions/transcript, actions, prerequisites, tools, materials, safety warnings and expected outcomes.

COMPLETE STRUCTURED TUTORIAL:
{json.dumps(structured, ensure_ascii=False)}

FULL AVAILABLE SPOKEN TRANSCRIPT:
{transcript or lesson.get("transcript") or "No transcript was available."}

LEARNER DESCRIPTION (user-authored; not visually verified):
{user_context or "Not provided."}

Return ONLY JSON with:
{{
 "tutorial_overview":"the process and intended final outcome",
 "learner_progress":"what the learner has already done, separating visible evidence from their description",
 "what_is_correct":["supported correct actions or conditions"],
 "what_differs":["specific differences from the tutorial sequence or method"],
 "missing_or_incorrect":["missing prerequisites, materials, actions, checks, or incorrect conditions"],
 "next_step":"the single appropriate next action, or a safe pause/clarification",
 "why_next_step_matters":"causal or procedural reason",
 "safety_requirements":["specific precautions grounded in tutorial or reliable baseline safety"],
 "current_step":number or null,
 "gaps":["whole-process gaps, not visual object differences"],
 "dependencies":["order and prerequisite relationships"],
 "limits":["what cannot be verified from a still photo or description"],
 "confidence":"low|medium|high"
}}
Do not claim hidden connections, internal states, measurements, compatibility, temperatures, or successful operation are verified by a photo.
If the photo shows a different phase, identify it without calling it an error. Do not invent details absent from the tutorial.
"""
    content = [meta.text_part(prompt)]
    if native_url and settings.native_video:
        content.append({"type": "input_video", "video_url": native_url})
    content.append(meta.image_part(photo))
    return meta.responses_json(content, timeout=480)


def _sam_text(label: str, sam: dict) -> str:
    seen = [f"{o['label']} x{o['count']}" + (f" ({o['coverage_pct']}% of image)" if o["coverage_pct"] is not None else "")
            for o in sam["observations"] if o["count"]]
    missing = [o["label"] for o in sam["observations"] if not o["count"]]
    return f"{label}: detected {', '.join(seen) or 'nothing'}" + (f"; not detected {', '.join(missing)}" if missing else "")


def _items(value, limit=8) -> list[dict]:
    out = []
    for item in (value or [])[:limit]:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            source = str(item.get("source") or "inferred").lower()
        else:
            text, source = str(item).strip(), "inferred"
        if text:
            out.append({"text": text, "source": source if source in ("reference", "learner", "both", "inferred") else "inferred"})
    return out


def _draft(lesson, photo, sam_text, prof, user_context="", whole_tutorial=None) -> dict:
    whole = whole_tutorial or {}
    prompt = f"""You are Skillow's practical-learning comparison agent for {prof['label']}.
The attached image is ONLY the learner's current situation photo. No tutorial frame is attached.
Base the comparison on the complete ordered procedure, spoken transcript, instructions, prerequisites,
tools, materials, expected outcomes and safety—not image/frame matching.

COMPLETE LESSON: {json.dumps({k: v for k, v in lesson.items() if k != "_transcript"}, ensure_ascii=False)}
TRANSCRIPT: {lesson.get("_transcript") or "Not available."}
WHOLE-PROCEDURE REVIEW: {json.dumps(whole, ensure_ascii=False)}
LEARNER DESCRIPTION (not visually verified): {user_context or "Not provided"}
SUPPLEMENTARY SAM DETECTIONS (objects only; never proof of correctness): {sam_text}
DOMAIN FOCUS: {prof['focus']}

Return ONLY JSON:
{{"summary":"plain-language status of learner progress","learner_summary":"what is visibly shown, separating description from observation",
"observed_components":["visible things only"],"likely_fitting":null,
"what_already_done":["actions supported by photo or learner description; label the evidence"],
"correct":["what aligns with tutorial and why"],
"differences":["what differs from the tutorial method or sequence"],
"missing_or_incorrect":["missing or incorrect actions, materials, prerequisites or checks"],
"unknowns":["what cannot be verified"],
"next_step":"one clear safe next action","why_next_step_matters":"reason",
"safety_requirements":["precautions relevant to the next step"],"confidence":"low|medium|high"}}
Be explicit about source of evidence. Do not claim hidden conditions are visible. Do not infer an absent item if it may be outside the photo.
"""
    return meta.responses_json([meta.text_part(prompt), meta.image_part(photo)], timeout=240)


def _audit_and_guide(lesson, photo, draft, sam_text, prof, user_context="", whole_tutorial=None) -> dict:
    whole = whole_tutorial or {}
    prompt = f"""You are Skillow's independent verification and decision agent.
Audit the draft using the learner photo, user description and COMPLETE tutorial evidence.
Do not use frame matching or SAM as a correctness test.

COMPLETE LESSON: {json.dumps({k: v for k, v in lesson.items() if k != "_transcript"}, ensure_ascii=False)}
FULL TRANSCRIPT: {lesson.get("_transcript") or "Not available."}
WHOLE-TUTORIAL REVIEW: {json.dumps(whole, ensure_ascii=False)}
LEARNER DESCRIPTION (not visually verified): {user_context or "Not provided"}
DRAFT: {json.dumps(draft, ensure_ascii=False)[:10000]}
SAM DETECTIONS (supplementary object observations only): {sam_text}
DOMAIN SAFETY PROFILE: {json.dumps(prof, ensure_ascii=False)}

Tasks:
1. Verify each claim's evidence source. Remove unsupported claims and soften uncertain ones.
2. Explain what the learner has already done, what is correct, what differs, what is missing/incorrect, and what cannot be verified.
3. Choose the appropriate next step from the tutorial sequence. Explain why it matters.
4. Identify specific safety requirements. If any stop trigger applies, stop and give a clear reason; do not provide hazardous continuation steps.
5. Build a beginner-friendly guide of 2-6 steps only when safe. Each step must correspond to the tutorial or be explicitly adapted/general. Never fabricate timestamps.

Return ONLY JSON:
{{"audit":{{"removed":[{{"claim":"...","reason":"..."}}],"softened":["..."]}},
"comparison":{{"summary":"...","observed_components":["..."],"likely_fitting":null,
"what_already_done":["..."],"correct":["..."],"differences":["..."],"missing_or_incorrect":["..."],
"unknowns":["..."],"next_step":"...","why_next_step_matters":"...","safety_requirements":["..."],
"matches":[{{"text":"...","source":"both|learner|reference|inferred"}}],
"differences_evidence":[{{"text":"...","source":"learner|reference|inferred"}}],
"missing":[{{"text":"...","source":"reference|inferred"}}],
"confidence":"low|medium|high","safety_note":"...","stop_triggered":false,"stop_reason":null}},
"guide":{{"title":"...","overview":"...","prerequisites":["..."],
"steps":[{{"title":"...","instruction":"...","why_it_matters":"...","check_before_next_step":"...",
"basis":"tutorial|adapted|general","related_video_timestamp_seconds":null,"confidence":"low|medium|high"}}],
"stop_conditions":["..."]}}}}
"""
    return meta.responses_json([meta.text_part(prompt), meta.image_part(photo)], timeout=320)



def _timed(timings: dict, key: str, started: float):
    timings[key] = round(time.time() - started, 1)


def run_comparison(*, tutorial_id: int, lesson: dict, lesson_json: str, video_path: str, native_url: str | None,
                   photo_path: Path, duration: float, demo_cfg: dict, job: Job, user_context: str = "",
                   transcript: str = "") -> dict:
    domain = lesson.get("domain") or demo_cfg.get("domain") or "general"
    prof = profile(domain)
    uploads = settings.storage_dir / "uploads"
    token = hashlib.sha256(f"{tutorial_id}|{photo_path.name}|{hashlib.sha256(user_context.encode()).hexdigest()[:12]}|{VERSION}".encode()).hexdigest()[:16]
    trace, timings = [], {}
    lesson_context = dict(lesson)
    lesson_context["_transcript"] = transcript or ""
    source = lesson.get("_analysis_source", "frames")

    job.stage("understand", "running", "Reading the full tutorial sequence, transcript, tools, materials and safety")
    started = time.time()
    native_review_used = bool(native_url and settings.native_video)
    try:
        whole_tutorial = compare_entire_tutorial(native_url if native_review_used else None,
                                                 lesson_context, photo_path, user_context, transcript)
    except Exception as exc:
        if not native_review_used:
            raise
        logger.info("Native full-video comparison unavailable; retrying with complete lesson and transcript: %s", exc)
        native_review_used = False
        whole_tutorial = compare_entire_tutorial(None, lesson_context, photo_path, user_context, transcript)
    _timed(timings, "understand", started)
    trace.append({"phase": "Understand", "title": "Understood the complete tutorial and learner context",
                  "detail": ("Reviewed the full video together with the ordered lesson, transcript, tools, materials, outcomes and safety—not a matched frame."
                             if native_review_used else
                             "Used every extracted lesson stage and available transcript. Direct full-video review was unavailable, so visual details may be less complete.")})
    job.stage("understand", "done", "Whole-procedure understanding complete")

    current_step = None
    try:
        step_no = int(whole_tutorial.get("current_step"))
        current_step = next((x for x in _steps(lesson) if x["step_number"] == step_no), None)
    except (TypeError, ValueError):
        pass
    job.stage("moment", "skipped", "No single-frame matching is used")

    started = time.time()
    job.stage("setup", "running", "Checking visible objects as supplementary evidence")
    labels = choose_labels(current_step, prof["sam_defaults"])
    sam_learner_path = uploads / f"{token}_learner_overlay.jpg"
    sam_learner = analyse_image(str(photo_path), labels, sam_learner_path)
    _timed(timings, "setup", started)
    found = sum(1 for o in sam_learner["observations"] if o["count"])
    job.stage("setup", "done", f"Supplementary object detection: {found} of {len(labels)} labels")
    sam_text = "Supplementary learner photo detections only (not proof of function, correctness or safety): " + _sam_text("Learner photo", sam_learner)

    started = time.time()
    job.stage("compare", "running", "Comparing progress and procedure against the whole lesson")
    draft = _draft(lesson_context, photo_path, sam_text, prof, user_context, whole_tutorial)
    _timed(timings, "compare", started)
    job.stage("compare", "done")

    started = time.time()
    job.stage("guide", "running", "Independently auditing claims and deciding the next safe action")
    audited = _audit_and_guide(lesson_context, photo_path, draft, sam_text, prof, user_context, whole_tutorial)
    comparison = audited.get("comparison") or {}
    guide = audited.get("guide") or {}
    audit = audited.get("audit") or {}
    removed = [x for x in (audit.get("removed") or []) if isinstance(x, dict)][:10]

    valid_times = {x["key_moment_seconds"] for x in _steps(lesson) if x["timing_known"]}
    steps = []
    for raw in (guide.get("steps") or [])[:6]:
        if not isinstance(raw, dict):
            continue
        t = raw.get("related_video_timestamp_seconds")
        try:
            t = float(t) if t is not None and any(abs(float(t) - v) < 0.6 for v in valid_times) else None
        except (TypeError, ValueError):
            t = None
        basis = str(raw.get("basis") or "adapted").lower()
        steps.append({**{k: str(raw.get(k) or "").strip() for k in
                         ("title", "instruction", "why_it_matters", "check_before_next_step")},
                      "basis": basis if basis in ("tutorial", "adapted", "general") else "adapted",
                      "related_video_timestamp_seconds": t,
                      "confidence": str(raw.get("confidence") or "medium").lower()})
    steps = [x for x in steps if x["title"] or x["instruction"]]
    stop = bool(comparison.get("stop_triggered"))
    if stop:
        steps = []
    _timed(timings, "guide", started)
    trace.append({"phase": "Verify", "title": "Verified claims against the full tutorial and learner evidence",
                  "detail": f"{len(removed)} unsupported claim(s) removed; SAM was treated only as supplementary object evidence."})
    trace.append({"phase": "Decide", "title": "Paused for safety" if stop else "Selected the next appropriate action",
                  "detail": str(comparison.get("stop_reason") or comparison.get("next_step") or "The personalised guide follows the learner's current progress.")[:300]})

    confidence = str(comparison.get("confidence") or draft.get("confidence") or "low").lower()
    differences = comparison.get("differences") or comparison.get("differences_evidence") or draft.get("differences") or []
    missing = comparison.get("missing") or comparison.get("missing_or_incorrect") or draft.get("missing_or_incorrect") or []
    tutorial_context = {"stage_number": current_step["step_number"] if current_step else None,
                        "stage_title": current_step["title"] if current_step else None,
                        "phase": current_step["phase"] if current_step else None,
                        "summary": whole_tutorial.get("tutorial_overview") or lesson.get("summary")}
    result = {
        "analysis_version": VERSION, "complete": True, "lesson_hash": lesson_hash(lesson_json),
        "user_context": user_context, "domain": domain,
        "review_mode": "full_video_plus_structured_lesson" if native_review_used else "structured_lesson_transcript_and_photo",
        "plain_language_summary": str(comparison.get("summary") or draft.get("summary") or "").strip(),
        "learner_summary": str(draft.get("learner_summary") or whole_tutorial.get("learner_progress") or "").strip(),
        "observed_components": [str(x) for x in (comparison.get("observed_components") or draft.get("observed_components") or [])][:12],
        "what_already_done": [str(x) for x in (comparison.get("what_already_done") or draft.get("what_already_done") or [whole_tutorial.get("learner_progress", "")]) if x][:10],
        "correct": [str(x) for x in (comparison.get("correct") or draft.get("correct") or whole_tutorial.get("what_is_correct") or [])][:10],
        "differences": _items(differences), "missing": _items(missing),
        "missing_or_incorrect": [str(x) for x in (comparison.get("missing_or_incorrect") or draft.get("missing_or_incorrect") or whole_tutorial.get("missing_or_incorrect") or [])][:10],
        "unknowns": [str(x) for x in (comparison.get("unknowns") or draft.get("unknowns") or whole_tutorial.get("limits") or [])][:10],
        "next_step": str(comparison.get("next_step") or draft.get("next_step") or whole_tutorial.get("next_step") or ""),
        "why_next_step_matters": str(comparison.get("why_next_step_matters") or draft.get("why_next_step_matters") or whole_tutorial.get("why_next_step_matters") or ""),
        "safety_requirements": [str(x) for x in (comparison.get("safety_requirements") or draft.get("safety_requirements") or whole_tutorial.get("safety_requirements") or [])][:10],
        "confidence": confidence if confidence in ("low", "medium", "high") else "low",
        "safety_note": str(comparison.get("safety_note") or prof["guide_note"]),
        "stop_triggered": stop, "stop_reason": str(comparison.get("stop_reason") or "") if stop else "",
        "tutorial_context": tutorial_context, "sam": {"tutorial": {"overlay": False, "legend": [], "observations": []}, "learner": sam_learner},
        "learner_overlay_url": media_url(sam_learner_path) if sam_learner["overlay"] else None,
        "tutorial_overlay_url": None, "tutorial_frame_url": None,
        "repair_guide": {"title": str(guide.get("title") or "Your personalised next steps"),
                         "overview": str(guide.get("overview") or result_summary(comparison, draft)),
                         "prerequisites": [str(x) for x in (guide.get("prerequisites") or [])][:8],
                         "steps": steps, "stop_conditions": [str(x) for x in (guide.get("stop_conditions") or [])][:10]},
        "trace": trace, "timings": timings, "analysis_source": source,
        "whole_tutorial_review": {**whole_tutorial,
            "what_is_correct": whole_tutorial.get("what_is_correct", []),
            "gaps": whole_tutorial.get("gaps", []), "dependencies": whole_tutorial.get("dependencies", []),
            "limits": whole_tutorial.get("limits", [])}
    }
    result["illustration"] = {"url": None, "kind": "disabled", "status": "skipped"}
    result["repair_guide_image_url"] = None
    job.stage("guide", "done", "Audited written guide prepared")
    return result


def result_summary(comparison, draft):
    return str(comparison.get("next_step") or draft.get("next_step") or
               comparison.get("summary") or draft.get("summary") or "")
