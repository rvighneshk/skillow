import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from io import BytesIO

from PIL import Image, ImageDraw

_buffer = BytesIO()
_image = Image.new("RGB", (320, 220), "white")
_draw = ImageDraw.Draw(_image)
_draw.rounded_rectangle((18, 18, 302, 202), radius=18, outline="#5268f5", width=5)
_draw.line((65, 110, 255, 110), fill="#e58a56", width=7)
_draw.ellipse((245, 95, 265, 115), fill="#36a77f")
_image.save(_buffer, format="PNG")
PNG = base64.b64encode(_buffer.getvalue()).decode()

STATE = {}
LOCK = threading.Lock()


def reset(**overrides):
    with LOCK:
        STATE.clear()
        STATE.update({"calls": [], "image_prompts": [], "inflight": 0, "max_inflight": 0, "native": "ok",
                      "stop": False, "sam": "ok", "junk_once": False, "image": "ok", "asr": "ok", "bad_timestamp": False})
        STATE.update(overrides)


def steps(duration=12):
    return [
        {"step_number": 1, "title": "Prepare the base", "instruction": "Crush the cookies.", "phase": "preparation",
         "start_seconds": 0, "end_seconds": 4, "key_moment_seconds": 2, "visual_observation": "Cookies in a bowl",
         "visible_items": ["bowl", "cookie"], "safety": [], "confidence": 0.9},
        {"step_number": 2, "title": "Layer cookies and cream", "instruction": "Alternate layers.", "phase": "assembly",
         "start_seconds": 4, "end_seconds": 9, "key_moment_seconds": 7, "visual_observation": "Cookies on cream",
         "visible_items": ["cookie", "cream layer", "ghost item"], "safety": [], "confidence": 0.85},
        {"step_number": 3, "title": "Serve a slice", "instruction": "Cut and plate.", "phase": "completion",
         "start_seconds": 9, "end_seconds": 99 if STATE.get("bad_timestamp") else duration, "key_moment_seconds": 11,
         "visual_observation": "Finished slice", "visible_items": ["slice", "plate"], "safety": [], "confidence": 0.8},
    ]


def lesson_payload():
    return {"domain": "cooking", "title": "Oreo cake", "summary": "A layered cake.", "tools": ["bowl"], "materials": ["cookies"],
            "safety_warnings": [], "transcript": "Instructor explains layering.", "steps": steps()}


def respond(payload):
    model = payload.get("model")
    content = payload["input"][0]["content"] if isinstance(payload["input"], list) else []
    text = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "input_text")
    kinds = [p.get("type") for p in content if isinstance(p, dict)]
    if model == "muse-image-1.0":
        return {"output": [{"type": "image_generation_call", "result": PNG}]}
    if "whole-procedure understanding agent" in text:
        return {"tutorial_overview": "A complete three-stage process with a final check.",
                "learner_progress": "The learner has begun the assembly stage, based on the photo and note.",
                "what_is_correct": ["The visible base components are present."],
                "what_differs": ["The final verification step has not been shown."],
                "missing_or_incorrect": ["A final function check is still required."],
                "next_step": "Complete the next assembly action, then perform the tutorial's final check.",
                "why_next_step_matters": "The sequence prevents testing an incomplete setup.",
                "safety_requirements": ["Disconnect power before changing wiring."],
                "current_step": 2, "gaps": ["Final test not shown"],
                "dependencies": ["Complete assembly before testing"],
                "limits": ["Photo cannot show hidden connections"], "confidence": "medium"}
    if "WHOLE-PROCEDURE REVIEW:" in text and "Return ONLY JSON" in text:
        return {"summary": "Your work is in progress and aligns with the tutorial so far.",
                "learner_summary": "Visible components are being assembled.",
                "observed_components": ["board", "wires"], "what_already_done": ["Placed the board and components."],
                "correct": ["The board and components are present."],
                "differences": ["The final check has not been completed."],
                "missing_or_incorrect": ["The function test remains."], "unknowns": ["Hidden connections"],
                "next_step": "Finish assembly and inspect every connection before powering.",
                "why_next_step_matters": "A final inspection catches shorts and incorrect connections.",
                "safety_requirements": ["Disconnect power before changing wiring."], "confidence": "medium"}
    if "independent verification and decision agent" in text:
        return {"audit": {"removed": [], "softened": []},
                "comparison": {"summary": "Your work is in progress and follows the main sequence.",
                               "observed_components": ["board", "wires"], "what_already_done": ["Placed the board and components."],
                               "correct": ["The board and components are present."],
                               "differences": ["The final check has not been completed."],
                               "missing_or_incorrect": ["The function test remains."], "unknowns": ["Hidden connections"],
                               "next_step": "Finish assembly and inspect every connection before powering.",
                               "why_next_step_matters": "A final inspection catches shorts and incorrect connections.",
                               "safety_requirements": ["Disconnect power before changing wiring."],
                               "confidence": "medium", "safety_note": "Disconnect power before changing connections.",
                               "stop_triggered": STATE["stop"], "stop_reason": "Raw egg risk" if STATE["stop"] else None},
                "guide": {"title": "Complete and check the assembly", "overview": "Finish the remaining assembly and inspect it.",
                          "prerequisites": ["Power disconnected"],
                          "steps": [{"title": "Finish assembly", "instruction": "Complete the remaining connections.",
                                     "why_it_matters": "The setup must be complete before testing.",
                                     "check_before_next_step": "Inspect the full sequence against the tutorial.",
                                     "basis": "tutorial", "related_video_timestamp_seconds": 7, "confidence": "high"}],
                          "stop_conditions": ["Stop if a component is damaged."]}}
    if "Watch the COMPLETE tutorial video" in text:
        return {"tutorial_overview": "Three stages in sequence.", "learner_progress": "Assembly underway.",
                "current_step": 2, "reference_moment_seconds": 8, "next_step": "Continue assembly.",
                "gaps": ["Final test not shown"], "dependencies": ["Complete wiring before powering"],
                "confidence": "medium", "limits": ["Photo cannot show hidden connections"]}
    if "Watch the whole tutorial video" in text:
        return lesson_payload()
    if "sampled frames from the tutorial" in text:
        return lesson_payload()
    if "auditing a draft tutorial analysis" in text:
        return {"checks": [{"step_number": 1, "verdict": "supported", "confidence": 0.9, "unsupported_items": [], "note": "ok"},
                           {"step_number": 2, "verdict": "partly", "confidence": 0.5, "unsupported_items": ["ghost item"], "note": "no ghost"},
                           {"step_number": 3, "verdict": "unsupported", "confidence": 0.9, "unsupported_items": [], "note": "title card"}]}
    return {"error": "unrecognised", "kinds": kinds}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        with LOCK:
            STATE["inflight"] += 1
            STATE["max_inflight"] = max(STATE["max_inflight"], STATE["inflight"])
        try:
            if self.path.startswith("/asr/transcribe"):
                with LOCK:
                    STATE["calls"].append("asr")
                if STATE["asr"] == "fail":
                    return self._send(500, {"error": "boom"})
                return self._send(200, {"segments": [{"text": "Welcome to the tutorial."}]})
            payload = json.loads(raw)
            content = payload["input"][0]["content"] if isinstance(payload["input"], list) else []
            has_video = any(isinstance(p, dict) and p.get("type") == "input_video" for p in content)
            prompt_text = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "input_text")
            if payload.get("model") == "muse-image-1.0":
                with LOCK:
                    STATE["image_prompts"].append(str(payload.get("input", "")))
            if "practical learning assistant" in prompt_text:
                with LOCK:
                    STATE["calls"].append("qa")
                return self._send(200, {"output_text": "## In this tutorial\nThe instructor stirs the rice so the ingredients cook more evenly.\n\n- Keep the heat controlled.\n- Stir gently and check the texture.\n\n**Why it matters:** movement helps distribute heat and moisture."})
            if payload.get("model") == "sam-3.1":
                label = next(p["text"] for p in content if p.get("type") == "input_text")
                with LOCK:
                    STATE["calls"].append(f"sam:{label}")
                if STATE["sam"] == "fail":
                    return self._send(500, {"error": "sam down"})
                records = [] if label == "ghost item" else [{"kind": "mask", "w": 8, "h": 8, "bits": [1] * 16 + [0] * 48}]
                body = "".join(f'data: {json.dumps({"type": "response.output_text.delta", "delta": json.dumps(records)})}\n\n') + "data: [DONE]\n\n"
                return self._send(200, body.encode(), "text/event-stream")
            if has_video:
                with LOCK:
                    STATE["calls"].append("video")
                if STATE["native"] == "fail":
                    return self._send(400, {"error": "unsupported video secret-key-123"})
            else:
                with LOCK:
                    STATE["calls"].append("model:" + str(payload.get("model")))
            if STATE["junk_once"] and "whole-procedure understanding agent" in json.dumps(payload):
                STATE["junk_once"] = False
                return self._send(200, {"output_text": "not json at all"})
            result = respond(payload)
            if payload.get("model") == "muse-image-1.0":
                if STATE["image"] == "fail":
                    return self._send(500, {"error": "img"})
                return self._send(200, result)
            return self._send(200, {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(result)}]}]})
        finally:
            with LOCK:
                STATE["inflight"] -= 1


def serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
