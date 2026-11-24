"""Stage 5: applications and the replies to them, read from Gmail, and the status they lead to.

Claude reads the mail: the `mail-reader` agent has Read and the Gmail connector's search and
read tools only (it cannot send, reply, forward, draft, label or delete mail). Code works on
both sides of it, as in stage 2d:

    make   writes the postings to look up to data/track/batches/track-<n>.json: every posting
           the candidate may have applied to and that is not rejected (status shortlist,
           approved, cv_ready, applied or response, or new with a score on the shortlist; never a
           `block` one), each with its company, title and apply day, plus the Gmail query
    save   reads what the agent returned (/track copies it to
           data/track/results/track-<n>.json), keeps only replies to postings of that batch,
           checks each against the job schema and stores it in `tracking`. Any mail about an
           application to a posting not marked yet (a LinkedIn "application sent", an ATS
           confirmation, an answer) marks it applied, with `application.applied_at` = the day of
           the first one; a reply that answers the application (assessment, interview, offer,
           rejection) becomes the `outcome` and moves the posting to response
    stale  applications still `applied` with no mail for STALE_DAYS (60) days or more
    close  marks such applications `no_response` (application.closed_at): /track stops
           looking for them

A posting that may have been applied to without being marked (shortlist, approved, cv_ready
or new with a score) is looked up only for STALE_DAYS days after it was saved, so the search
does not grow forever.

Mail text is untrusted, like posting text, so the answer is never trusted beyond the schema:
unknown postings and mail already stored are dropped, an invalid reply is left out. Mail text
itself is never stored: a reply is its message id, the day, the kind and a note of at most 160
characters. Counts and errors name an id and a field, never a value.
"""

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from jobfinder import jobs
from jobfinder.score.batches import BatchError, read_answer

MODEL = "haiku"  # the model in .claude/agents/mail-reader.md
BATCH_SIZE = 10  # postings per batch, and companies in one Gmail query
# LinkedIn's job alert senders: their mails name many companies and never answer an application
# ("application sent" comes from jobs-noreply@linkedin.com, which stays in)
ALERT_SENDERS = ("jobalerts-noreply@linkedin.com", "jobs-listings@linkedin.com")
SLACK_DAYS = 2  # the search starts this many days before the last check, apply day or save day
STALE_DAYS = 60  # no mail for this long: the application is stale, a maybe-applied posting is left
TRACKED = ("applied", "response", "interview")
BEFORE = ("shortlist", "approved", "cv_ready")  # may have been applied to without /mark-applied
KINDS = ("received", "assessment", "interview", "offer", "rejection", "other")
ANSWERS = ("assessment", "interview", "offer", "rejection")  # the kinds that answer an application
POSTING_FIELDS = ("id", "company_name", "title")


def pending(store: dict, min_score: int = 70, today: date | None = None) -> list[dict]:
    """Postings that may have news: applied, or maybe applied to and saved in the last
    STALE_DAYS days; not rejected, not closed."""
    cutoff = (today or date.today()) - timedelta(days=STALE_DAYS)
    return [job for job in store["jobs"] if _watched(job, min_score, cutoff)
            and job.get("safety", {}).get("verdict") != "block"
            and job.get("tracking", {}).get("outcome") != "rejection"]


def _watched(job: dict, min_score: int, cutoff: date) -> bool:
    status = job.get("status")
    if status in TRACKED:
        return bool(job.get("application", {}).get("applied_at"))
    if _day(job["created_at"]) < cutoff:  # maybe applied to, long ago: no longer looked up
        return False
    if status == "new":  # on the shortlist: scoring does not change the status
        return job.get("match", {}).get("score", -1) >= min_score
    return status in BEFORE


def last_activity(job: dict) -> date:
    """The apply day, or the day of the newest mail about the application if later."""
    days = [_day(job["application"]["applied_at"])]
    days += [date.fromisoformat(reply["received_at"])
             for reply in job.get("tracking", {}).get("replies", [])]
    return max(days)


def stale(store: dict, days: int = STALE_DAYS, today: date | None = None) -> list[dict]:
    """Applications still `applied` (no answer) whose last activity is `days` or more ago,
    the quietest first."""
    today = today or date.today()
    found = [job for job in store["jobs"] if job.get("status") == "applied"
             and "application" in job and (today - last_activity(job)).days >= days]
    found.sort(key=last_activity)
    return [{"id": job["id"], "title": job.get("title"), "company_name": job.get("company_name"),
             "url": job.get("url"), "applied_at": job["application"]["applied_at"],
             "last_activity": last_activity(job).isoformat(),
             "quiet_days": (today - last_activity(job)).days} for job in found]


def close(store: dict, ids: list[str], now: datetime | None = None) -> dict:
    """Mark applications `no_response`. Only `applied` ones; any other id is skipped with a
    reason code: `unknown` or `status:<status>`."""
    stamp = (now or datetime.now(UTC)).isoformat()
    by_id = {job["id"]: job for job in store["jobs"]}
    closed, skipped = [], []
    for job_id in dict.fromkeys(ids):
        job = by_id.get(job_id)
        if job is None:
            skipped.append({"id": job_id, "reason": "unknown"})
        elif job["status"] != "applied" or "application" not in job:
            skipped.append({"id": job_id, "reason": f"status:{job['status']}"})
        else:
            job["status"] = "no_response"
            job["application"] = {**job["application"], "closed_at": stamp}
            closed.append(job_id)
    return {"closed": closed, "skipped": skipped}


MAX_ROUND = 9


def interview(store: dict, ids: list[str], back: bool = False) -> dict:
    """Move applications through the interview rounds by hand (the candidate knows, mail may not say).
    Forward: `applied` or `response` -> `interview` round 1, `interview` round n -> n + 1 (up to
    MAX_ROUND). Back: round n -> n - 1, round 1 -> `applied`. Any other id is skipped with a reason
    code: `unknown`, `status:<status>`, `last_round` or `not_interview`."""
    by_id = {job["id"]: job for job in store["jobs"]}
    moved, skipped = [], []
    for job_id in dict.fromkeys(ids):
        job = by_id.get(job_id)
        status = (job or {}).get("status")
        if job is None:
            skipped.append({"id": job_id, "reason": "unknown"})
        elif back and status != "interview":
            skipped.append({"id": job_id, "reason": "not_interview"})
        elif not back and status not in ("applied", "response", "interview"):
            skipped.append({"id": job_id, "reason": f"status:{status}"})
        else:
            application = dict(job.get("application") or {})
            current = application.get("interview_round", 0) if status == "interview" else 0
            number = current - 1 if back else current + 1
            if number > MAX_ROUND:
                skipped.append({"id": job_id, "reason": "last_round"})
                continue
            if number < 1:  # back from round 1
                application.pop("interview_round", None)
                job["status"] = "applied"
            else:
                application["interview_round"] = number
                job["status"] = "interview"
            job["application"] = application
            moved.append({"id": job_id, "status": job["status"], "round": max(number, 0)})
    return {"moved": moved, "skipped": skipped}


def make(store: dict, batches_dir: Path, results_dir: Path, now: datetime | None = None,
         size: int = BATCH_SIZE, min_score: int = 70) -> dict:
    """Write every pending posting into batch files of `size`; old files go first."""
    for old in [*batches_dir.glob("track-*.json"), *results_dir.glob("track-*.json")]:
        old.unlink()
    stamp = (now or datetime.now(UTC)).isoformat()
    todo = pending(store, min_score, _day(stamp))
    batches_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    batches = []
    for n, start in enumerate(range(0, len(todo), size), 1):
        part = todo[start:start + size]
        since = min(_start(job) for job in part) - timedelta(days=SLACK_DAYS)
        postings = [{**{key: job.get(key) for key in POSTING_FIELDS},
                     "applied_on": _applied_on(job),
                     "apply_domain": _domain(job.get("apply_url"))} for job in part]
        path = batches_dir / f"track-{n}.json"
        batch = {"step": "track", "made_at": stamp, "since": since.isoformat(),
                 "query": query(part, since), "jobs": postings}
        path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        batches.append({"path": str(path), "result_path": str(results_dir / path.name),
                        "count": len(part)})
    return {"count": len(todo), "batches": batches}


def _start(job: dict) -> date:
    """The day the next search has to cover from: the last check, the apply day or the day saved."""
    return _day(job.get("tracking", {}).get("checked_at")
                or job.get("application", {}).get("applied_at") or job["created_at"])


def _applied_on(job: dict) -> str | None:
    applied_at = job.get("application", {}).get("applied_at")
    return _day(applied_at).isoformat() if applied_at else None


def _day(value: str) -> date:
    return date.fromisoformat(value[:10])


def _domain(url: str | None) -> str | None:
    match = re.match(r"https?://([^/:?#]+)", url or "")
    return match.group(1).lower().removeprefix("www.") if match else None


def query(part: list[dict], since: date) -> str:
    """Mail received since `since`, not sent by the candidate nor a job alert, naming one of the
    companies."""
    names = list(dict.fromkeys(name for job in part if (name := search_name(job.get("company_name")))))
    alerts = " ".join(f"-from:{sender}" for sender in ALERT_SENDERS)
    return (f"after:{since:%Y/%m/%d} -from:me -in:chats {alerts} "
            + "{" + " ".join(f'"{name}"' for name in names) + "}")


def search_name(name: str | None) -> str:
    """The company name as a search phrase (Gmail, LinkedIn): no search operators, no legal form at
    the end."""
    words = re.sub(r'["{}()\[\]:*]', " ", name or "").split()
    while len(words) > 1:
        if jobs.normalize(words[-1]) in jobs.COMPANY_SUFFIXES:
            words = words[:-1]
        elif len(words) > 2 and jobs.normalize(" ".join(words[-2:])) in jobs.COMPANY_SUFFIXES:
            words = words[:-2]
        else:
            break
    return " ".join(words)[:60]


def save(store: dict, batch_path: Path, result_path: Path, now: datetime | None = None,
         min_score: int = 70) -> dict:
    """Store an agent's answer for one batch. Returns counts and the replies stored."""
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    if batch.get("step") != "track":
        raise BatchError(f"{batch_path} is not a tracking batch file")
    entries = [entry for entry in read_answer(result_path, "replies")
               if isinstance(entry, dict) and isinstance(entry.get("job_id"), str)]
    now = now or datetime.now(UTC)
    # A day of slack at the end too: the agent gives the day in the candidate's time zone.
    since, until = date.fromisoformat(batch["since"]), now.date() + timedelta(days=1)
    in_batch = {posting["id"] for posting in batch["jobs"]}
    todo = {job["id"]: job for job in pending(store, min_score, _day(batch["made_at"]))
            if job["id"] in in_batch}
    known = {reply["message_id"] for job in store["jobs"]
             for reply in job.get("tracking", {}).get("replies", [])}
    saved, invalid, unknown, duplicate = [], [], 0, 0
    for entry in entries:
        job = todo.get(entry.get("job_id"))
        if job is None:
            unknown += 1  # not in this batch, or rejected or changed meanwhile
            continue
        message_id, received_at = entry.get("message_id"), entry.get("received_at")
        if isinstance(message_id, str) and message_id in known:
            duplicate += 1
            continue
        if isinstance(received_at, str):
            received_at = received_at[:10]  # a time of day is fine, only the day is kept
        reply = {"message_id": message_id, "received_at": received_at,
                 "kind": entry.get("kind"), "note": entry.get("note") or "",
                 "saved_at": now.isoformat()}
        tracking = job.get("tracking", {"checked_at": batch["made_at"], "outcome": None, "replies": []})
        tracking = {**tracking, "replies": [*tracking["replies"], reply]}
        if errors := jobs.validate({**job, "tracking": tracking}):
            invalid.append(f"{job['id']} {errors[0].split(':')[0]}")  # the field, not the value
            continue
        if not since <= date.fromisoformat(received_at) <= until:
            invalid.append(f"{job['id']} /tracking/replies/received_at")
            continue
        job["tracking"] = tracking
        known.add(message_id)
        saved.append((job, reply))
    before = {job_id: (job["status"], job.get("tracking", {}).get("outcome"))
              for job_id, job in todo.items()}
    for job in todo.values():  # every posting of the batch was looked up, with or without news
        tracking = job.setdefault("tracking", {"checked_at": batch["made_at"], "outcome": None,
                                               "replies": []})
        tracking["checked_at"] = batch["made_at"]
        tracking["outcome"] = outcome(tracking["replies"])
        if tracking["replies"] and job["status"] not in TRACKED:  # applied, seen in the mail
            first = min(reply["received_at"] for reply in tracking["replies"])
            job["status"] = "applied"
            job["application"] = {"applied_at": f"{first}T00:00:00+00:00"}
        if tracking["outcome"] and job["status"] == "applied":
            job["status"] = "response"
    result = {"checked": len(todo), "saved": len(saved), "invalid": len(invalid),
              "unknown": unknown, "duplicate": duplicate,
              "replies": [{"id": job["id"], "title": job.get("title"),
                           "company_name": job.get("company_name"), **reply,
                           "status": job["status"], "outcome": job["tracking"]["outcome"]}
                          for job, reply in saved],
              "changes": [{"id": job["id"], "title": job.get("title"),
                           "company_name": job.get("company_name"),
                           "from_status": before[job["id"]][0], "to_status": job["status"],
                           "from_outcome": before[job["id"]][1],
                           "to_outcome": job["tracking"]["outcome"]}
                          for job in todo.values()
                          if before[job["id"]] != (job["status"], job["tracking"]["outcome"])]}
    if invalid:
        result["first_invalid"] = invalid[0]
    return result


def outcome(replies: list[dict]) -> str | None:
    """The kind of the latest reply that answers the application; None while there is none."""
    answers = [(reply["received_at"], n, reply["kind"])  # the same day: the one saved last
               for n, reply in enumerate(replies) if reply["kind"] in ANSWERS]
    return max(answers)[2] if answers else None


def applications(store: dict) -> list[dict]:
    """Every application with its status and latest reply, most recent apply day first
    (closed ones too)."""
    entries = []
    for job in store["jobs"]:
        if job.get("status") not in (*TRACKED, "no_response") or "application" not in job:
            continue
        tracking = job.get("tracking", {})
        replies = sorted(tracking.get("replies", []), key=lambda reply: reply["received_at"])
        entries.append({"id": job["id"], "title": job.get("title"),
                        "company_name": job.get("company_name"), "url": job.get("url"),
                        "applied_at": job["application"]["applied_at"], "status": job["status"],
                        "outcome": tracking.get("outcome"), "checked_at": tracking.get("checked_at"),
                        "last_reply": replies[-1] if replies else None})
    return sorted(entries, key=lambda entry: entry["applied_at"], reverse=True)
