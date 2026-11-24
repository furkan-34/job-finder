"""Stage 2d, steps 2 and 3: batch files for the agents that read posting text, and saving
what they return.

The LLM steps run in Claude Code subagents that can only read: `job-extractor` (Haiku,
structured extraction) and `job-matcher` (Sonnet, Opus or Haiku, score against the profile).
They cannot write, so the files go through code on both sides:

    make   writes the postings one agent reads to data/score/batches/<step>-<n>.json:
           only postings stage 2d may read (rules.eligible) that passed the rule filter
           and still need the step
    save   reads what the agent returned (the orchestrating command writes it to
           data/score/results/<step>-<n>.json), keeps only postings of that batch, checks
           every value against the job schema (types, enums, lengths) and stores it as
           `extracted` or `match`; after an extraction the rule filter runs again

The agent's answer is LLM output from untrusted text, so it is never trusted beyond the
schema: unknown ids are dropped, an invalid entry is left out and the posting waits for
the next run. Counts and errors name an id and a field, never a value.
"""

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from jobfinder import jobs
from jobfinder.score import rules

STEPS = ("extract", "match")
EXTRACT_MODEL = "haiku"  # the model in .claude/agents/job-extractor.md
BATCH_SIZE = {"extract": 10, "match": 10}
POSTING_FIELDS = ("id", "title", "company_name", "location", "work_modes", "employment_type",
                  "seniority", "salary", "description")
EXTRACT_LISTS = ("required_skills", "preferred_skills", "required_languages")
EXTRACT_FIELDS = ("industry", "seniority", "min_years_experience", *EXTRACT_LISTS, "salary",
                  "visa_sponsorship")
MATCH_FIELDS = ("score", "summary", "strengths", "gaps")


class BatchError(Exception):
    pass


def pending(store: dict, step: str) -> list[dict]:
    """Jobs that still need the step: extraction first, then matching."""
    ready = [job for job in store["jobs"]
             if rules.eligible(job) and job.get("filter", {}).get("passed")]
    if step == "extract":
        return [job for job in ready if "extracted" not in job]
    return [job for job in ready if "extracted" in job and "match" not in job]


def make(store: dict, step: str, limit: int, batches_dir: Path, results_dir: Path,
         size: int | None = None, profile_md: str | None = None) -> dict:
    """Write up to `limit` pending jobs into batch files of `size`; old files of the step go.
    A match batch carries the profile (`profile_md`) too, so the agent reads one file."""
    size = size or BATCH_SIZE[step]
    for old in [*batches_dir.glob(f"{step}-*.json"), *results_dir.glob(f"{step}-*.json")]:
        old.unlink()
    todo = pending(store, step)
    chosen = todo[:limit]
    batches_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    batches = []
    for n, start in enumerate(range(0, len(chosen), size), 1):
        part = chosen[start:start + size]
        path = batches_dir / f"{step}-{n}.json"
        postings = [_posting(job, step) for job in part]
        batch = {"step": step, **({"profile": profile_md} if step == "match" else {}), "jobs": postings}
        path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        batches.append({"path": str(path), "result_path": str(results_dir / path.name),
                        "count": len(part)})
    return {"step": step, "count": len(chosen), "remaining": len(todo) - len(chosen),
            "batches": batches}


def _posting(job: dict, step: str) -> dict:
    posting = {key: job.get(key) for key in POSTING_FIELDS}
    posting["description"] = jobs.for_agent(posting["description"])
    if step == "match":
        posting["extracted"] = {key: job["extracted"][key] for key in EXTRACT_FIELDS}
    return posting


def save(store: dict, batch_path: Path, result_path: Path, matcher_model: str, profile: dict,
         now: datetime | None = None, exclude: list[str] | tuple = ()) -> dict:
    """Store an agent's answer for one batch. Returns counts, never values."""
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    step = batch.get("step")
    if step not in STEPS:
        raise BatchError(f"{batch_path} is not a batch file")
    model = EXTRACT_MODEL if step == "extract" else matcher_model
    entries = [entry for entry in read_answer(result_path)
               if isinstance(entry, dict) and isinstance(entry.get("id"), str)]
    stamp = (now or datetime.now(UTC)).isoformat()
    in_batch = {posting["id"] for posting in batch["jobs"]}
    todo = {job["id"]: job for job in pending(store, step) if job["id"] in in_batch}
    saved, invalid = [], []
    for entry in entries:
        job = todo.pop(entry.get("id"), None)
        if job is None:
            continue  # not in this batch, answered twice, or decided or filtered out meanwhile
        key, value = _record(step, entry, model, stamp)
        if errors := jobs.validate({**job, key: value}):
            invalid.append(f"{job['id']} {errors[0].split(':')[0]}")  # the field, not the value
            continue
        job[key] = value
        if step == "extract":
            rules.refilter(job, profile, stamp, exclude)
        saved.append(job)
    answered = {entry.get("id") for entry in entries}
    result = {"step": step, "saved": len(saved), "invalid": len(invalid),
              "unknown": len(answered - in_batch), "missing": len(in_batch - answered)}
    if invalid:
        result["first_invalid"] = invalid[0]
    if step == "extract":
        rejected = [job["filter"]["reasons"] for job in saved if not job["filter"]["passed"]]
        result["rejected"] = len(rejected)
        result["reasons"] = dict(sorted(Counter(r for found in rejected for r in found).items()))
    return result


def read_answer(result_path: Path, key: str = "jobs") -> list:
    """The `key` list of an agent's JSON answer; a ```json fence around it is fine."""
    text = result_path.read_text(encoding="utf-8").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BatchError(f"{result_path} is not JSON: {exc.msg} at line {exc.lineno}") from None
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise BatchError(f"{result_path} must be an object with a '{key}' list")
    return data[key]


def _record(step: str, entry: dict, model: str, stamp: str) -> tuple[str, dict]:
    if step == "extract":
        value = {key: entry.get(key, [] if key in EXTRACT_LISTS else None) for key in EXTRACT_FIELDS}
        return "extracted", {**value, "model": model, "extracted_at": stamp}
    value = {key: entry.get(key) for key in MATCH_FIELDS}
    return "match", {**value, "model": model, "scored_at": stamp}
