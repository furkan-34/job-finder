"""Search queries for the fetch stage: data/searches/searches.json.

    {
      "updated_at": "2026-10-23",
      "searches": [
        {"id": "senior-backend-istanbul", "keywords": "Senior Backend Engineer hybrid",
         "location": "Istanbul, Turkey", "limit": 25, "enabled": true}
      ]
    }

The /setup-searches command writes the file; these helpers validate and summarise it.
Each enabled search runs once per chosen actor (config/apify_actors.json).
"""

import json
import re
from datetime import date
from pathlib import Path

from jobfinder.paths import SEARCHES_JSON
from jobfinder.sources.actors import cost_per_item

MAX_LIMIT = 1000  # the LinkedIn actor returns at most 1,000 postings per search
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def load(path: Path = SEARCHES_JSON) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run /setup-searches first.")
    return json.loads(path.read_text(encoding="utf-8"))


def validate(data: dict) -> list[str]:
    """Return human-readable problems; an empty list means the file is usable."""
    if not isinstance(data, dict) or not isinstance(data.get("searches"), list):
        return ["top level must be an object with a 'searches' list"]
    errors, seen = [], set()
    for i, search in enumerate(data["searches"]):
        at = f"/searches/{i}"
        if not isinstance(search, dict):
            errors.append(f"{at}: must be an object")
            continue
        sid = search.get("id")
        if not isinstance(sid, str) or not ID_PATTERN.match(sid):
            errors.append(f"{at}/id: lowercase letters, digits and '-' only")
        elif sid in seen:
            errors.append(f"{at}/id: duplicate '{sid}'")
        seen.add(sid)
        for field in ("keywords", "location"):
            if not isinstance(search.get(field), str) or not search[field].strip():
                errors.append(f"{at}/{field}: must be a non-empty string")
        limit = search.get("limit")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
            errors.append(f"{at}/limit: must be an integer from 1 to {MAX_LIMIT}")
        if not isinstance(search.get("enabled", True), bool):
            errors.append(f"{at}/enabled: must be true or false")
    if not errors and not enabled(data):
        errors.append("no enabled searches")
    return errors


def save(data: dict, path: Path = SEARCHES_JSON) -> dict:
    """Validate and write the file (the web UI saves it; /setup-searches writes it itself)."""
    errors = validate(data)
    if errors:
        raise ValueError("; ".join(errors))
    data = {**data, "updated_at": date.today().isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def enabled(data: dict) -> list[dict]:
    return [s for s in data["searches"] if s.get("enabled", True)]


def summary(data: dict, actors: list[dict]) -> dict:
    """Totals per run with these actors: how many postings at most, and what that costs at most."""
    active = enabled(data)
    total = sum(s["limit"] for s in active)
    return {
        "searches": [{k: s[k] for k in ("id", "keywords", "location", "limit")} for s in active],
        "data_sources": [a["name"] for a in actors],
        "max_postings_per_run": total * len(actors),
        "max_cost_per_run_usd": max_cost(data, actors),
    }


def max_cost(data: dict, actors: list[dict]) -> float:
    total = sum(s["limit"] for s in enabled(data))
    return round(total * sum(cost_per_item(a) for a in actors), 4)
