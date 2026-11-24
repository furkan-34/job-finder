"""Applying, by hand (stage 4, part of stage 3b's flow). The candidate sends every application.

    ready(store)              postings whose CV and cover letter are done (status cv_ready), best first:
                              the table at the end of /write-cv, with each apply link
    mark_applied(job, on)     once the candidate says they sent it: cv_ready -> applied, and
                              application.applied_at (read by stage 5 to look for replies)

Nothing here opens a link, fills in a form or sends anything. An entry holds the short
fields of a posting (as `jobfinder score list`), never its description, and a posting the
safety check blocked is never listed.
"""

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from jobfinder.paths import DATA_DIR

FIELDS = ("id", "title", "company_name", "location", "work_modes", "url", "apply_url")
# Stage 3b's files on the job's `documents`, paths relative to data/; records written
# before the last two existed lack them.
DOCUMENTS = ("cv", "cover_letter", "cover_letter_text", "job_description")


class ApplyError(Exception):
    pass


def ready(store: dict, data_dir: Path = DATA_DIR) -> list[dict]:
    jobs = [job for job in store["jobs"] if job["status"] == "cv_ready" and _safe(job)]
    jobs.sort(key=lambda job: (-_score(job, -1), job["created_at"]))
    return [_entry(job, data_dir) for job in jobs]


def find(store: dict, job_id: str) -> dict:
    job = next((job for job in store["jobs"] if job["id"] == job_id), None)
    if job is None:
        raise ApplyError(f"no posting with id {job_id!r}")
    return job


EARLY = ("new", "shortlist", "approved")


def mark_applied(job: dict, on: date | None = None, now: datetime | None = None, early: bool = False) -> dict:
    """cv_ready -> applied. `on` is the day it was sent if not today (stored as 00:00 UTC).
    `early` also takes a posting the candidate applied to without the documents (`EARLY` statuses)."""
    now = now or datetime.now(UTC)
    if job["status"] in ("applied", "interview", "response"):
        raise ApplyError(f"{job['id']} is already marked as applied "
                         f"({job.get('application', {}).get('applied_at')}).")
    if job["status"] != "cv_ready" and not (early and job["status"] in EARLY):
        raise ApplyError(f"{job['id']} has status {job['status']!r}; only a posting with status "
                         "'cv_ready' can be marked as applied.")
    if (job.get("safety") or {}).get("verdict") == "block":
        raise ApplyError(f"{job['id']} was blocked by the safety check.")
    if on and on > now.date():
        raise ApplyError(f"{on} is in the future.")
    if on and on < datetime.fromisoformat(job["created_at"]).date():
        raise ApplyError(f"{on} is before the posting was saved ({job['created_at'][:10]}).")
    applied_at = datetime.combine(on, time(), UTC) if on else now
    job["status"] = "applied"
    job["application"] = {"applied_at": applied_at.isoformat(timespec="seconds")}
    return {"id": job["id"], "title": job["title"], "company_name": job.get("company_name"),
            "status": job["status"], "applied_at": job["application"]["applied_at"]}


def parse_day(text: str, today: date | None = None) -> date:
    """`today`, `yesterday` or YYYY-MM-DD."""
    today = today or datetime.now(UTC).date()
    if text == "today":
        return today
    if text == "yesterday":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ApplyError(f"--date must be today, yesterday or YYYY-MM-DD, not {text!r}") from None


def _entry(job: dict, data_dir: Path) -> dict:
    safety = job["safety"]
    documents = job.get("documents") or {}
    return {**{key: job.get(key) for key in FIELDS}, "status": job["status"],
            "link": job.get("apply_url") or job.get("url"), "score": _score(job, None),
            "warnings": safety["reasons"] if safety["verdict"] == "warn" else [],
            "documents": {key: {"path": str(data_dir / documents[key]),
                                "exists": (data_dir / documents[key]).is_file()}
                          for key in DOCUMENTS if documents.get(key)}}


def _safe(job: dict) -> bool:
    return job.get("safety", {}).get("verdict") in ("pass", "warn")


def _score(job: dict, default):
    return (job.get("match") or {}).get("score", default)
