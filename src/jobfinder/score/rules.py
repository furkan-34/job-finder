"""Stage 2d, step 1: the rule filter. Plain code, no LLM.

Every posting that passed the safety check (stage 2c, verdict `pass` or `warn`) and is
still `new` gets a `filter` entry:

    {"passed": true | false, "reasons": ["dealbreaker_keyword:crypto", ...], "checked_at": "..."}

A `block` posting, or one not checked yet, is never touched. The rules come from the
candidate's preferences in data/profile/profile.json:

    dealbreaker_company:<name>    the company is on the dealbreaker list (legal forms ignored)
    dealbreaker_keyword:<word>    a dealbreaker keyword in the title, company or description
    excluded_word:<word>          a word of the search's exclude list (data/fetch/settings.json,
                                  `exclude_words`) in the title, company or description
    employment_type:<type>        the source's employment type is not one the candidate wants
    seniority:<level>             the source's seniority level (e.g. LinkedIn "Entry level")
                                  covers none of the wanted levels
    work_mode:<mode>              no work mode of the posting is one the candidate wants

Once the structured extraction (step 2) has added `extracted`, the posting is checked
again with these as well:

    dealbreaker_industry:<name>   the extracted industry is a dealbreaker
    language:<name>               a required language the candidate does not speak
    seniority:<level>             the extracted level is more than one step from every wanted level

After that, one posting is kept of each group of the same posting in several cities:

    same_posting:<id>             the same company, title and description (the city named in
                                  the description aside) as posting <id>, which stage 2d reads
                                  instead

The one kept has already been worked on (extracted, scored or past `new`), else its city is
one of the candidate's locations, else it was saved first. A posting is grouped only when its
description is the same word for word, so a real difference is never lost.

A field the source leaves empty, or writes in a way the tables below do not know,
never rejects a posting. Reason values are the candidate's own words or codes from
these tables, never posting text.
"""

import hashlib
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime

from jobfinder.jobs import company_key, normalize

SAME = "same_posting:"

LEVELS = ("intern", "junior", "mid", "senior", "lead", "principal")
# Source employment types, normalized -> profile employment_types ("fulltime", "parttime" and
# "intern" are Ashby's, "permanent" is Lever's).
EMPLOYMENT_TYPES = {
    "full time": "full_time", "part time": "part_time", "contract": "contract",
    "temporary": "contract", "freelance": "freelance", "internship": "internship",
    "volunteer": "volunteer", "fulltime": "full_time", "parttime": "part_time",
    "permanent": "full_time", "intern": "internship",
}
# Source seniority levels (LinkedIn's), normalized -> the profile levels they can mean.
SENIORITY = {
    "internship": {"intern"},
    "entry level": {"junior"},
    "associate": {"junior", "mid"},
    "mid senior level": {"mid", "senior", "lead"},
    "director": {"lead", "principal"},
    "executive": {"principal"},
}


def check(job: dict, preferences: dict, languages: list[dict], exclude: list[str] | tuple = ()) -> list[str]:
    """Reasons to drop the job (empty: it goes on to the next step)."""
    reasons = []
    dealbreakers = preferences["dealbreakers"]
    company = company_key(job.get("company_name"))
    reasons += [f"dealbreaker_company:{name.lower()}" for name in dealbreakers["companies"]
                if company and company_key(name) == company]
    text = "\n".join(job.get(key) or "" for key in ("title", "company_name", "description"))
    reasons += [f"dealbreaker_keyword:{word.lower()}" for word in dealbreakers["keywords"]
                if _has_word(text, word)]
    reasons += [f"excluded_word:{word.lower()}" for word in exclude if _has_word(text, word)]
    employment = EMPLOYMENT_TYPES.get(normalize(job.get("employment_type")))
    if employment and employment not in preferences["employment_types"]:
        reasons.append(f"employment_type:{employment}")
    levels = SENIORITY.get(normalize(job.get("seniority")))
    if levels and not levels & set(preferences["seniority"]):
        reasons.append(f"seniority:{normalize(job['seniority']).replace(' ', '_')}")
    wanted_modes = {mode for place in preferences["locations"] for mode in place["work_modes"]}
    if job.get("work_modes") and not set(job["work_modes"]) & wanted_modes:
        reasons.append(f"work_mode:{'+'.join(sorted(job['work_modes']))}")
    if extracted := job.get("extracted"):
        reasons += _check_extracted(extracted, preferences, languages)
    return list(dict.fromkeys(reasons))


def _check_extracted(extracted: dict, preferences: dict, languages: list[dict]) -> list[str]:
    reasons = [f"dealbreaker_industry:{name.lower()}"
               for name in preferences["dealbreakers"]["industries"]
               if _has_word(extracted.get("industry") or "", name)]
    spoken = {normalize(lang["language"]) for lang in languages}
    reasons += [f"language:{normalize(lang)}" for lang in extracted.get("required_languages", [])
                if normalize(lang) not in spoken]
    level = extracted.get("seniority")
    if level and min(abs(LEVELS.index(level) - LEVELS.index(wanted))
                     for wanted in preferences["seniority"]) > 1:
        reasons.append(f"seniority:{level}")
    return reasons


def _has_word(text: str, word: str) -> bool:
    """`word` in `text` as a whole word or phrase, ignoring case ("C++" too)."""
    word = word.strip().replace("İ", "i").lower()
    return bool(word) and re.search(rf"(?<![\w]){re.escape(word)}(?![\w])",
                                    text.replace("İ", "i").lower()) is not None


def eligible(job: dict) -> bool:
    """Checked by stage 2c, not blocked, and not decided yet: the only postings stage 2d reads."""
    return job.get("safety", {}).get("verdict") in ("pass", "warn") and job.get("status") == "new"


def check_store(store: dict, profile: dict, recheck: bool = False,
                now: datetime | None = None, exclude: list[str] | tuple = ()) -> dict:
    """Filter the eligible jobs that have no `filter` entry yet (every eligible job with
    recheck, e.g. after the preferences changed). Returns counts and the reasons."""
    checked_at = (now or datetime.now(UTC)).isoformat()
    checked, passed, reasons = 0, 0, Counter()
    for job in store["jobs"]:
        if not eligible(job) or ("filter" in job and not recheck):
            continue
        refilter(job, profile, checked_at, exclude)
        checked += 1
    changed = keep_one_of_each(store, profile, checked_at)
    for job in store["jobs"]:
        if eligible(job) and job.get("filter", {}).get("checked_at") == checked_at:
            passed += job["filter"]["passed"]
            reasons.update(job["filter"]["reasons"])
    return {"checked": checked, "passed": passed, "rejected": checked - passed,
            "reasons": dict(sorted(reasons.items())), "same_posting_changed": changed}


def city(job: dict) -> str:
    return normalize((job.get("location") or "").split(",")[0])


def group_key(job: dict) -> str | None:
    """company|title|hash of the description without the posting's own city; None when a part
    is missing (never grouped)."""
    company = company_key(job.get("company_name"))
    title = normalize(re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", job.get("title") or ""))
    text = normalize(job.get("description"))
    if not (company and title and text):
        return None
    if place := city(job):
        text = " ".join(re.sub(rf"\b{re.escape(place)}\b", " ", text).split())
    return f"{company}|{title}|{hashlib.sha1(text.encode()).hexdigest()}"


def keep_one_of_each(store: dict, profile: dict, checked_at: str) -> int:
    """Mark every copy of a posting but one with `same_posting:<id>` (see the module text).
    Returns how many filter entries changed."""
    groups = defaultdict(list)
    for job in store["jobs"]:
        if job.get("safety", {}).get("verdict") in ("pass", "warn") and (key := group_key(job)):
            groups[key].append(job)
    places = {normalize(place["place"].split(",")[0]) for place in profile["preferences"]["locations"]}
    changed = 0
    for members in groups.values():
        candidates = [job for job in members if _worked_on(job) or _passes_but_same(job)]
        if len(candidates) < 2:
            continue
        keep = min(candidates, key=lambda job: (not _worked_on(job), city(job) not in places,
                                                job.get("created_at") or ""))
        for job in candidates:
            if _worked_on(job):
                continue
            own = [reason for reason in job["filter"]["reasons"] if not reason.startswith(SAME)]
            wanted = own if job is keep else own + [SAME + keep["id"]]
            if wanted != job["filter"]["reasons"]:
                job["filter"] = {"passed": not wanted, "reasons": wanted, "checked_at": checked_at}
                changed += 1
    return changed


def _worked_on(job: dict) -> bool:
    return job.get("status") != "new" or "extracted" in job or "match" in job


def _passes_but_same(job: dict) -> bool:
    """Eligible and filtered, and rejected for nothing but being a copy."""
    found = job.get("filter")
    return (eligible(job) and found is not None
            and all(reason.startswith(SAME) for reason in found["reasons"]))


def refilter(job: dict, profile: dict, checked_at: str, exclude: list[str] | tuple = ()) -> None:
    found = check(job, profile["preferences"], profile["skills"]["languages"], exclude)
    job["filter"] = {"passed": not found, "reasons": found, "checked_at": checked_at}
