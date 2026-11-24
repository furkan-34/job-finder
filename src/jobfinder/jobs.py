"""The job store: every fetched posting, in one standard shape, in data/jobs/jobs.json.

    {"updated_at": "...", "jobs": [{...}, ...]}   one entry per posting, see schemas/job.schema.json

A raw actor output file (data/fetch/raw/...) is mapped to that shape by
jobfinder.sources.mapping, from the actor's "output" entry in
config/apify_actors.json, and added here (import_raw). A posting whose id is already
in the store is skipped, never overwritten, so later stages can add to an entry
(status, scores) without a new fetch undoing it.

A posting reposted under a new id (or found by a second source) is caught by its
fingerprint: normalized company + title + city. It is skipped too and counted as
a repost. The fingerprint is computed, not stored.
"""

import json
import os
import re
import unicodedata
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from jobfinder.paths import DATA_DIR, JOB_SCHEMA_PATH, JOBS_JSON
from jobfinder.sources.mapping import to_job

DESCRIPTION_MAX = 6000  # characters of a description an agent reads (the store keeps it whole)

# Legal-form words dropped from the end of a company name ("Acme Inc." = "ACME").
COMPANY_SUFFIXES = {"inc", "llc", "ltd", "limited", "gmbh", "ag", "bv", "nv", "sa", "sas", "srl",
                    "plc", "corp", "corporation", "co", "company", "as", "a s", "sti", "ltd sti",
                    "group", "holding"}

@cache
def load_schema() -> dict:
    return json.loads(JOB_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(job: dict) -> list[str]:
    validator = Draft202012Validator(load_schema(), format_checker=FormatChecker())
    return [f"/{'/'.join(map(str, e.absolute_path))}: {e.message}"
            for e in validator.iter_errors(job)]


def load(path: Path = JOBS_JSON) -> dict:
    if not path.exists():
        return {"updated_at": None, "jobs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def for_agent(description: str | None, limit: int = DESCRIPTION_MAX) -> str | None:
    """A description as the agents read it: runs of spaces and blank lines closed up, a line
    repeated word for word kept once, and at most `limit` characters, cut at a word. Requirements
    come before benefits and legal text in almost every posting, so the cut takes what matters
    least. The safety check has read the whole text before."""
    if description is None:
        return None
    lines, seen = [], set()
    for line in description.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if line in seen and len(line) > 20:  # a repeated block; short lines ("Requirements:") stay
            continue
        seen.add(line)
        lines.append(line)
    text = "\n".join(lines).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip() + " …"


def normalize(text: str | None) -> str:
    """Lowercase, no accents, no punctuation, single spaces: 'Ünal A.Ş.' -> 'unal a s'."""
    text = (text or "").replace("ı", "i").replace("İ", "i")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def company_key(name: str | None) -> str:
    """Normalized company name without its legal form: 'Acme Holding Ltd. Şti.' -> 'acme'."""
    company = normalize(name)
    while (suffix := next((s for s in COMPANY_SUFFIXES if company.endswith(" " + s)), None)):
        company = company[:-len(suffix) - 1]
    return company


def fingerprint(job: dict) -> str | None:
    """company|title|city, normalized; None without a company or title (never matched)."""
    company = company_key(job.get("company_name"))
    # Bracketed parts are gender or team notes: "Backend Engineer (m/w/d)".
    title = normalize(re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", job.get("title") or ""))
    city = normalize((job.get("location") or "").split(",")[0])
    if not company or not title:
        return None
    return f"{company}|{title}|{city}"


def add(store: dict, jobs: list[dict]) -> tuple[int, int, int]:
    """Append jobs that are not in the store yet. Returns (added, already_saved, reposts):
    a known id is already saved, a new id with a known fingerprint is a repost."""
    known = {job["id"] for job in store["jobs"]}
    prints = {fingerprint(job) for job in store["jobs"]} - {None}
    added = reposts = 0
    for job in jobs:
        if job["id"] in known:
            continue
        if (fp := fingerprint(job)) in prints:
            reposts += 1
            continue
        store["jobs"].append(job)
        known.add(job["id"])
        if fp:
            prints.add(fp)
        added += 1
    return added, len(jobs) - added - reposts, reposts


def import_raw(store: dict, raw_path: Path, actors: list[dict]) -> dict:
    """Map one raw file's items with its actor's output mapping and add them to the store.

    Safe to repeat: postings already in the store are skipped. Returns counts only,
    never posting text. An item that does not fit the job schema is left out and counted
    as invalid; `first_invalid` names the first one's id and field."""
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    actor = next((a for a in actors if a["name"] == raw.get("data_source")), None)
    if actor is None:
        return {"raw_path": str(raw_path), "error": f"unknown data source {raw.get('data_source')!r}"}
    now = datetime.now(UTC)
    mapped, invalid = [], []
    for item in raw.get("items", []):
        job = to_job(item, actor, now, raw.get("search_id"), _relative(raw_path))
        if job is None:
            invalid.append("item has no id")
        elif errors := validate(job):
            # Only the field, not the message: the message can quote posting text.
            invalid.append(f"{job['id']} {errors[0].split(':')[0]}")
        else:
            mapped.append(job)
    added, already_saved, reposts = add(store, mapped)
    result = {"raw_path": str(raw_path), "count": len(raw.get("items", [])), "new": added,
              "already_saved": already_saved, "reposts": reposts, "invalid": len(invalid)}
    if invalid:
        result["first_invalid"] = invalid[0]
    return result


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(DATA_DIR))
    except ValueError:
        return str(path)


def dedupe(store: dict) -> list[str]:
    """Remove reposts already in the store: an entry whose fingerprint matches an earlier one.
    Only entries still `new` are removed, so nothing a later stage worked on is lost.
    Returns the removed ids."""
    seen: set[str] = set()
    kept, removed = [], []
    for job in store["jobs"]:
        fp = fingerprint(job)
        if fp in seen and job.get("status") == "new":
            removed.append(job["id"])
            continue
        if fp:
            seen.add(fp)
        kept.append(job)
    store["jobs"] = kept
    return removed


def approve(store: dict, ids: list[str]) -> dict:
    """Mark postings `approved` for stage 3b: the choice of step 3a (/approve-jobs).

    Only a posting the safety check let through (`pass` or `warn`) and not decided yet
    (`new` or `shortlist`) can be approved; any other id is skipped with a reason code:
    `unknown`, `not_checked`, `blocked` or `status:<status>`."""
    by_id = {job["id"]: job for job in store["jobs"]}
    approved, skipped = [], []
    for job_id in dict.fromkeys(ids):
        job = by_id.get(job_id)
        verdict = (job or {}).get("safety", {}).get("verdict")
        if job is None:
            reason = "unknown"
        elif verdict is None:
            reason = "not_checked"
        elif verdict == "block":
            reason = "blocked"
        elif job["status"] not in ("new", "shortlist"):
            reason = f"status:{job['status']}"
        else:
            job["status"] = "approved"
            approved.append(job_id)
            continue
        skipped.append({"id": job_id, "reason": reason})
    return {"approved": approved, "skipped": skipped}


def delete(store: dict, ids: list[str]) -> dict:
    """Mark postings `deleted` (the candidate's choice, in the web UI). A deleted posting stays in the
    store as a marker, so a later search that finds the same id, or the same company, title and city,
    does not add it again (`add`); listings and every stage leave it out. Any status can be deleted."""
    by_id = {job["id"]: job for job in store["jobs"]}
    deleted, unknown = [], []
    for job_id in dict.fromkeys(ids):
        job = by_id.get(job_id)
        if job is None:
            unknown.append(job_id)
            continue
        job["status"] = "deleted"
        deleted.append(job_id)
    return {"deleted": deleted, "unknown": unknown}


def save(store: dict, path: Path = JOBS_JSON) -> None:
    """Write through a temporary file so an interrupted run never leaves half a file."""
    store["updated_at"] = datetime.now(UTC).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
