"""freehire.me: a free JSON API, no token, for the actor entries with "kind": "freehire".

    GET /api/v1/agent/jobs/search?q=...&countries=tr&limit=25&offset=0&posted_within_days=7
    -> {"data": [job, ...], "meta": {"limit", "offset", "total"}}

`data` items carry the full description, so one request is one page of postings. The fields
are mapped to the standard job shape by jobfinder.sources.mapping like an Apify actor's output.
The search is by country (`countries`, ISO 3166 alpha-2): the part of the search's location
after the last comma is looked up in the entry's `countries`; the city is not used. Posting
text is untrusted, like any actor's output: the safety check of stage 2c runs on it first.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from jobfinder.sources.apify import SourceError

BASE_URL = "https://freehire.me/api/v1/agent/jobs/search"
USER_AGENT = "job-finder (personal job search tool; github.com/furkan-34/job-finder)"
PAGE = 25  # postings per request, the page size freehire's own search skill uses
TIMEOUT = 30  # seconds per request
RETRIES = 2  # more tries after a 429 or 503
DAYS = {"anyTime": None, "past24Hours": 1, "pastWeek": 7, "pastMonth": 30}


@dataclass
class Run:
    """What save_raw and the runner read from an Apify run."""
    id: str | None = None
    usage_total_usd: float = 0.0


def fetch(actor: dict, keywords: str, location: str, limit: int, date_posted: str,
          get=None) -> tuple[Run, dict, list[dict]]:
    """(run, the request's parameters, items): up to `limit` postings for one search.
    `get(url) -> parsed JSON` replaces the network in tests."""
    params = build_params(actor, keywords, location, limit, date_posted)
    get = get or _get
    items: list[dict] = []
    while len(items) < limit:
        page = {**params, "limit": min(PAGE, limit - len(items)), "offset": len(items)}
        data = get(f"{BASE_URL}?{urllib.parse.urlencode(_query(page))}")
        batch = data.get("data") if isinstance(data, dict) else None
        if not isinstance(batch, list):
            raise SourceError("freehire answered with an unexpected shape (no 'data' list).")
        items += [item for item in batch if isinstance(item, dict)]
        if len(batch) < page["limit"]:
            break  # the last page
    return Run(), params, items[:limit]


def build_params(actor: dict, keywords: str, location: str, limit: int, date_posted: str) -> dict:
    """The actor's `input` with the search's values; a value that is None is left out."""
    if date_posted not in DAYS:
        raise SourceError(f"date_posted must be one of {', '.join(DAYS)}")
    q = " ".join(word for word in keywords.split()
                 if word.casefold() not in actor.get("drop_words", []))
    if not q:
        raise SourceError("the keywords are only words freehire's search drops "
                          f"({', '.join(actor.get('drop_words', []))})")
    values = {"keywords": q, "country": country(actor, location), "limit": limit,
              "days": DAYS[date_posted]}
    params = {}
    for key, template in actor["input"].items():
        value = values.get(template[1:-1], template) if (
            isinstance(template, str) and template.startswith("{") and template.endswith("}")) else template
        if value is not None:
            params[key] = value
    return params


def country(actor: dict, location: str) -> str:
    """'Istanbul, Turkey' -> 'tr'. A country the entry does not list is an error, not a guess."""
    name = location.rsplit(",", 1)[-1].strip().casefold()
    code = actor.get("countries", {}).get(name)
    if code is None:
        raise SourceError(f"freehire searches by country and {name!r} is not in `countries` of "
                          f"{actor['name']} in config/apify_actors.json; add it with its ISO code.")
    return code


def _query(params: dict) -> dict:
    return {key: params[key] for key in sorted(params)}


def _get(url: str):
    """One GET with a pause and a retry on 429 and 503 (Retry-After, at most 10 seconds)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and attempt < RETRIES:
                time.sleep(_pause(exc.headers.get("Retry-After")))
                continue
            raise SourceError(f"freehire answered HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SourceError(f"Could not read freehire: {type(exc).__name__}.") from exc
    raise SourceError("freehire kept answering 429 or 503.")  # pragma: no cover - the loop returns or raises


def _pause(retry_after: str | None) -> float:
    try:
        return min(max(float(retry_after or ""), 1.0), 10.0)
    except ValueError:
        return 2.0
