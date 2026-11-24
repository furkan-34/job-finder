"""Run settings of the job search: data/fetch/settings.json.

    {"data_sources": ["all"], "date_posted": "anyTime", "exclude_words": []}

`exclude_words`: a posting with one of these words in its title, company or description is
filtered out by the rule filter (stage 2d), whatever the profile's dealbreakers say.

/setup-job-search writes them with `jobfinder fetch configure`; every run reads them.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from jobfinder.paths import FETCH_SETTINGS_JSON
from jobfinder.sources.apify import DATE_POSTED, SourceError

DEFAULTS = {"data_sources": ["all"], "date_posted": "anyTime", "exclude_words": []}
MAX_WORDS, MAX_WORD_LENGTH = 100, 60
DATA_SOURCES_ENV = "JOBFINDER_DATA_SOURCES"  # optional override: comma-separated actor names


def load(path: Path = FETCH_SETTINGS_JSON) -> dict:
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    saved.pop("schedule", None)  # left by the removed recurring run
    return {**DEFAULTS, **saved}


def save(settings: dict, path: Path = FETCH_SETTINGS_JSON) -> dict:
    if settings["date_posted"] not in DATE_POSTED:
        raise SourceError(f"date_posted must be one of {', '.join(DATE_POSTED)}")
    settings = {**settings, "exclude_words": clean_words(settings.get("exclude_words", [])),
                "updated_at": datetime.now(UTC).isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return settings


def clean_words(words) -> list[str]:
    """The words trimmed, empty ones dropped, each once (case ignored); SourceError when not a list of short text."""
    if not isinstance(words, list) or not all(isinstance(word, str) for word in words):
        raise SourceError("exclude_words must be a list of words")
    clean = list({word.casefold(): word for word in reversed([" ".join(w.split()) for w in words if w.strip()])}.values())
    clean.reverse()  # each word once, case ignored, in the order first written
    if len(clean) > MAX_WORDS or any(len(word) > MAX_WORD_LENGTH for word in clean):
        raise SourceError(f"exclude_words: at most {MAX_WORDS} words of {MAX_WORD_LENGTH} characters")
    return clean
