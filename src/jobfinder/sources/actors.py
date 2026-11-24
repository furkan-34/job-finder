"""The Apify actors the fetch stage can use: config/apify_actors.json.

Each entry's `name` is what a job's `data_source` field says, its `input` and
`output` tell jobfinder.sources.mapping how to call the actor and how to map its
items to the standard job shape, and `site` prefixes job ids, so the same
posting found by two actors for one site is stored once. An entry with `"kind": "freehire"`
is not an Apify actor but freehire.me's free API (jobfinder.sources.freehire), and one with
`"kind": "greenhouse"`, `"lever"` or `"ashby"` reads that ATS's free company job boards
(jobfinder.sources.ats).
"""

import json
import re
from pathlib import Path

from jobfinder.paths import ACTORS_JSON
from jobfinder.sources.apify import SourceError
from jobfinder.sources.mapping import check_spec

NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
ALL = "all"


def load(path: Path = ACTORS_JSON) -> list[dict]:
    actors = json.loads(path.read_text(encoding="utf-8"))["actors"]
    names = [a.get("name") for a in actors]
    for actor in actors:
        if not isinstance(actor.get("name"), str) or not NAME_PATTERN.match(actor["name"]):
            raise SourceError(f"{path}: actor name {actor.get('name')!r} must be UPPER_SNAKE_CASE")
        if names.count(actor["name"]) > 1:
            raise SourceError(f"{path}: actor name {actor['name']} is listed twice")
        if problems := check_spec(actor):
            raise SourceError(f"{path}: {actor['name']}: {'; '.join(problems)}")
    return actors


def uses_apify(actors: list[dict]) -> bool:
    """True when one of them needs the Apify token and credit."""
    return any(a.get("kind", "apify") == "apify" for a in actors)


def select(actors: list[dict], names: list[str] | None) -> list[dict]:
    """Pick actors by name; None, [] or ["all"] means every listed actor."""
    if not names or ALL in names:
        return actors
    by_name = {a["name"]: a for a in actors}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise SourceError(f"Unknown data source(s): {', '.join(unknown)}. "
                          f"Listed: {', '.join(by_name)}")
    return [by_name[n] for n in dict.fromkeys(names)]


def cost_per_item(actor: dict) -> float:
    return actor["cost_per_1000_results_usd"] / 1000
