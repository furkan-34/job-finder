"""Every posting in data/jobs/jobs.json with where it is in the pipeline, for /show-list.

Each posting gets one `stage`, the furthest step it has reached:

    unchecked     not through the safety check yet
    blocked       the safety check blocked it: no stage reads it, and its text is never shown
    filtered_out  the rule filter rejected it
    unscored      checked and filtered, not scored yet
    new           scored, not on the shortlist or approved
    shortlist, approved, cv_ready, applied, interview, response, no_response, rejected   its status
    deleted       the candidate deleted it: kept only so a search does not add it again, and never listed

A blocked posting is listed by id and stage only. The others carry their short fields, the
score, the `warn` reasons, the apply day, the outcome of the replies, `also_in`: the cities
of the copies the rule filter set aside as `same_posting` of it, and, from scoring on,
`people_search`: two LinkedIn people searches, the company's recruiters and people in the same
role, as links for the candidate to open (no tool opens or reads them). Counts per stage
come first, so the table can be skipped.
"""

import re
from collections import Counter, defaultdict
from urllib.parse import quote

from jobfinder.score.rules import SAME
from jobfinder.track import search_name

PEOPLE_SEARCH = "https://www.linkedin.com/search/results/people/?keywords="
PEOPLE_STAGES = ("interview", "response", "applied", "cv_ready", "approved", "shortlist", "new")  # scored on

FIELDS = ("id", "title", "company_name", "location", "url", "apply_url")
STAGES = ("interview", "response", "applied", "no_response", "cv_ready", "approved", "shortlist", "new", "unscored",
          "filtered_out", "rejected", "unchecked", "blocked", "deleted")  # the order of the listing


def stage(job: dict) -> str:
    if job["status"] == "deleted":
        return "deleted"
    verdict = (job.get("safety") or {}).get("verdict")
    if verdict is None:
        return "unchecked"
    if verdict == "block":
        return "blocked"
    if job["status"] == "new":
        if (job.get("filter") or {}).get("passed") is False:
            return "filtered_out"
        if "match" not in job:
            return "unscored"
    return job["status"]


def copies(store: dict) -> dict[str, list[str]]:
    """Posting id -> the locations of its copies set aside by the rule filter."""
    found = defaultdict(list)
    for job in store["jobs"]:
        for reason in (job.get("filter") or {}).get("reasons", []):
            if reason.startswith(SAME) and job.get("location"):
                found[reason.removeprefix(SAME)].append(job["location"])
    return found


def people_search(job: dict) -> dict | None:
    """LinkedIn people searches for "<company> recruiter" and "<company> <role>"; None without
    a company. The names come from the posting, so they only go in, URL-encoded, as search text."""
    company = search_name(job.get("company_name"))
    if not company:
        return None
    role = " ".join(re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", job.get("title") or "").split())[:80]
    return {"recruiter": PEOPLE_SEARCH + quote(f"{company} recruiter"),
            "team": PEOPLE_SEARCH + quote(f"{company} {role}".strip())}


def entry(job: dict, also_in: list[str] | None = None) -> dict:
    where = stage(job)
    if where == "blocked":
        return {"id": job["id"], "stage": where}
    safety = job.get("safety") or {}
    return {**{key: job.get(key) for key in FIELDS}, "stage": where, "status": job["status"],
            "score": (job.get("match") or {}).get("score"),
            "warnings": safety["reasons"] if safety.get("verdict") == "warn" else [],
            "applied_at": (job.get("application") or {}).get("applied_at"),
            "outcome": (job.get("tracking") or {}).get("outcome"),
            "also_in": also_in or [],
            "people_search": people_search(job) if where in PEOPLE_STAGES else None}


def listing(store: dict, stages: list[str] | None = None, top: int | None = None) -> dict:
    """Counts per stage of every posting, and the postings of `stages` (all by default),
    furthest stage first, then best score, then newest."""
    shown = [job for job in store["jobs"] if job["status"] != "deleted"]
    counts = Counter(stage(job) for job in shown)
    also_in = copies(store)
    rows = [entry(job, also_in.get(job["id"])) for job in shown
            if not stages or stage(job) in stages]
    created = {job["id"]: job.get("created_at") or "" for job in shown}
    rows.sort(key=lambda row: created[row["id"]], reverse=True)
    rows.sort(key=lambda row: (STAGES.index(row["stage"]), -(row.get("score") or -1)))
    return {"total": len(shown),
            "counts": {name: counts[name] for name in STAGES if counts[name]},
            "count": len(rows[:top]), "jobs": rows[:top]}
