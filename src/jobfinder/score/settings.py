"""Scoring settings: data/score/settings.json.

    {"matcher_model": "sonnet", "min_score": 70, "shortlist_size": 10, "max_per_run": 50}

/setup-job-scoring writes them with `jobfinder score configure`; every scoring run reads them.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from jobfinder.paths import SCORE_SETTINGS_JSON

MATCHER_MODELS = ("sonnet", "opus", "haiku")
DEFAULTS = {"matcher_model": "sonnet", "min_score": 70, "shortlist_size": 10, "max_per_run": 50}
LIMITS = {"min_score": (0, 100), "shortlist_size": (1, 50), "max_per_run": (1, 500)}


class SettingsError(Exception):
    pass


def load(path: Path = SCORE_SETTINGS_JSON) -> dict:
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {**DEFAULTS, **saved}


def save(settings: dict, path: Path = SCORE_SETTINGS_JSON) -> dict:
    if settings["matcher_model"] not in MATCHER_MODELS:
        raise SettingsError(f"matcher_model must be one of {', '.join(MATCHER_MODELS)}")
    for key, (low, high) in LIMITS.items():
        if not isinstance(settings[key], int) or not low <= settings[key] <= high:
            raise SettingsError(f"{key} must be a whole number from {low} to {high}")
    settings = {**settings, "updated_at": datetime.now(UTC).isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return settings
