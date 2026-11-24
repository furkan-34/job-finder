"""Company job boards of three applicant tracking systems (ATS): free JSON APIs, no token, for
the actor entries with "kind": "greenhouse", "lever" or "ashby".

    greenhouse  GET https://boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true
                -> {"jobs": [job, ...], "meta": {"total"}}
    lever       GET https://api.lever.co/v0/postings/<board>?mode=json -> [posting, ...]
    ashby       GET https://api.ashbyhq.com/posting-api/job-board/<board>?includeCompensation=true
                -> {"jobs": [job, ...]}

One answer is every open posting of one company, with the full description, and the services
have no search. So the entry's `boards` (board name -> company name) are read one request each
(kept for the rest of the run, so the next search reads them again for free) and the postings
are filtered here, in plain code:

- keywords: every word not in `drop_words` must be a word of the title;
- location: by country, like freehire: the part after the last comma must be in the entry's
  `countries`, and a posting is kept when one of its locations has one of that country's words
  or the search's city; a location in `any_location` ("Worldwide") keeps every location;
- date_posted: published within the last 1, 7 or 30 days; a posting without a date is left
  out then;
- the newest `limit` postings are kept.

Items are kept as the service returned them, with one key added, `_board` ({"name", "company"}),
because Lever and Ashby postings do not name the company. Posting text is untrusted, like any
actor's output: the safety check of stage 2c runs on it first.
"""

import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

from jobfinder.jobs import normalize
from jobfinder.sources.apify import SourceError
from jobfinder.sources.freehire import DAYS, USER_AGENT, Run, _pause

TIMEOUT = 30  # seconds per request
RETRIES = 2  # more tries after a 429 or 503
URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{board}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true",
}
KINDS = tuple(URLS)
_CACHE: dict[str, object] = {}  # url -> answer, for the rest of the run


def fetch(actor: dict, keywords: str, location: str, limit: int, date_posted: str,
          get=None, now: datetime | None = None) -> tuple[Run, dict, list[dict]]:
    """(run, the search's parameters, items): up to `limit` postings for one search, newest
    first. `get(url) -> parsed JSON` replaces the network in tests."""
    values = search_values(actor, keywords, location, limit, date_posted)
    params = _fill(actor["input"], values)
    since = (now or datetime.now(UTC)) - timedelta(days=values["days"]) if values["days"] else None
    items = []
    for board, company in actor["boards"].items():
        for item in _postings(actor, board, get):
            if _keep(actor["kind"], item, values, since):
                items.append({**item, "_board": {"name": board, "company": company}})
    items.sort(key=lambda item: _published(actor["kind"], item) or datetime.min.replace(tzinfo=UTC),
               reverse=True)
    return Run(), params, items[:limit]


def search_values(actor: dict, keywords: str, location: str, limit: int, date_posted: str) -> dict:
    """The search translated to the filters; anything it would have to guess is an error."""
    if date_posted not in DAYS:
        raise SourceError(f"date_posted must be one of {', '.join(DAYS)}")
    if not actor.get("boards"):
        raise SourceError(f"{actor['name']} has no company boards: add them to `boards` of "
                          f"{actor['name']} in config/apify_actors.json.")
    drop = {normalize(word) for word in actor.get("drop_words", [])}
    words = [word for word in normalize(keywords).split() if word not in drop]
    if not words:
        raise SourceError(f"the keywords are only words {actor['name']} leaves out "
                          f"({', '.join(actor.get('drop_words', []))})")
    return {"keywords": words, "location_words": location_words(actor, location),
            "boards": list(actor["boards"]), "limit": limit, "days": DAYS[date_posted]}


def location_words(actor: dict, location: str) -> list[str] | None:
    """'Istanbul, Turkey' -> Turkey's words and 'istanbul'; None for 'Worldwide' (every place)."""
    parts = [normalize(part) for part in location.split(",")]
    if parts[-1] in {normalize(word) for word in actor.get("any_location", [])}:
        return None
    countries = {normalize(name): words for name, words in actor.get("countries", {}).items()}
    if parts[-1] not in countries:
        raise SourceError(f"{actor['name']} filters by country and {parts[-1]!r} is not in "
                          f"`countries` of {actor['name']} in config/apify_actors.json; add it "
                          "with the words its postings' locations use.")
    words = [normalize(word) for word in countries[parts[-1]]]
    return list(dict.fromkeys(words + ([parts[0]] if len(parts) > 1 and parts[0] else [])))


def _postings(actor: dict, board: str, get) -> list[dict]:
    url = URLS[actor["kind"]].format(board=board)
    if get is None:
        if url not in _CACHE:
            _CACHE[url] = _get(url, actor["name"], board)
        data = _CACHE[url]
    else:
        data = get(url)
    postings = data if actor["kind"] == "lever" else (data.get("jobs") if isinstance(data, dict) else None)
    if not isinstance(postings, list):
        raise SourceError(f"{actor['name']} answered with an unexpected shape for board {board!r}.")
    return [item for item in postings if isinstance(item, dict)]


def _keep(kind: str, item: dict, values: dict, since: datetime | None) -> bool:
    if kind == "ashby" and item.get("isListed") is False:
        return False
    title = set(normalize(_str(item.get("text" if kind == "lever" else "title"))).split())
    if not all(word in title for word in values["keywords"]):
        return False
    if values["location_words"] is not None:
        places = [f" {normalize(place)} " for place in _locations(kind, item)]
        if not any(f" {word} " in place for word in values["location_words"] for place in places):
            return False
    if since is not None:
        published = _published(kind, item)
        return published is not None and published >= since
    return True


def _locations(kind: str, item: dict) -> list[str]:
    """Every location text of a posting."""
    if kind == "greenhouse":
        return [_str(_dig(item, "location", "name"))] + [
            _str(office.get("location") or office.get("name")) for office in _list(item.get("offices"))]
    if kind == "lever":
        categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        return [_str(categories.get("location"))] + [_str(p) for p in _list(categories.get("allLocations"))]
    address = [_str(_dig(item, "address", "postalAddress", key))
               for key in ("addressLocality", "addressCountry")]
    return [_str(item.get("location")), *address] + [
        _str(other.get("location")) for other in _list(item.get("secondaryLocations"))]


def _published(kind: str, item: dict) -> datetime | None:
    value = {"greenhouse": "first_published", "lever": "createdAt", "ashby": "publishedAt"}[kind]
    value = item.get(value)
    try:
        if isinstance(value, int | float) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000, UTC)  # Lever: milliseconds
        if isinstance(value, str):
            moment = datetime.fromisoformat(value)
            return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _fill(template: dict, values: dict) -> dict:
    return {key: values.get(value[1:-1], value) if isinstance(value, str) and value.startswith("{")
            and value.endswith("}") else value for key, value in template.items()}


def _dig(item: dict, *keys):
    for key in keys:
        item = item.get(key) if isinstance(item, dict) else None
    return item


def _list(value) -> list:
    return [one for one in value if isinstance(one, dict | str)] if isinstance(value, list) else []


def _str(value) -> str:
    return value if isinstance(value, str) else ""


def _get(url: str, name: str, board: str):
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
            if exc.code == 404:
                raise SourceError(f"{name}: board {board!r} was not found (HTTP 404); fix `boards` "
                                  f"of {name} in config/apify_actors.json.") from exc
            raise SourceError(f"{name} answered HTTP {exc.code} for board {board!r}.") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SourceError(f"Could not read {name} board {board!r}: {type(exc).__name__}.") from exc
    raise SourceError(f"{name} kept answering 429 or 503.")  # pragma: no cover - the loop returns or raises
