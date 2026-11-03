import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests import fake_meta, stubs  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="skillow_test_"))
server, base = fake_meta.serve()
os.environ.update(MODEL_API_KEY="secret-key-123", META_BASE_URL=base, STORAGE_DIR=str(TMP / "storage"), META_CONCURRENCY="4")
stubs.install()

from app.config import settings  # noqa: E402
from app.services import comparison as cmp, jobs, meta_client as meta  # noqa: E402
from app.services import tutorial_analyzer as ta  # noqa: E402
from app.services.lesson_analysis import STAGES, analyze_tutorial  # noqa: E402

VIDEO = TMP / "demo.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=duration=12:size=640x360:rate=15",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(VIDEO)], check=True)
PHOTO = TMP / "learner.jpg"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x480", "-frames:v", "1", str(PHOTO)], check=True)
NATIVE = "https://example.com/tutorial.mp4"
results = []


def job(stages=STAGES):
    return jobs.Job(("test",), stages)


def run_analysis(native=NATIVE, work="w"):
    return analyze_tutorial(str(VIDEO), "Oreo cake", native, "cooking", settings.storage_dir / "frames" / work, job())


def run_compare(lesson, lesson_json, native=NATIVE, tid=1, demo_cfg=None):
    return cmp.run_comparison(tutorial_id=tid, lesson=lesson, lesson_json=lesson_json, video_path=str(VIDEO), native_url=native,
                              photo_path=PHOTO, duration=12.0, demo_cfg=demo_cfg or {}, job=job(cmp.STAGES))


def check(fn):
    try:
        fn()
        results.append((fn.__name__, True, ""))
    except Exception:
        results.append((fn.__name__, False, traceback.format_exc(limit=4)))
    return fn


@check
def normalise_drops_impossible_timestamps_and_sorts():
    data = {"steps": [{"title": "B", "start_seconds": 5, "end_seconds": 6, "key_moment_seconds": 40, "phase": "weird"},
                      {"title": "A", "start_seconds": 1, "end_seconds": 2, "confidence": 5}]}
    lesson = ta.normalise_lesson(data, 12.0, "native_video", "cooking")
    first, second = lesson["steps"]
    assert first["title"] == "A" and first["confidence"] == 1.0
    assert second["phase"] == "demonstration" and 5 <= second["key_moment_seconds"] <= 6
    assert lesson["domain"] == "cooking" and [s["step_number"] for s in lesson["steps"]] == [1, 2]


@check
def normalise_rejects_empty_lessons():
    try:
        ta.normalise_lesson({"steps": []}, 10.0, "frames")
    except meta.MetaError:
        return
    raise AssertionError("expected MetaError")


@check
def native_video_path_sends_video_and_skips_bulk_frames():
    fake_meta.reset()
    transcript, lesson, duration = run_analysis(work="native")
    assert lesson["_analysis_source"] == "native_video" and "video" in fake_meta.STATE["calls"]
    assert not list((settings.storage_dir / "frames" / "native").glob("frame_*.jpg"))
    assert "Welcome" in transcript and 11 < duration < 13
    v = lesson["verification"]
    assert v["available"] and (v["supported"], v["partly"], v["unsupported"]) == (1, 1, 1) and v["items_removed"] == 1
    by = {s["step_number"]: s for s in lesson["steps"]}
    assert by[1]["verified"] is True and by[2]["verified"] == "partial" and by[3]["verified"] is False
    assert "ghost item" not in by[2]["visible_items"] and by[3]["confidence"] < 0.8


@check
def out_of_range_timestamp_from_model_is_discarded():
    fake_meta.reset(bad_timestamp=True)
    _, lesson, _ = run_analysis(work="badts")
    last = lesson["steps"][2]
    assert last["end_seconds"] <= 12.6


@check
def native_failure_falls_back_to_frames_without_leaking_details():
    fake_meta.reset(native="fail")
    _, lesson, _ = run_analysis(work="fallback")
    assert lesson["_analysis_source"] == "frames" and lesson.get("_native_error")
    assert "secret-key-123" not in lesson["_native_error"] and "unsupported video" not in lesson["_native_error"]
    assert list((settings.storage_dir / "frames" / "fallback").glob("frame_*.jpg"))


@check
def uploads_without_public_url_use_frames_and_never_send_video():
    fake_meta.reset()
    _, lesson, _ = run_analysis(native=None, work="noconf")
    assert lesson["_analysis_source"] == "frames" and "video" not in fake_meta.STATE["calls"]


@check
def transcription_failure_is_not_fatal():
    fake_meta.reset(asr="fail")
    transcript, lesson, _ = run_analysis(work="asrfail")
    assert lesson["steps"] and transcript


@check
def whole_tutorial_comparison_uses_transcript_and_no_matched_frame():
    fake_meta.reset()
    _, lesson, _ = run_analysis(work="c1")
    out = run_compare(lesson, "L1", tid=11)
    assert out["complete"] and out["analysis_version"] == cmp.VERSION
    assert out["tutorial_context"]["stage_number"] == 2
    assert "match" not in out and "reference" not in out
    assert out["tutorial_overlay_url"] is None
    sam_calls = [c for c in fake_meta.STATE["calls"] if c.startswith("sam:")]
    assert sam_calls and 0 < len(sam_calls) <= 8
    assert {t["phase"] for t in out["trace"]} == {"Understand", "Verify", "Decide"}
    assert out["repair_guide"]["steps"][0]["related_video_timestamp_seconds"] == 7
    assert out["illustration"]["status"] == "ok" and out["illustration"]["kind"] == "contextual_instruction"
    assert out["next_step"] and out["correct"] and out["differences"] and out["unknowns"]
    assert fake_meta.STATE["image_prompts"] and out["next_step"] in fake_meta.STATE["image_prompts"][-1]
    assert fake_meta.STATE["max_inflight"] <= 4


@check
def structured_lesson_fallback_compares_without_frame_matching():
    fake_meta.reset()
    _, lesson, _ = run_analysis(native=None, work="c2")
    out = run_compare(lesson, "L2", native=None, tid=12)
    assert "match" not in out and "reference" not in out
    assert out["tutorial_overlay_url"] is None


@check
def native_video_review_uses_complete_tutorial_not_a_matched_moment():
    fake_meta.reset()
    _, lesson, _ = run_analysis(work="c3")
    fake_meta.STATE["calls"].clear()
    out = run_compare(lesson, "L3", tid=13)
    assert fake_meta.STATE["calls"].count("video") == 1
    assert "reference" not in out and "match" not in out
    assert any(t["title"] == "Understood the complete tutorial and learner context" for t in out["trace"])


@check
def full_video_and_structured_lesson_are_reviewed_once():
    fake_meta.reset()
    _, lesson, _ = run_analysis(work="c4")
    fake_meta.STATE["calls"].clear()
    out = run_compare(lesson, "L4", tid=14)
    assert fake_meta.STATE["calls"].count("video") == 1 and out["whole_tutorial_review"]


@check
def curated_frame_moments_are_not_used_as_comparison_basis():
    fake_meta.reset()
    _, lesson, _ = run_analysis(native=None, work="c5")
    out = run_compare(lesson, "L5", native=None, tid=15, demo_cfg={"reference_moments": [{"phase": "assembly", "seconds": 6, "label": "Layering"}]})
    assert "reference" not in out and "match" not in out


@check
def safety_trigger_stops_and_removes_guide_steps():
    fake_meta.reset(stop=True)
    _, lesson, _ = run_analysis(work="c6")
    out = run_compare(lesson, "L6", tid=16)
    assert out["stop_triggered"] and out["repair_guide"]["steps"] == [] and "Raw egg" in out["stop_reason"]
    assert any(t["title"] == "Paused for safety" for t in out["trace"])


@check
def sam_and_image_failures_do_not_break_comparison():
    fake_meta.reset(sam="fail", image="fail")
    _, lesson, _ = run_analysis(work="c7")
    out = run_compare(lesson, "L7", tid=17)
    assert out["complete"] and out["learner_overlay_url"] is None
    assert out["illustration"]["status"] == "failed" and out["repair_guide_image_url"] is None


@check
def electronics_visual_is_contextual_raster_not_automatic_svg_schematic():
    fake_meta.reset()
    _, lesson, _ = run_analysis(work="c8")
    lesson["domain"] = "electronics"
    fake_meta.STATE["calls"].clear()
    out = run_compare(lesson, "L8", tid=18)
    assert out["illustration"]["kind"] == "contextual_instruction" and out["illustration"]["url"].endswith(".png")
    assert "model:muse-image-1.0" in fake_meta.STATE["calls"]


@check
def malformed_json_is_retried_once():
    fake_meta.reset(junk_once=True)
    _, lesson, _ = run_analysis(work="c9")
    result = cmp.compare_entire_tutorial(NATIVE, lesson, PHOTO, "assembling", "spoken transcript")
    assert result["tutorial_overview"] and fake_meta.STATE["junk_once"] is False


@check
def cache_validity_depends_on_version_completeness_and_lesson_hash():
    h = cmp.lesson_hash("A")
    good = {"analysis_version": cmp.VERSION, "complete": True, "lesson_hash": h}
    assert cmp.is_current(good, h)
    assert not cmp.is_current({**good, "lesson_hash": cmp.lesson_hash("B")}, h)
    assert not cmp.is_current({**good, "complete": False}, h)
    assert not cmp.is_current({**good, "analysis_version": 1}, h)
    assert not cmp.is_current({}, h)


@check
def api_errors_never_expose_keys_or_response_bodies():
    fake_meta.reset(native="fail")
    try:
        meta.responses_text([meta.text_part("x"), {"type": "input_video", "video_url": NATIVE}])
    except meta.MetaError as exc:
        assert "secret-key-123" not in str(exc) and "unsupported video" not in str(exc) and "400" in str(exc)
        return
    raise AssertionError("expected MetaError")


@check
def identical_jobs_are_deduplicated_while_running():
    import threading
    gate = threading.Event()
    first = jobs.start(("dup", 1), STAGES, lambda j: gate.wait(5) or {"ok": 1})
    second = jobs.start(("dup", 1), STAGES, lambda j: {"ok": 2})
    assert first.id == second.id
    gate.set()
    for _ in range(50):
        if first.status != "running":
            break
        import time
        time.sleep(0.05)
    assert first.status == "done" and jobs.start(("dup", 1), STAGES, lambda j: {"ok": 3}).id != first.id


@check
def failed_jobs_report_a_safe_message():
    import time
    failing = jobs.start(("fail", 1), STAGES, lambda j: (_ for _ in ()).throw(RuntimeError("secret-key-123 /home/user/x")))
    for _ in range(50):
        if failing.status != "running":
            break
        time.sleep(0.05)
    assert failing.status == "failed" and "secret-key-123" not in failing.error and "/home" not in failing.error



@check
def native_video_comparison_failure_retries_with_lesson_and_transcript():
    fake_meta.reset(native="fail")
    _, lesson, _ = run_analysis(native=None, work="native_compare_fallback")
    fake_meta.STATE["calls"].clear()
    out = run_compare(lesson, "fallback lesson", tid=30, native=NATIVE)
    assert fake_meta.STATE["calls"].count("video") == 1
    assert out["review_mode"] == "structured_lesson_transcript_and_photo"
    assert "reference" not in out and "match" not in out and out["next_step"]


@check
def each_demo_launch_pairs_its_configured_video_and_setup_photo():
    import json
    from types import SimpleNamespace
    import app.routes as routes
    cases = json.loads((ROOT / "config" / "demo_cases.json").read_text())
    original_asset, original_launch = routes._public_asset, routes._launch
    downloads = []
    def fake_asset(url, destination, max_bytes):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if url.endswith(".mp4"):
            shutil.copy2(VIDEO, destination)
        else:
            from PIL import Image
            Image.new("RGB", (160, 120), "white").save(destination, format="PNG")
        downloads.append((url, destination))
    def fake_launch(db, video_path, title, filename, native_url, domain):
        return SimpleNamespace(id=900 + len(downloads), title=title)
    try:
        routes._public_asset, routes._launch = fake_asset, fake_launch
        for slug, cfg in cases.items():
            downloads.clear()
            response = routes.start_demo(slug, None)
            assert response.status_code == 303
            assert response.headers["location"].startswith("/tutorials/")
            assert [x[0] for x in downloads] == [cfg["video_url"], cfg["setup_image_url"]]
            assert downloads[0][1].suffix == ".mp4" and downloads[0][1].exists()
            assert downloads[1][1].suffix == ".png" and downloads[1][1].exists()
    finally:
        routes._public_asset, routes._launch = original_asset, original_launch

@check
def all_three_demo_domains_use_whole_procedure_comparison():
    import json
    cases = json.loads((ROOT / "config" / "demo_cases.json").read_text())
    assert set(cases) == {"arduino", "plumbing", "cooking"}
    for slug, cfg in cases.items():
        assert cfg["video_url"].endswith(f"/assets/{slug}.mp4")
        assert cfg["setup_image_url"].endswith(f"/assets/{slug}.png")
        lesson = {"title": cfg["title"], "domain": cfg["domain"], "summary": cfg["description"],
                  "tools": ["tool from tutorial"], "materials": ["materials from tutorial"],
                  "safety_warnings": ["Check safety before proceeding."],
                  "steps": [{"step_number": 1, "title": "Follow the procedure", "instruction": "Perform the next action.",
                             "phase": "assembly", "start_seconds": 0, "end_seconds": 10,
                             "key_moment_seconds": 5, "timing_known": True, "visible_items": [],
                             "safety": ["Use the appropriate precaution."], "expected_outcome": "Expected result."}]}
        fake_meta.reset()
        out = run_compare(lesson, json.dumps(lesson), native=None, tid=100 + len(slug), demo_cfg=cfg)
        assert out["complete"] and out["analysis_version"] == cmp.VERSION
        assert "reference" not in out and "match" not in out
        assert out["whole_tutorial_review"]["tutorial_overview"]
        assert out["next_step"] and out["why_next_step_matters"]
        assert out["illustration"]["kind"] == "contextual_instruction"
        assert out["illustration"]["status"] == "ok"


if __name__ == "__main__":
    width = max(len(n) for n, _, _ in results)
    for name, ok, tb in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            print(tb)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)


def test_normalise_preserves_detailed_substeps_and_more_than_ten_stages():
    from app.services.tutorial_analyzer import normalise_lesson
    data = {"title": "Detailed tutorial", "steps": [
        {"title": f"Action {i}", "instruction": f"Do action {i} carefully.",
         "substeps": ["Position the part", "Align it", "Secure it"]}
        for i in range(1, 16)
    ]}
    lesson = normalise_lesson(data, 600, "native_video")
    assert len(lesson["steps"]) == 15
    assert lesson["steps"][0]["substeps"] == ["Position the part", "Align it", "Secure it"]
    assert "3. Secure it" in lesson["steps"][0]["instruction"]
    assert lesson["steps"][-1]["step_number"] == 15
