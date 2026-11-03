import base64
import logging
from io import BytesIO
from pathlib import Path

from app.config import settings
from app.services import meta_client as meta

logger = logging.getLogger("skillow.diagrams")

ILLUSTRATION_STYLE = (
    "Create one focused, detailed instructional visual that directly teaches the supplied action. "
    "Use a clean light background, realistic but clearly legible component geometry, and a strong visual hierarchy. "
    "Show only the relevant tools, materials, parts and their actual spatial relationships. Use arrows only where "
    "they clarify the stated movement, sequence or flow. Use separate panels only when the written procedure is "
    "inherently sequential; do not make a collage. Never add generic troubleshooting content, decorative objects, "
    "unrequested circuit schematics, invented connections, unsafe actions, words, labels, letters, numbers, "
    "measurements or pseudo-text. If a detail is uncertain, depict it as a point to inspect rather than as fact. "
    "The visual must not contradict or go beyond the written context. "
)


def generate_diagram(prompt: str, output_path: str) -> str:
    payload = {"model": settings.muse_image_model, "input": ILLUSTRATION_STYLE + prompt}
    with meta.gate:
        response = meta.session.post(meta.url("/responses"), headers=meta.headers(), json=payload, timeout=(10, 300))
    if response.status_code >= 400:
        logger.warning("Image generation failed: %s %s", response.status_code, response.text[:300])
        raise meta.MetaError(f"Image generation was rejected ({response.status_code}).")
    result = response.json()
    image_b64 = next((i["result"] for i in result.get("output", []) or []
                      if i.get("type") == "image_generation_call" and i.get("result")), None)
    if not image_b64:
        image_b64 = next((i["b64_json"] for i in result.get("data", []) or [] if i.get("b64_json")), None)
    if not image_b64:
        raise meta.MetaError("The image model did not return an image.")
    try:
        from PIL import Image, ImageOps
        raw = base64.b64decode(image_b64, validate=True)
        with Image.open(BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            if image.width < 64 or image.height < 64:
                raise meta.MetaError("The image model returned an unusably small visual.")
            buffer = BytesIO()
            image.save(buffer, format="PNG", optimize=True)
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.stem + ".part.png")
        temporary.write_bytes(buffer.getvalue())
        temporary.replace(target)
    except meta.MetaError:
        raise
    except Exception as exc:
        raise meta.MetaError("The image model returned an invalid image.") from exc
    return output_path

