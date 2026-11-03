import json
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent.parent / "config"
DOMAINS = ("plumbing", "cooking", "electronics", "general")
PHASES = ("preparation", "assembly", "intermediate", "completion", "demonstration")


@lru_cache(maxsize=1)
def _profiles() -> dict:
    return json.loads((CONFIG / "domain_profiles.json").read_text(encoding="utf-8"))


def profile(domain: str) -> dict:
    return _profiles().get(domain) or _profiles()["general"]


def clean_domain(value) -> str:
    value = str(value or "").strip().lower()
    return value if value in DOMAINS else "general"


def demo_cases() -> dict:
    path = CONFIG / "demo_cases.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
