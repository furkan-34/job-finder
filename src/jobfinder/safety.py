"""Stage 2c: safety check of every posting, before any LLM reads it.

Plain code, no LLM. Each job in data/jobs/jobs.json gets a `safety` entry:

    {"verdict": "pass" | "warn" | "block", "reasons": ["fee_request", ...],
     "model_score": 0.02, "checked_at": "..."}

    block  asks for money, tries to give an LLM instructions (prompt injection), or hides
           text in invisible characters; stage 2d never reads it
    warn   something to look at (apply link to an unknown domain, link shortener, chat
           contact, text addressed to an AI, ...); scored, and the reasons are shown with
           the posting
    pass   none of the above

Two layers look for prompt injection: regular expressions (English and Turkish), and
a small local classifier (jobfinder.guard, Prompt Guard 2 22M) whose probability is
stored as `model_score`; at or above the threshold in config/safety.json the posting
is blocked as `prompt_injection_model`. Without the downloaded model `model_score` is
null and the posting is scored the next time the check runs with the model.

Every text field of the posting is checked, not only the description: a title, a
company name or a link can carry an injection too. The patterns and domain lists are in
config/safety.json. Reasons are codes, plus a domain for the link checks, never posting
text, so they are safe to show to an LLM.
"""

import ipaddress
import json
import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

from jobfinder import guard
from jobfinder.jobs import company_key
from jobfinder.paths import SAFETY_JSON

VERDICTS = ("pass", "warn", "block")
URL_PATTERN = re.compile(r"(?:https?://|www\.)[^\s<>\"'()\[\]{}]+", re.IGNORECASE)
# Removed before matching, so "ign​ore" still reads "ignore".
INVISIBLE = re.compile("[​-‏⁠-⁤﻿\U000e0000-\U000e007f]")
# Never in honest text: Unicode tag characters (hidden text) and bidirectional overrides.
HIDDEN = re.compile("[\U000e0000-\U000e007f‪-‮⁦-⁩]")
# Second-level labels under a country code: careers.acme.co.uk -> acme.co.uk.
SECOND_LEVEL = {"co", "com", "org", "net", "gov", "edu", "ac", "gen", "biz", "info"}


class SafetyError(Exception):
    pass


@cache
def load_rules(path: Path = SAFETY_JSON) -> dict:
    """The rules with every pattern compiled; a bad pattern fails here, not mid-run."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = {"block": {}, "warn": {}}
    for level in rules:
        for code, patterns in data.get(level, {}).items():
            try:
                rules[level][code] = [re.compile(p, re.IGNORECASE | re.MULTILINE)
                                     for p in patterns]
            except re.error as exc:
                raise SafetyError(f"{path}: {level}.{code}: bad pattern: {exc}") from exc
    for key in ("ats_domains", "shorteners"):
        rules[key] = tuple(d.lower() for d in data.get(key, []))
    rules["model"] = data.get("model")
    return rules


def load_scorer(rules: dict) -> Callable[[str], float] | None:
    """The local classifier, or None when it is not configured or not downloaded yet."""
    return guard.load_scorer(rules["model"]) if rules.get("model") else None


def check(job: dict, rules: dict, scorer: Callable[[str], float] | None = None) -> dict:
    """The verdict and reasons for one job (without `checked_at`)."""
    raw = "\n".join(_texts(job))
    visible = INVISIBLE.sub("", raw)
    text = visible.replace("İ", "i").lower()
    block = [code for code, patterns in rules["block"].items() if any(p.search(text) for p in patterns)]
    if HIDDEN.search(raw):
        block.append("hidden_characters")
    model_score = round(scorer(visible), 4) if scorer else None
    if model_score is not None and model_score >= rules["model"]["threshold"]:
        block.append("prompt_injection_model")
    warn = [code for code, patterns in rules["warn"].items() if any(p.search(text) for p in patterns)]
    warn += _apply_link(job, rules)
    for url in URL_PATTERN.findall(raw):
        warn += _link_problems(_host(url), rules)
    # A domain comes from posting text: keep only the characters a domain can have.
    reasons = list(dict.fromkeys(re.sub(r"[^a-z0-9.:_-]", "", r) for r in block + warn))
    verdict = "block" if block else "warn" if warn else "pass"
    return {"verdict": verdict, "reasons": reasons, "model_score": model_score}


def _texts(job: dict) -> list[str]:
    """Every text value of the posting (strings in lists too), except our own `safety` entry."""
    values = [v for key, v in job.items() if key != "safety"]
    values += [v for value in values if isinstance(value, list) for v in value]
    return [v for v in values if isinstance(v, str)]


def check_store(store: dict, rules: dict, scorer: Callable[[str], float] | None = None,
                recheck: bool = False, now: datetime | None = None) -> dict:
    """Check the jobs that have no `safety` entry yet, and, with a scorer, the ones checked
    without the model (every job with recheck). Returns counts per verdict and per reason
    code (no domains, no text) and whether the model was used."""
    checked_at = (now or datetime.now(UTC)).isoformat()
    verdicts, reasons = Counter(), Counter()
    for job in store["jobs"]:
        done = "safety" in job and (scorer is None or job["safety"].get("model_score") is not None)
        if done and not recheck:
            continue
        job["safety"] = {**check(job, rules, scorer), "checked_at": checked_at}
        verdicts[job["safety"]["verdict"]] += 1
        reasons.update(r.split(":")[0] for r in job["safety"]["reasons"])
    return {"checked": sum(verdicts.values()), **{v: verdicts[v] for v in VERDICTS},
            "reasons": dict(sorted(reasons.items())), "model": scorer is not None}


def _apply_link(job: dict, rules: dict) -> list[str]:
    """An apply link must go to the source site, a known ATS or the company's own domain."""
    url = (job.get("apply_url") or "").strip()
    if not url:
        return []  # applied on the source site (e.g. LinkedIn Easy Apply)
    host = _host(url)
    if host is None:
        return ["apply_url_invalid"]
    problems = _link_problems(host, rules)
    if urlsplit(url).scheme.lower() == "http":
        problems.append("apply_not_https")
    if problems:
        return problems
    domain = base_domain(host)
    if (_matches(host, rules["ats_domains"]) or domain == base_domain(_host(job.get("url")))
            or domain == base_domain(_host(job.get("company_website")))
            or _named_after(domain, job.get("company_name"))):
        return []
    return [f"apply_domain_unknown:{domain}"]


def _link_problems(host: str | None, rules: dict) -> list[str]:
    if host is None:
        return []
    if _is_ip(host):
        return ["ip_link"]
    if host.startswith("xn--") or ".xn--" in host:
        return [f"punycode_link:{host}"]
    if _matches(host, rules["shorteners"]):
        return [f"link_shortener:{base_domain(host)}"]
    return []


def _host(url: str | None) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.rstrip(".").lower() if host and "." in host else None


def base_domain(host: str | None) -> str | None:
    """careers.acme.com -> acme.com, jobs.acme.co.uk -> acme.co.uk."""
    if not host or _is_ip(host):
        return host
    labels = host.split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def _matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _named_after(domain: str, company_name: str | None) -> bool:
    """acme.com for "Acme Inc.", acmesoftware.io for "Acme Software", acme.de for "Acme GmbH"."""
    name = company_key(company_name).replace(" ", "")
    label = domain.split(".")[0].replace("-", "")
    return len(name) >= 3 and (name in label or (len(label) >= 4 and label in name))


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
