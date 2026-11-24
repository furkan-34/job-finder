"""Apify access: token check and actor runs.

The token is read from .env here, so the calling agent never sees it.
Posting text is untrusted: test runs return only short structured fields
(title, company, location, date, link) of postings that pass the stage 2c safety
check, and keep the full items on disk.
"""

import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apify_client import ApifyClient
from apify_client.errors import ApifyApiError, UnauthorizedError
from dotenv import load_dotenv

from jobfinder import safety
from jobfinder.paths import ENV_FILE, RAW_DIR
from jobfinder.sources.errors import SourceError
from jobfinder.sources.mapping import build_input, to_job

SUMMARY_FIELDS = ("title", "company_name", "location", "posted_at", "url")
DATE_POSTED = ("anyTime", "past24Hours", "pastWeek", "pastMonth")


def make_client() -> ApifyClient:
    load_dotenv(ENV_FILE)
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        raise SourceError(f"APIFY_TOKEN is not set. Add it to {ENV_FILE} (see .env.example).")
    return ApifyClient(token)


@contextmanager
def _api_errors():
    """Turn client, API and network failures into one readable SourceError."""
    try:
        yield
    except SourceError:
        raise
    except UnauthorizedError as exc:
        raise SourceError("Apify rejected the token (401). Check APIFY_TOKEN in .env.") from exc
    except ApifyApiError as exc:
        raise SourceError(f"Apify API error: {exc}") from exc
    except Exception as exc:  # transport errors come from the client's HTTP library
        raise SourceError(f"Could not reach Apify: {exc}") from exc


def check(client: ApifyClient) -> dict:
    """Verify the token and report this month's usage."""
    with _api_errors():
        user = client.user().get()
        limits = client.user().limits()
    plan = getattr(user, "plan", None)
    used = limits.current.monthly_usage_usd
    limit = limits.limits.max_monthly_usage_usd
    return {
        "ok": True,
        "username": user.username,
        "plan": plan.id if plan else None,
        "monthly_used_usd": round(used, 4),
        "monthly_limit_usd": limit,
        "monthly_remaining_usd": round(limit - used, 4),
        "cycle_ends": limits.monthly_usage_cycle.end_at.date().isoformat(),
    }


def run_actor(client: ApifyClient, actor_id: str, run_input: dict,
              limit: int) -> tuple[object, list[dict]]:
    """Run one actor and return (run, items)."""
    with _api_errors():
        run = client.actor(actor_id).call(
            run_input=run_input, max_items=limit, run_timeout=timedelta(minutes=10), logger=None)
        if run is None or run.status != "SUCCEEDED":
            status = run.status if run else "unknown"
            raise SourceError(f"Actor run did not succeed (status: {status}).")
        items = client.dataset(run.default_dataset_id).list_items().items
    return run, items


def save_raw(raw_dir: Path, data_source: str, actor_id: str, fetched_at: datetime, run,
             run_input: dict, items: list[dict], search_id: str | None = None) -> Path:
    """Keep the full actor output on disk for later reprocessing."""
    stamp = f"{fetched_at:%Y%m%dT%H%M%SZ}"
    raw_path = raw_dir / data_source / (f"{stamp}_{search_id}.json" if search_id else f"{stamp}.json")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps({
        "data_source": data_source,
        "actor": actor_id,
        "run_id": run.id,
        "search_id": search_id,
        "fetched_at": fetched_at.isoformat(),
        "input": run_input,
        "items": items,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return raw_path


def try_actor(client: ApifyClient, actor: dict, keywords: str, location: str, limit: int = 5,
               date_posted: str = "pastWeek", raw_dir: Path = RAW_DIR) -> dict:
    """Run an actor for a few postings, save the raw items, return a summary.

    Each posting gets the safety check first; a blocked one is only counted, so its
    text never reaches the caller."""
    if date_posted not in DATE_POSTED:
        raise SourceError(f"date_posted must be one of {', '.join(DATE_POSTED)}")
    run_input = build_input(actor, keywords, location, limit, date_posted)
    run, items = run_actor(client, actor["actor_id"], run_input, limit)
    fetched_at = datetime.now(UTC)
    raw_path = save_raw(raw_dir, actor["name"], actor["actor_id"], fetched_at, run, run_input, items)
    jobs = [job for item in items if (job := to_job(item, actor, fetched_at))]
    rules = safety.load_rules()
    scorer = safety.load_scorer(rules)
    verdicts = [safety.check(job, rules, scorer)["verdict"] for job in jobs]
    shown = [{**{field: job.get(field) for field in SUMMARY_FIELDS}, "safety": verdict}
             for job, verdict in zip(jobs, verdicts) if verdict != "block"]
    return {
        "ok": True,
        "count": len(items),
        "cost_usd": run.usage_total_usd,
        "raw_path": str(raw_path),
        "blocked": len(jobs) - len(shown),
        "jobs": shown,
    }
