"""CV settings: data/cv/settings.json.

    {"writer_model": "sonnet", "review": true}

/setup-cv writes them with `jobfinder cv configure`; every /execute-cv run reads them. `review`:
the cv-reviewer agent reads each saved CV and letter as a hiring manager would and returns a
revised answer, checked and rendered again (one more agent run per posting). On by default.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from jobfinder.paths import CV_SETTINGS_JSON

WRITER_MODELS = ("sonnet", "opus")
DEFAULTS = {"writer_model": "sonnet", "review": True}


class SettingsError(Exception):
    pass


def load(path: Path = CV_SETTINGS_JSON) -> dict:
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {**DEFAULTS, **saved}


def save(settings: dict, path: Path = CV_SETTINGS_JSON) -> dict:
    if settings["writer_model"] not in WRITER_MODELS:
        raise SettingsError(f"writer_model must be one of {', '.join(WRITER_MODELS)}")
    if not isinstance(settings["review"], bool):
        raise SettingsError("review must be true or false")
    settings = {**settings, "updated_at": datetime.now(UTC).isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return settings
