"""The shortlist: scored postings, best first, each with its safety warnings.

Only postings stage 2d may read (rules.eligible), that passed the rule filter and have
a `match` score at or above the threshold. The entry holds the short fields of the
posting (as `jobfinder source test` lists them), the score and its reasons, and the
`warn` reasons of the safety check, so they are always shown with the posting, and `also_in`:
the cities of its copies the rule filter set aside, and `people_search` (see overview).
"""

from jobfinder.overview import copies, people_search
from jobfinder.score.rules import eligible

FIELDS = ("id", "title", "company_name", "location", "work_modes", "posted_at", "url", "apply_url")


def ranked(store: dict, min_score: int, top: int | None) -> list[dict]:
    """The best `top` postings at or above `min_score` (all of them with None)."""
    scored = [job for job in store["jobs"]
              if eligible(job) and job.get("filter", {}).get("passed") and "match" in job
              and job["match"]["score"] >= min_score]
    scored.sort(key=lambda job: (-job["match"]["score"], job["created_at"]))
    also_in = copies(store)
    return [_entry(job, also_in.get(job["id"], [])) for job in scored[:top]]


def _entry(job: dict, also_in: list[str]) -> dict:
    match = job["match"]
    warnings = job["safety"]["reasons"] if job["safety"]["verdict"] == "warn" else []
    return {**{key: job.get(key) for key in FIELDS}, "score": match["score"],
            "summary": match["summary"], "strengths": match["strengths"], "gaps": match["gaps"],
            "warnings": warnings, "also_in": also_in, "people_search": people_search(job)}
