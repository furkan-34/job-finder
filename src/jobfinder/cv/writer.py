"""Stage 3b: the input file for the `cv-writer` agent, and checking and storing what it returns.

The agent reads posting text, so it can only read (.claude/agents/cv-writer.md). Code does
everything around it:

    make   writes data/cv/<DD-MM-YYYY>/<key>/input.json for each approved posting (or one
           given posting), under the day it runs:
           the posting's fields and the profile without contact details, with every
           experience, achievement and project numbered (`exp-1`, `exp-1-a2`, `proj-1`)
    save   reads the agent's answer (the orchestrating command writes it to answer.json next
           to the input), checks it against the profile, renders the CV and the cover letter
           as PDF (cv.render) into the same folder and moves the posting from approved to
           cv_ready; a rejected answer leaves rejection.json there, with the values that broke
           a rule, for the agent's second try

No invented facts. Names, contact details, titles, companies, places, dates and education
come from profile.json and are written by code. The agent tailors the rest to the posting,
and code checks each piece against the profile:

    bullets       each one rewrites one achievement of that job (by reference); a rewrite with
                  a number the achievement does not have, or a skill that neither the
                  achievement nor the job's technologies name, is replaced by the achievement
                  word for word
    skills        grouped under short labels; only names the profile lists are kept
    projects      by reference
    summary and   every number must be in the profile; no link, email address, hidden
    cover letter  characters, or skill the posting asks for and the profile does not have
                  (anywhere in it, under any of its names in config/cv_check.json; soft skills
                  listed there as generic never count as missing)

The contact details never go to the agent; render adds them. The answer is LLM output from
untrusted text: what can be dropped is dropped and counted, a summary or letter that breaks a
rule rejects the answer and the posting stays approved. Errors name a field and a code, never
a value: the values go only to rejection.json, which only the agent reads.
"""

import json
import re
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

from jobfinder import jobs
from jobfinder.cv import render
from jobfinder.paths import CV_CHECK_JSON
from jobfinder.safety import HIDDEN, INVISIBLE, URL_PATTERN

LANGUAGE = "en"
POSTING_FIELDS = ("id", "title", "company_name", "location", "work_modes", "employment_type",
                  "seniority", "description")
BULLET_MAX = 220  # characters
COVER_LETTER_MAX = 3000  # characters, all paragraphs together
DAY_FORMAT = "%d-%m-%Y"  # the day folder under data/cv/, e.g. 24-10-2026
REJECTION = "rejection.json"  # next to the input: why the last answer was rejected
REVIEW = "review.json"  # next to the input: the cv-reviewer's revised answer; untrusted
ANSWER_SCHEMA = {
    "type": "object",
    "required": ["id", "summary", "skills", "experience", "projects", "cover_letter"],
    "properties": {
        "id": {"type": "string"},
        "summary": {"type": "string", "minLength": 1, "maxLength": 500},
        "skills": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
            "type": "object", "required": ["category", "items"],
            "properties": {"category": {"type": "string", "minLength": 1, "maxLength": 30},
                           "items": {"type": "array", "maxItems": 20,
                                     "items": {"type": "string", "maxLength": 60}}}}},
        "experience": {"type": "array", "maxItems": 30, "items": {
            "type": "object", "required": ["ref", "bullets"],
            "properties": {"ref": {"type": "string"},
                           "bullets": {"type": "array", "maxItems": 30, "items": {
                               "type": "object", "required": ["ref"],
                               "properties": {"ref": {"type": "string"},
                                              "text": {"type": "string", "maxLength": 400}}}}}}},
        "projects": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
        "cover_letter": {"type": "array", "minItems": 2, "maxItems": 6,
                         "items": {"type": "string", "minLength": 1, "maxLength": 1000}},
    },
}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# A number not glued to a letter before it: "2M", "40%", "3.3" count; "S3", "p95", "K8s" do not.
NUMBER = re.compile(r"(?<![A-Za-z0-9.,])\d+(?:[.,]\d+)*")
LABEL = re.compile(r"[A-Za-z][A-Za-z &/+().,-]*")  # a skill group's label: words, no digits
NOT_A_FACT = re.compile(r"(?<![\w/.])24\s?[/x]\s?7(?![\w/])")  # "24/7 on-call", "24x7"


class WriterError(Exception):
    pass


def key(job: dict) -> str:
    """The posting's folder name under data/cv/: the company name (cut to 60 characters) and
    the source id in lowercase ASCII words joined by '-', e.g. 'Acme Software GmbH' and
    4012345678 -> acme-software-gmbh-4012345678. Posting text, so nothing else gets through."""
    company = "-".join(render.ascii_words(job.get("company_name") or "")).lower()[:60].strip("-")
    source_id = "-".join(render.ascii_words(str(job.get("source_id") or job["id"])))
    return "-".join(part for part in (company, source_id) if part)


def ready(store: dict, job_id: str | None = None) -> list[dict]:
    """Postings to write documents for: every approved one, or the given one when it is
    approved or already cv_ready (written again). Never a posting the safety check blocked."""
    statuses = ("approved", "cv_ready") if job_id else ("approved",)
    return [job for job in store["jobs"]
            if job.get("safety", {}).get("verdict") in ("pass", "warn")
            and job["status"] in statuses and job_id in (None, job["id"])]


def make(store: dict, profile: dict, cv_dir: Path, job_id: str | None = None,
         today: date | None = None) -> dict:
    """Write the agent's input file for each posting that needs documents, into today's
    folder (data/cv/<DD-MM-YYYY>/<key>/); an old answer there goes."""
    todo = ready(store, job_id)
    if job_id and not todo:
        raise WriterError(f"{job_id} is not an approved posting")
    day = (today or date.today()).strftime(DAY_FORMAT)
    made = []
    for job in todo:
        folder = cv_dir / day / key(job)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "answer.json").unlink(missing_ok=True)
        (folder / REVIEW).unlink(missing_ok=True)
        (folder / REJECTION).unlink(missing_ok=True)
        path = folder / "input.json"
        path.write_text(json.dumps(writer_input(job, profile), ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        made.append({"id": job["id"], "title": job.get("title"),
                     "company_name": job.get("company_name"), "input_path": str(path),
                     "answer_path": str(folder / "answer.json")})
    return {"count": len(made), "jobs": made}


def writer_input(job: dict, profile: dict) -> dict:
    posting = {field: job.get(field) for field in POSTING_FIELDS}
    posting["description"] = jobs.for_agent(posting["description"])
    if extracted := job.get("extracted"):
        posting["extracted"] = {k: v for k, v in extracted.items() if k not in ("model", "extracted_at")}
    if match := job.get("match"):
        posting["match"] = {k: match[k] for k in ("score", "strengths", "gaps")}
    return {"language": LANGUAGE, "posting": posting, "profile": facts(profile)}


def facts(profile: dict) -> dict:
    """The profile as the agent sees it: no contact details, every item it can pick numbered."""
    summary, prefs = profile["summary"], profile["preferences"]
    extras, place = profile.get("extras") or {}, profile["identity"]["location"]
    return {
        "headline": summary["headline"],
        "total_years_experience": summary["total_years_experience"],
        "about": summary.get("about"),
        "based_in": f"{place['city']}, {place['country']}",
        "target_roles": prefs["target_roles"],
        "work_authorization": profile["work_authorization"],
        "open_to_relocation": prefs.get("open_to_relocation"),
        "availability": prefs.get("availability"),
        "experience": [
            {"ref": f"exp-{i}",
             **{k: exp[k] for k in ("title", "company", "start", "end", "location", "technologies")},
             "achievements": [{"ref": f"exp-{i}-a{j}", "text": text}
                              for j, text in enumerate(exp["achievements"], 1)]}
            for i, exp in enumerate(profile["experience"], 1)],
        "education": profile["education"],
        "skills": profile["skills"]["technical"],
        "languages": profile["skills"]["languages"],
        "certifications": extras.get("certifications", []),
        "projects": [{"ref": f"proj-{i}", "name": p["name"], "description": p["description"],
                      "technologies": p.get("technologies", [])}
                     for i, p in enumerate(extras.get("projects", []), 1)],
        "publications": [{k: v for k, v in p.items() if k != "url"}
                         for p in extras.get("publications", [])],
    }


def check(answer: object, job: dict, profile: dict, terms: dict | None = None) -> dict:
    """{"errors": [...], "problems": [...]} when the answer cannot be used, else
    {"errors": [], "plan", "dropped"}. A problem is an error with the values that caused it
    ({"field", "code", "values"}), for the agent only.

    The plan holds the checked choices: the summary, skill groups, final bullets per job (as
    profile indexes), project indexes and the cover letter; render adds every other fact."""
    if errors := _schema_errors(answer):
        return {"errors": errors, "problems": []}
    terms = terms or load_terms()
    problems = [] if answer["id"] == job["id"] else [{"field": "id", "code": "mismatch"}]
    extras = profile.get("extras") or {}
    spellings = _profile_skills(profile)  # the profile's own names, before aliases are added
    known = _known(spellings, terms)
    extracted = job.get("extracted") or {}
    asked = [s for s in (*extracted.get("required_skills", []), *extracted.get("preferred_skills", []))
             if s.strip()]
    lacking = missing_skills(asked, profile, terms)
    allowed = _numbers([facts(profile), job.get("title"), job.get("company_name")])
    problems += _text_problems("summary", answer["summary"], allowed, lacking, spellings)
    for n, paragraph in enumerate(answer["cover_letter"]):
        problems += _text_problems(f"cover_letter/{n}", paragraph, allowed, lacking, spellings)
    if sum(len(p) for p in answer["cover_letter"]) > COVER_LETTER_MAX:
        problems.append({"field": "cover_letter", "code": "too_long"})
    if problems:
        return {"errors": [f"{p['field']}: {p['code']}" for p in problems], "problems": problems}

    dropped = {"skills": 0, "achievements": 0, "rewrites": 0, "projects": 0}
    skills, seen = [], set()
    for group in answer["skills"]:
        items = []
        for name in group["items"]:
            if _fold(name) not in known:
                dropped["skills"] += 1
            elif (spelling := known[_fold(name)]) not in seen:  # an alias counts as its skill
                seen.add(spelling)
                items.append(spelling)
        label = " ".join(group["category"].split())
        if items:
            skills.append({"category": label if LABEL.fullmatch(label) else "Skills", "items": items})
    if not skills:
        skills = [{"category": "Skills", "items": [s["name"] for s in profile["skills"]["technical"]]}]

    chosen = {entry["ref"]: entry["bullets"] for entry in answer["experience"]}
    dropped["achievements"] = sum(len(bullets) for ref, bullets in chosen.items()
                                  if not _index(ref, "exp-", len(profile["experience"])))
    names = [s for s in dict.fromkeys([*asked, *spellings]) if _fold(s) not in terms["generic"]]
    experience = []
    for i, exp in enumerate(profile["experience"], 1):
        bullets, used = [], set()
        for bullet in chosen.get(f"exp-{i}", []):
            n = _index(bullet["ref"], f"exp-{i}-a", len(exp["achievements"]))
            if n is None or n in used:
                dropped["achievements"] += n is None
                continue
            used.add(n)
            source, text = exp["achievements"][n - 1], " ".join(bullet.get("text", "").split())
            if text and not _rewrite_ok(text, source, exp["technologies"], names, spellings, terms):
                dropped["rewrites"] += 1
                text = ""
            bullets.append(text or source)
        experience.append({"index": i - 1, "bullets": bullets or list(exp["achievements"])})

    picked = [_index(ref, "proj-", len(extras.get("projects", []))) for ref in answer["projects"]]
    dropped["projects"] = picked.count(None)
    projects = [n - 1 for n in dict.fromkeys(n for n in picked if n)]

    plan = {"summary": " ".join(answer["summary"].split()), "skills": skills,
            "experience": experience, "projects": projects,
            "cover_letter": [" ".join(p.split()) for p in answer["cover_letter"]]}
    return {"errors": [], "plan": plan, "dropped": dropped}


def save(store: dict, profile: dict, input_path: Path, answer_path: Path, cv_dir: Path,
         writer_model: str, now: datetime | None = None, reviewed: bool = False) -> dict:
    """Check the agent's answer for one posting; if it holds, render the documents and move
    the posting to cv_ready, in the input's folder. Returns paths and counts, never a value
    from the answer. `reviewed`: the answer is the cv-reviewer's revision of a saved one; it
    is checked the same way, and when it fails the saved documents stay as they are."""
    folder, cv_root = input_path.resolve().parent, cv_dir.resolve()
    if folder.parent.parent != cv_root:
        raise WriterError(f"{input_path} is not in a posting folder under {cv_dir}")
    given = json.loads(input_path.read_text(encoding="utf-8"))
    job_id = given.get("posting", {}).get("id")
    job = next(iter(ready(store, job_id)), None) if isinstance(job_id, str) else None
    if job is None:
        raise WriterError(f"{input_path} is not the input of an approved posting")
    if reviewed and job["status"] != "cv_ready":
        raise WriterError(f"{job_id} has no saved documents to review")
    if given.get("profile") != facts(profile):
        return {"ok": False, "id": job_id, "errors": ["profile: changed since cv batch; run it again"]}
    result = check(_read_answer(answer_path), job, profile)
    if result["errors"]:
        return _reject(folder, job_id, result["errors"], result["problems"], reviewed)
    now = now or datetime.now(UTC)
    try:
        files = render.write(profile, job, result["plan"], folder, now.date())
    except render.RenderError as exc:
        return _reject(folder, job_id, [f"render: {exc}"], [], reviewed)
    (folder / REJECTION).unlink(missing_ok=True)
    job["documents"] = {**{name: str(files[name].relative_to(cv_root.parent))
                           for name in ("cv", "cover_letter", "cover_letter_text", "job_description")},
                        "language": LANGUAGE, "model": writer_model, "created_at": now.isoformat(),
                        **({"reviewed": True} if reviewed else {})}
    job["status"] = "cv_ready"
    if errors := jobs.validate(job):
        raise WriterError(f"{job_id} does not fit the job schema: {errors[0]}")
    return {"ok": True, "id": job_id, "documents": job["documents"], "pages": files["pages"],
            "trimmed": files["trimmed"], "dropped": result["dropped"]}


def missing_skills(asked: list[str], profile: dict, terms: dict | None = None) -> list[str]:
    """The skills in `asked` the profile does not have (see _in_profile), in their order."""
    terms = terms or load_terms()
    known = _known(_profile_skills(profile), terms)
    told = "\n".join(_strings(facts(profile)))  # everything the agent was told about the candidate
    return [s for s in asked if not _in_profile(s, known, told, terms)]


def canonical_skill(name: str, terms: dict | None = None) -> str:
    """One name per skill: the first name of its alias group in config/cv_check.json, else the
    name with its spaces tidied."""
    terms = terms or load_terms()
    return terms["aliases"].get(_fold(name), (" ".join(name.split()),))[0]


def _profile_skills(profile: dict) -> list[str]:
    """The profile's own skill names: the skill list, then the technologies of every job and
    project (the first spelling of a name wins)."""
    extras = profile.get("extras") or {}
    return list(_canonical([*(s["name"] for s in profile["skills"]["technical"]),
                            *(t for exp in profile["experience"] for t in exp["technologies"]),
                            *(t for p in extras.get("projects", []) for t in p.get("technologies", []))]).values())


def _known(spellings: list[str], terms: dict) -> dict[str, str]:
    """Folded name or alias -> the profile's own spelling."""
    known = _canonical(spellings)
    for name in spellings:
        for variant in _variants(name, terms):
            known.setdefault(_fold(variant), name)
    return known


def load_terms(path: Path = CV_CHECK_JSON) -> dict:
    """config/cv_check.json: {"aliases": {folded name: every name of its group},
    "generic": {folded names}}."""
    return _load_terms(path)


@cache
def _load_terms(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    aliases: dict[str, tuple[str, ...]] = {}
    for group in data.get("aliases", []):
        for name in group:
            aliases[_fold(name)] = tuple(dict.fromkeys([*aliases.get(_fold(name), ()), *group]))
    return {"aliases": aliases, "generic": frozenset(_fold(n) for n in data.get("generic", []))}


def _reject(folder: Path, job_id: str, errors: list[str], problems: list[dict],
            reviewed: bool = False) -> dict:
    """Write rejection.json for the agent's second try; the result names it, with codes only.
    A review gets no second try: the documents saved before stay."""
    if reviewed:
        return {"ok": False, "id": job_id, "errors": errors, "kept": "the documents saved before the review"}
    path = folder / REJECTION
    path.write_text(json.dumps({"id": job_id, "errors": errors, "problems": problems},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": False, "id": job_id, "errors": errors, "rejection_path": str(path)}


def _schema_errors(answer: object) -> list[str]:
    """'<pointer>: <JSON Schema keyword>', e.g. '/cover_letter: minItems', '/summary: required'."""
    errors = []
    for error in Draft202012Validator(ANSWER_SCHEMA).iter_errors(answer):
        path = "/" + "/".join(map(str, error.absolute_path))
        if error.validator == "required" and isinstance(error.instance, dict):
            errors += [f"{path.rstrip('/')}/{name}: required" for name in error.validator_value
                       if name not in error.instance]
        else:
            errors.append(f"{path}: {error.validator}")
    return errors


def _read_answer(path: Path) -> object:
    """The agent's JSON answer; a ```json fence around it is fine."""
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise WriterError(f"{path} is not JSON: {exc.msg} at line {exc.lineno}") from None


def _text_problems(field: str, text: str, allowed: set[str], lacking: list[str],
                   skills: list[str]) -> list[dict]:
    """Each broken rule as {"field", "code"}, with "values" when the agent needs them to find
    it: the numbers as written, the skill names."""
    problems = []
    if HIDDEN.search(text) or INVISIBLE.search(text):
        problems.append({"field": field, "code": "hidden_characters"})
    if URL_PATTERN.search(text):
        problems.append({"field": field, "code": "link"})
    if EMAIL.search(text):
        problems.append({"field": field, "code": "email"})
    if numbers := [n for n in NUMBER.findall(_countable(text, skills)) if _number(n) not in allowed]:
        problems.append({"field": field, "code": "number_not_in_profile",
                         "values": list(dict.fromkeys(numbers))})
    if names := [name for name in lacking if _mentions(text, name)]:
        problems.append({"field": field, "code": "skill_not_in_profile",
                         "values": list(dict.fromkeys(names))})
    return problems


def _rewrite_ok(text: str, source: str, technologies: list[str], names: list[str],
                skills: list[str], terms: dict) -> bool:
    """A bullet may reword its achievement, but not add a number or a skill to it. A skill
    counts as there under any of its names."""
    if len(text) > BULLET_MAX or HIDDEN.search(text) or INVISIBLE.search(text):
        return False
    if URL_PATTERN.search(text) or EMAIL.search(text):
        return False
    if not _numbers([_countable(text, skills)]) <= _numbers([source]):
        return False
    used = {_fold(v) for t in technologies for v in _variants(t, terms)}
    return not any(_mentions(text, name)
                   and not any(_fold(v) in used or _mentions(source, v, ignore_case=True)
                               for v in _variants(name, terms))
                   for name in names)


def _in_profile(name: str, known: dict[str, str], told: str, terms: dict) -> bool:
    """A skill the posting asks for is in the profile when the profile lists it or names it
    anywhere (an achievement, the about text, a project), under any of its names; in the text
    case counts, so "the rest" is not REST. Generic skills (communication, leadership, ...)
    are never missing."""
    if _fold(name) in terms["generic"]:
        return True
    return any(_fold(v) in known or _mentions(told, v) for v in _variants(name, terms))


def _variants(name: str, terms: dict) -> list[str]:
    """The name and its aliases from config/cv_check.json."""
    return [name, *(v for v in terms["aliases"].get(_fold(name), ()) if _fold(v) != _fold(name))]


def _countable(text: str, skills: list[str]) -> str:
    """The text without numbers that are not claims: "24/7", and a version right after one
    of the profile's skills ("Python 3.12", "Java 17")."""
    text = NOT_A_FACT.sub(" ", text)
    for name in skills:
        text = re.sub(rf"(?<![\w+#.])({re.escape(name.strip())})[ /-]?v?\d+(?:\.\d+)*(?![\w%]|[.,]\d)",
                      r"\1", text)
    return text


def _mentions(text: str, name: str, ignore_case: bool = False) -> bool:
    """`name` as a whole word in `text` ("Go" in "Go services", not in "Google"). Case counts
    by default, so prose ("go further", "communication") does not match skill names. A name
    of one or two letters does not match before "&" or "-" ("R" in "R&D", "Go" in "Go-to-market")."""
    name = name.strip()
    after = r"(?![\w+#&-])" if len(name) <= 2 else r"(?![\w+#])"
    pattern = rf"(?<![\w+#.]){re.escape(name)}{after}"
    return re.search(pattern, text, re.IGNORECASE if ignore_case else 0) is not None


def _strings(value: object):
    """Every string in a nested value, `ref` values left out."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k != "ref":
                yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, str):
        yield value


def _numbers(values: list) -> set[str]:
    """Every number in the values (strings and numbers, nested), in canonical form; `ref`
    values are left out, so the numbering itself allows no number."""
    found: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            found |= _numbers([v for k, v in value.items() if k != "ref"])
        elif isinstance(value, list):
            found |= _numbers(value)
        elif isinstance(value, bool) or value is None:
            continue
        elif isinstance(value, int | float):
            found.add(_number(str(value)))
        else:
            found |= {_number(n) for n in NUMBER.findall(str(value))}
    return found


def _number(text: str) -> str:
    """'1,000' -> '1000', '3,5' -> '3.5', '4.0' -> '4', '03' -> '3'."""
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", text):
        text = text.replace(",", "")
    text = text.replace(",", ".")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.lstrip("0") or "0"


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def _canonical(names) -> dict[str, str]:
    """Folded name -> the profile's own spelling (the first one wins)."""
    out: dict[str, str] = {}
    for name in names:
        out.setdefault(_fold(name), name)
    return out


def _index(ref: object, prefix: str, count: int) -> int | None:
    """'exp-2' with prefix 'exp-' -> 2 when 1 <= 2 <= count, else None."""
    if isinstance(ref, str) and ref.startswith(prefix) and ref[len(prefix):].isdigit():
        n = int(ref[len(prefix):])
        return n if 1 <= n <= count else None
    return None
