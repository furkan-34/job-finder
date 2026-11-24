"""One mapper for every Apify actor, driven by the actor's entry in config/apify_actors.json.

    "input":  the actor input, with "{keywords}", "{location}", "{limit}" and
              "{date_posted}" replaced by the search's values ("{city}" too, see below)
    "output": job field -> the actor's output field, either "fieldName" (dots for
              nested fields, "company.name"), a list of them (the first one that is
              not empty), {"field": ..., "transform": ...}, or {"fields": [...],
              "transform": ...} (every one that is not empty, in order, as a list)

Optional keys that translate the search's values for an actor that needs it:

    "drop_words":         words left out of the keywords (case ignored), e.g. "remote"
    "date_posted_values": date_posted -> the actor's own value (null: no date filter,
                          the input key is left out); a value it does not list is an
                          error, never a wider search
    "location_countries": the countries the actor covers; "{city}" is the part of the
                          location before the first comma, or "" (the whole country)
                          when the location is only a country

Plain code, no LLM: an actor returns JSON with fixed field names, and posting
text is not read before the safety check of stage 2c. Adding an actor whose
output fits the transforms below needs only a config entry.
"""

import html
from datetime import UTC, datetime
from html.parser import HTMLParser

from jobfinder.sources.errors import SourceError

WORK_MODES = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite",
              "on site": "onsite"}


def _text(value):
    """Strings and numbers as they are, empty as None, anything else as text."""
    if value is None or value == "" or value == []:
        return None
    return value if isinstance(value, str | int) else str(value)


def _join(value):
    """A list such as ["$80K", "$100K"] -> "$80K - $100K"."""
    if isinstance(value, list):
        value = " - ".join(str(part) for part in value if part)
    return _text(value)


def _work_modes(value):
    """"Hybrid" or ["Remote", "On-site"] -> ["onsite", "remote"]; unknown words dropped."""
    values = value if isinstance(value, list) else [value]
    return sorted({WORK_MODES[v.strip().lower()] for v in values
                   if isinstance(v, str) and v.strip().lower() in WORK_MODES})


BLOCK_TAGS = frozenset({"p", "br", "div", "li", "ul", "ol", "tr", "h1", "h2", "h3", "h4", "h5", "h6"})


class _Text(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        self.handle_starttag(tag, [])

    def handle_data(self, data):
        self.parts.append(data)


def _html_text(value):
    """HTML (also HTML-escaped, as Greenhouse sends it), or lists and objects of it, -> plain
    text: the strings in order, one paragraph each."""
    strings = []

    def walk(one):
        if isinstance(one, str):
            strings.append(one)
        elif isinstance(one, list | dict):
            for part in one.values() if isinstance(one, dict) else one:
                walk(part)

    walk(value)
    paragraphs = []
    for string in strings:
        parser = _Text()
        parser.feed(html.unescape(string))
        parser.close()
        lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
        paragraphs.append("\n".join(line for line in lines if line))
    return _text("\n\n".join(p for p in paragraphs if p))


def _epoch_ms(value):
    """Milliseconds since 1970 (Lever's dates) -> ISO 8601 in UTC; text as it is."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000, UTC).isoformat()
    return _text(value)


TRANSFORMS = {"text": _text, "join": _join, "work_modes": _work_modes, "html_text": _html_text,
              "epoch_ms": _epoch_ms}
REQUIRED_OUTPUT = ("source_id", "title")


def build_input(actor: dict, keywords: str, location: str, limit: int, date_posted: str) -> dict:
    values = {"keywords": _keywords(actor, keywords), "location": location, "limit": limit,
              "date_posted": _date_posted(actor, date_posted)}
    if "location_countries" in actor:
        values["city"] = _city(actor, location)
    filled = {key: _fill(value, values) for key, value in actor["input"].items()}
    return {key: value for key, value in filled.items() if value is not None}


def to_job(item: dict, actor: dict, created_at: datetime, search_id: str | None = None,
           raw_file: str | None = None) -> dict | None:
    """Map one actor item to the standard job shape; None if it has no id."""
    mapped = {field: _get(item, spec) for field, spec in actor["output"].items()}
    source_id = str(mapped.pop("source_id") or "").strip()
    if not source_id:
        return None
    job = {"id": f"{actor['site']}:{source_id}", "data_source": actor["name"],
           "source_id": source_id, **mapped}
    job["title"] = job.get("title") or ""
    job.setdefault("work_modes", [])
    return {**job, "search_id": search_id, "raw_file": raw_file, "status": "new",
            "created_at": created_at.isoformat()}


def check_spec(actor: dict) -> list[str]:
    """Problems in an actor's input/output entry; empty when it can be used."""
    problems = []
    if not isinstance(actor.get("input"), dict):
        problems.append("'input' must be an object")
    output = actor.get("output")
    if not isinstance(output, dict):
        return problems + ["'output' must be an object"]
    problems += [f"'output' has no {field!r}" for field in REQUIRED_OUTPUT if field not in output]
    for field, spec in output.items():
        transform = spec.get("transform", "text") if isinstance(spec, dict) else "text"
        if isinstance(spec, dict) and ("field" in spec) == ("fields" in spec):
            problems.append(f"output {field!r}: give either 'field' or 'fields'")
        if transform not in TRANSFORMS:
            problems.append(f"output {field!r}: unknown transform {transform!r}")
    return problems


def _keywords(actor: dict, keywords: str) -> str:
    drop = [word.casefold() for word in actor.get("drop_words", [])]
    kept = " ".join(word for word in keywords.split() if word.casefold() not in drop)
    if not kept:
        raise SourceError(f"the keywords are only words {actor['name']} leaves out "
                          f"({', '.join(drop)})")
    return kept


def _date_posted(actor: dict, date_posted: str) -> str:
    values = actor.get("date_posted_values")
    if values is None:
        return date_posted
    if date_posted not in values:
        raise SourceError(f"{actor['name']} has no {date_posted!r} filter; choose one of "
                          f"{', '.join(values)} (jobfinder fetch configure --date-posted) "
                          f"or leave {actor['name']} out.")
    return values[date_posted]


def _city(actor: dict, location: str) -> str:
    """'London, United Kingdom' -> 'London', 'United Kingdom' -> ''. Another country is an error."""
    parts = [part.strip() for part in location.split(",")]
    countries = [c.casefold() for c in actor["location_countries"]]
    if parts[-1].casefold() not in countries:
        raise SourceError(f"{actor['name']} covers only {', '.join(actor['location_countries'])}; "
                          f"{parts[-1]!r} is not in `location_countries` of {actor['name']} in "
                          "config/apify_actors.json.")
    return parts[0] if len(parts) > 1 else ""


def _fill(value, values: dict):
    """"{limit}" -> 25 (the value keeps its type); anything else is left as it is."""
    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        return values.get(value[1:-1], value)
    return value


def _get(item: dict, spec):
    transform = spec.get("transform", "text") if isinstance(spec, dict) else "text"
    if isinstance(spec, dict) and "fields" in spec:
        values = [value for one in spec["fields"] if (value := _lookup(item, one)) not in (None, "", [])]
        return TRANSFORMS[transform](values or None)
    path = spec["field"] if isinstance(spec, dict) else spec
    for one in path if isinstance(path, list) else [path]:
        value = _lookup(item, one)
        if value not in (None, "", []):
            break
    return TRANSFORMS[transform](value)


def _lookup(item: dict, path: str):
    value = item
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value
