import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import settings
from app.services import meta_client as meta

logger = logging.getLogger("skillow.sam")
PALETTE = [(83, 104, 246), (255, 144, 107), (46, 184, 142), (140, 117, 232), (240, 190, 60), (230, 90, 120)]


def choose_labels(stage: dict | None, defaults: list[str], limit: int = 4) -> list[str]:
    candidates = []
    if stage:
        candidates += list(stage.get("visible_items") or [])
    candidates += defaults
    labels = []
    for item in candidates:
        text = re.sub(r"\s+", " ", str(item).strip().lower())
        if 2 <= len(text) <= 40 and len(text.split()) <= 4 and text not in labels:
            labels.append(text)
    return labels[:limit]


def _stream(image_path: str, prompt: str) -> str:
    payload = {"model": settings.sam_model, "stream": True,
               "input": [{"type": "message", "role": "user",
                          "content": [meta.text_part(prompt), meta.image_part(image_path)]}],
               "metadata": {"mask_encoding": "one_bit", "include_confidence": "true"}}
    deltas = []
    with meta.gate:
        response = meta.session.post(meta.url("/responses"), headers=meta.headers(stream=True), json=payload,
                                     stream=True, timeout=(10, 240))
        try:
            if response.status_code >= 400:
                logger.warning("SAM failed: %s %s", response.status_code, response.text[:300])
                raise meta.MetaError(f"Segmentation was rejected ({response.status_code}).")
            for raw_line in response.iter_lines(decode_unicode=False):
                line = raw_line.decode("utf-8", errors="replace") if raw_line else ""
                if not line.startswith("data: "):
                    continue
                data = line.removeprefix("data: ")
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "response.output_text.delta" and event.get("delta"):
                    deltas.append(event["delta"])
        finally:
            response.close()
    return "".join(deltas)


def _parse(raw_output: str):
    from meta_sam_parser import CompletedOutcome, image_segmentation_format
    parser = image_segmentation_format().create_parser()
    parser.push(raw_output)
    return parser.finish(CompletedOutcome()).result


def _coverage(mask) -> float:
    from meta_sam_parser import decode_mask_to_raster
    raster = decode_mask_to_raster(mask)
    return round(100.0 * sum(1 for b in raster if b) / max(1, len(raster)), 1)


def segment_label(image_path: str, label: str) -> dict:
    try:
        parsed = _parse(_stream(image_path, label))
        from meta_sam_parser import records_of_kind
        masks = list(records_of_kind(parsed.records, "mask"))
        boxes = list(records_of_kind(parsed.records, "box"))
        coverage = []
        for mask in masks:
            try:
                coverage.append(_coverage(mask.mask))
            except Exception:
                pass
        return {"label": label, "count": max(len(masks), len(boxes)), "masks": masks,
                "coverage_pct": round(sum(coverage), 1) if coverage else None}
    except Exception as exc:
        logger.info("Segmentation for '%s' unavailable: %s", label, exc)
        return {"label": label, "count": 0, "masks": [], "coverage_pct": None, "error": True}


def render_overlay(image_path: str, results: list[dict], output_path: str) -> bool:
    try:
        from PIL import Image, ImageOps
        from meta_sam_parser import decode_mask_to_raster
        original = ImageOps.exif_transpose(Image.open(image_path)).convert("RGBA")
        overlay = Image.new("RGBA", original.size, (0, 0, 0, 0))
        drawn = False
        for index, result in enumerate(results):
            colour = PALETTE[index % len(PALETTE)]
            for record in result["masks"]:
                mask = record.mask
                raster = decode_mask_to_raster(mask)
                mask_image = Image.frombytes("L", (mask.width, mask.height), bytes(255 if b else 0 for b in raster))
                if mask_image.size != original.size:
                    mask_image = mask_image.resize(original.size)
                tint = Image.new("RGBA", original.size, colour + (110,))
                overlay.alpha_composite(Image.composite(tint, Image.new("RGBA", original.size), mask_image))
                drawn = True
        if not drawn:
            return False
        Image.alpha_composite(original, overlay).convert("RGB").save(output_path, "JPEG", quality=88)
        return True
    except Exception as exc:
        logger.info("Overlay rendering unavailable: %s", exc)
        return False


def analyse_image(image_path: str, labels: list[str], overlay_path: Path) -> dict:
    if not labels:
        return {"labels": [], "legend": [], "observations": [], "overlay": False}
    with ThreadPoolExecutor(max_workers=min(len(labels), settings.meta_concurrency)) as pool:
        results = list(pool.map(lambda label: segment_label(image_path, label), labels))
    found = [r for r in results if r["count"]]
    overlay = render_overlay(image_path, found, str(overlay_path)) if found else False
    colour_of = {r["label"]: PALETTE[i % len(PALETTE)] for i, r in enumerate(found)}
    return {
        "labels": labels, "overlay": overlay,
        "legend": [{"label": r["label"], "count": r["count"], "colour": "#%02x%02x%02x" % colour_of[r["label"]]}
                   for r in found] if overlay else [],
        "observations": [{"label": r["label"], "count": r["count"], "coverage_pct": r["coverage_pct"]} for r in results],
        "errors": sum(1 for r in results if r.get("error")),
    }
