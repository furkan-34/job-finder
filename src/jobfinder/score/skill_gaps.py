"""The skill gap report: skills the postings ask for that the profile does not have.

Code only, no LLM. It reads `extracted` (stage 2d) of the postings the safety check let
through (pass or warn) that passed the rule filter and were added in the last `days` days,
whatever their status. A skill is in the profile as `cv save` decides it
(writer.missing_skills: aliases from config/cv_check.json, the technologies of every job and
project, the generic skills). Names are grouped under one name per skill (writer.canonical_skill).

Each posting counts once per skill: 2 when it requires it, 1 when it only prefers it, times
the posting's match score / 100 (0.5 when it has no score), so the skills of the postings that
fit best come first. Skill names come from an LLM that read untrusted text; they are capped by
the job schema and are only shown. Each skill also has a `key` (lower-case letters, digits and
dashes) by which `jobfinder profile add-skill` adds it, so posting text never goes into a shell
command.
"""

import re
from datetime import date, datetime, timedelta

from jobfinder.cv import writer

DAYS = 90
TOP = 15
EXAMPLES = 3
UNSCORED = 0.5


def postings(store: dict, days: int, today: date) -> list[dict]:
    """The postings the report reads, newest first."""
    since = today - timedelta(days=days)
    found = [job for job in store["jobs"]
             if job.get("safety", {}).get("verdict") in ("pass", "warn")
             and job.get("filter", {}).get("passed") and "extracted" in job
             and datetime.fromisoformat(job["created_at"]).date() >= since]
    return sorted(found, key=lambda job: job["created_at"], reverse=True)


def report(store: dict, profile: dict, days: int = DAYS, top: int | None = TOP,
           today: date | None = None, terms: dict | None = None) -> dict:
    terms = terms or writer.load_terms()
    read = postings(store, days, today or date.today())
    skills: dict[str, dict] = {}
    for job in read:
        extracted = job["extracted"]
        asked = {}  # skill -> required?, the first name the posting uses
        for required, names in ((True, extracted["required_skills"]),
                                (False, extracted["preferred_skills"])):
            for name in writer.missing_skills([n for n in names if n.strip()], profile, terms):
                asked.setdefault(writer.canonical_skill(name, terms), required)
        score = job["match"]["score"] if "match" in job else None
        for skill, required in asked.items():
            entry = skills.setdefault(skill, {"skill": skill, "key": key(skill), "weight": 0.0, "postings": 0,
                                              "required": 0, "preferred": 0, "scores": [],
                                              "examples": []})
            entry["weight"] += (2 if required else 1) * (UNSCORED if score is None else score / 100)
            entry["postings"] += 1
            entry["required" if required else "preferred"] += 1
            if score is not None:
                entry["scores"].append(score)
            if len(entry["examples"]) < EXAMPLES:
                entry["examples"].append({key: job.get(key) for key in ("id", "title", "company_name")})
    ranked = sorted(skills.values(), key=lambda e: (-e["weight"], -e["postings"], e["skill"].casefold()))
    return {"days": days, "postings": len(read), "count": len(ranked),
            "skills": [_shown(entry) for entry in ranked[:top]]}


def key(skill: str) -> str:
    """'Apache Kafka' -> 'apache-kafka', 'C++' -> 'c-plus-plus': '+' and '#' are spelled out so
    C, C++ and C# differ."""
    spelled = skill.casefold().replace("+", " plus ").replace("#", " sharp ")
    return re.sub(r"[^a-z0-9]+", "-", spelled).strip("-") or "skill"


def find(store: dict, profile: dict, skill_key: str, days: int = DAYS, today: date | None = None,
         terms: dict | None = None) -> dict | None:
    """The report's entry with this key, among every missing skill (not only the top ones)."""
    return next((entry for entry in report(store, profile, days, None, today, terms)["skills"]
                 if entry["key"] == skill_key), None)


def _shown(entry: dict) -> dict:
    scores = entry.pop("scores")
    return {**entry, "weight": round(entry["weight"], 2),
            "avg_score": round(sum(scores) / len(scores)) if scores else None}
