import base64
import json
import logging
import threading
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.config import settings

logger = logging.getLogger("skillow.meta")


class MetaError(RuntimeError):
    pass


_retry = Retry(total=2, backoff_factor=1.5, status_forcelist=(429, 502, 503, 504),
               allowed_methods=frozenset({"POST", "GET"}), respect_retry_after_header=True,
               raise_on_status=False)
session = requests.Session()
for _scheme in ("https://", "http://"):
    session.mount(_scheme, HTTPAdapter(max_retries=_retry, pool_connections=8, pool_maxsize=16))
gate = threading.BoundedSemaphore(max(1, settings.meta_concurrency))


def url(path: str) -> str:
    return settings.meta_base_url.rstrip("/") + path


def headers(stream: bool = False) -> dict:
    if not settings.model_api_key:
        raise MetaError("MODEL_API_KEY is missing. Add it to your .env file.")
    result = {"Authorization": f"Bearer {settings.model_api_key}", "Content-Type": "application/json"}
    if stream:
        result["Accept"] = "text/event-stream"
    return result


@lru_cache(maxsize=64)
def _encode(path: str, mtime: float, max_side: int, quality: int) -> str:
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((max_side, max_side))
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def image_data_url(path, max_side: int = 1280, quality: int = 84) -> str:
    p = Path(path)
    return _encode(str(p), p.stat().st_mtime, max_side, quality)


def extract_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"].strip()
    chunks = []
    for item in payload.get("output", []) or []:
        if item.get("type") == "message":
            for part in item.get("content", []) or []:
                if part.get("type") in ("output_text", "text") and part.get("text"):
                    chunks.append(part["text"])
    if not chunks:
        raise MetaError("The AI service returned no text output.")
    return "\n".join(chunks).strip()


def parse_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise MetaError("The AI service did not return structured data.")
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise MetaError("The AI service returned malformed structured data.") from exc


def responses_text(content: list, model: str | None = None, timeout: int = 240) -> str:
    payload = {"model": model or settings.muse_spark_model,
               "input": [{"type": "message", "role": "user", "content": content}]}
    with gate:
        response = session.post(url("/responses"), headers=headers(), json=payload, timeout=(10, timeout))
    if response.status_code >= 400:
        logger.warning("Meta /responses failed: %s %s", response.status_code, response.text[:600])
        raise MetaError(f"The AI service rejected the request ({response.status_code}).")
    return extract_text(response.json())


def responses_json(content: list, model: str | None = None, timeout: int = 240, attempts: int = 2) -> dict:
    last = None
    for _ in range(attempts):
        try:
            return parse_json(responses_text(content, model, timeout))
        except MetaError as exc:
            last = exc
            if "structured data" not in str(exc):
                raise
    raise last


def text_part(text: str) -> dict:
    return {"type": "input_text", "text": text}


def image_part(path, max_side: int = 1280) -> dict:
    return {"type": "input_image", "image_url": image_data_url(path, max_side)}


def public_error(exc: Exception) -> str:
    if isinstance(exc, MetaError):
        return str(exc)
    if isinstance(exc, requests.Timeout):
        return "The AI service took too long to respond. Please try again."
    if isinstance(exc, requests.ConnectionError):
        return "Could not reach the AI service. Check your connection and try again."
    logger.exception("Unexpected error")
    return "Something went wrong while processing this request. Please try again."
