"""Run every enabled search with each chosen actor and add the new postings to the job store.

(An entry of kind "freehire" is read from freehire.me's API, and one of kind "greenhouse", "lever"
or "ashby" from that ATS's company job boards, instead of run on Apify: no token, no cost.) For each actor and search the full actor output is kept under
data/fetch/raw/<DATA_SOURCE>/ (stage 2a), then that raw file is mapped to the
standard shape and added to data/jobs/jobs.json by jobs.import_raw (stage 2b),
which skips postings already there and reposts of them. The new postings then
get the stage 2c safety check (jobfinder.safety). One
summary per run goes to data/fetch/runs/<timestamp>.json: counts, costs and
paths only, never posting text.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from jobfinder import jobs, safety
from jobfinder.guard import GuardError
from jobfinder.paths import JOBS_JSON, RAW_DIR, RUNS_DIR
from jobfinder.searches import enabled, max_cost
from jobfinder.sources import ats, freehire
from jobfinder.sources.actors import uses_apify
from jobfinder.sources.apify import SourceError, check, run_actor, save_raw
from jobfinder.sources.mapping import build_input


def run_searches(client, data: dict, actors: list[dict], date_posted: str = "anyTime",
                 raw_dir: Path = RAW_DIR, runs_dir: Path = RUNS_DIR,
                 jobs_json: Path = JOBS_JSON) -> dict:
    started_at = datetime.now(UTC)
    summary = {
        "data_sources": [a["name"] for a in actors],
        "date_posted": date_posted,
        "started_at": started_at.isoformat(),
        "searches": [],
    }
    try:
        if uses_apify(actors):
            _check_budget(client, max_cost(data, actors))
    except SourceError as exc:
        summary["error"] = str(exc)
    else:
        store = jobs.load(jobs_json)
        for actor in actors:
            for search in enabled(data):
                summary["searches"].append(
                    _run_one(client, actor, actors, search, date_posted, raw_dir, store, jobs_json))
        summary["safety"] = _check_safety(store, jobs_json)

    done = [s for s in summary["searches"] if "error" not in s]
    summary.update(
        ok="error" not in summary and bool(done),
        finished_at=datetime.now(UTC).isoformat(),
        total_count=sum(s["count"] for s in done),
        total_new=sum(s["new"] for s in done),
        total_reposts=sum(s["reposts"] for s in done),
        total_invalid=sum(s["invalid"] for s in done),
        total_cost_usd=round(sum(s["cost_usd"] or 0 for s in done), 4),
        jobs_path=str(jobs_json),
    )
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_path = runs_dir / f"{started_at:%Y%m%dT%H%M%SZ}.json"
    run_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**summary, "run_path": str(run_path)}


def _run_one(client, actor, actors, search, date_posted, raw_dir, store, jobs_json) -> dict:
    entry = {"data_source": actor["name"], "search_id": search["id"]}
    try:
        if actor.get("kind") == "freehire":
            run, run_input, items = freehire.fetch(actor, search["keywords"], search["location"],
                                                   search["limit"], date_posted)
        elif actor.get("kind") in ats.KINDS:
            run, run_input, items = ats.fetch(actor, search["keywords"], search["location"],
                                              search["limit"], date_posted)
        else:
            run_input = build_input(actor, search["keywords"], search["location"], search["limit"],
                                    date_posted)
            run, items = run_actor(client, actor["actor_id"], run_input, search["limit"])
    except SourceError as exc:
        return {**entry, "error": str(exc)}
    fetched_at = datetime.now(UTC)
    path = save_raw(raw_dir, actor["name"], actor["actor_id"], fetched_at, run, run_input, items,
                    search["id"])
    imported = jobs.import_raw(store, path, actors)
    jobs.save(store, jobs_json)
    return {**entry, **imported, "cost_usd": run.usage_total_usd}


def _check_safety(store: dict, jobs_json: Path) -> dict:
    """Stage 2c on the postings not checked yet; unchecked ones wait for `jobs check`."""
    try:
        rules = safety.load_rules()
        counts = safety.check_store(store, rules, safety.load_scorer(rules))
    except (safety.SafetyError, GuardError, OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc)}
    if counts["checked"]:
        jobs.save(store, jobs_json)
    return counts


def _check_budget(client, worst_case: float) -> None:
    """Refuse to start when the worst-case cost exceeds this month's remaining credit."""
    remaining = check(client)["monthly_remaining_usd"]
    if worst_case > remaining:
        raise SourceError(
            f"Not enough Apify credit: this run may cost up to ${worst_case:.2f}, "
            f"${remaining:.2f} is left this month. Lower the limits in /setup-searches "
            "or choose fewer data sources.")
