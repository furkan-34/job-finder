"""Stage 3b: render the CV and the cover letter as PDF, with Typst, in the layout of Jake's Resume.

Next to them go the cover letter as plain text (to paste into a form) and the posting itself
as a PDF (its fields and description as stored, to read later what the job was), both by
code alone.

The layout is cv/template.typ; this module only builds the data it fills in. Typst runs from
the `typst` Python package (the compiler is in the wheel: no LaTeX or other install), and the
data goes in as JSON, so no text can change the layout.

Every fact comes from profile.json: names, contact details, titles, companies, places, dates,
education, certifications. The plan from writer.check adds what the agent chose for this
posting: the summary, the skills in groups, the bullets of each job (checked rewrites of the
profile's achievements) and the projects, and the cover letter's paragraphs.

Two checks run on the result, in code:

    fit    the CV takes at most two pages (one when it fits); while it is longer, the last
           bullet of the oldest job with more than two goes, then the last project, then down
           to one bullet per job
    ats    the text read back out of each PDF (pypdf, as an applicant tracking system reads
           it) must hold the name, the email, every heading, the summary and every bullet, and
           no year may touch a dash other than the ASCII "-": some ATS imports (Workday) split a
           date range only on "-" and drop a "2019 – 2023" entirely

Date ranges are written "Mar 2021 - Present"; an education whose end year is still ahead
says "(expected)", so it does not read as a finished degree.
"""

import io
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import typst
from pypdf import PdfReader

TEMPLATE = Path(__file__).with_name("template.typ")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
LETTER_COUNTRIES = {"US", "USA", "CA", "CAN", "United States", "Canada"}  # Letter paper; A4 elsewhere
MAX_PAGES = 2
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # control characters but tab and newline
# A year next to a Unicode dash (hyphen, en dash, em dash, ...): an ATS may not see the range.
DATE_DASH = re.compile(r"\b\d{4}\s*[\u2010-\u2015\u2212]|[\u2010-\u2015\u2212]\s*\d{4}\b")


class RenderError(Exception):
    pass


def write(profile: dict, job: dict, plan: dict, folder: Path, today: date) -> dict:
    """Write <Name>-CV.pdf, <Name>-Cover-Letter.pdf, <Name>-Cover-Letter.txt and
    Job-Description.pdf into the posting's folder."""
    stem = file_stem(profile["identity"]["full_name"])
    cv = cv_data(profile, plan, today)
    pdf, pages, trimmed = fit(cv, MAX_PAGES)
    letter = letter_data(profile, job, plan, today)
    letter_pdf = compile_pdf(letter)
    problems = ats_check(pdf, _expected_cv(cv)) + ats_check(letter_pdf, _expected_letter(letter))
    if date_dashes(pdf):
        problems.append("ASCII date ranges")
    if problems:
        raise RenderError(f"text read back from the PDF is missing: {', '.join(problems)}")
    posting_pdf = compile_pdf(job_data(job, cv["paper"]))
    folder.mkdir(parents=True, exist_ok=True)
    paths = {"cv": folder / f"{stem}-CV.pdf", "cover_letter": folder / f"{stem}-Cover-Letter.pdf",
             "cover_letter_text": folder / f"{stem}-Cover-Letter.txt",
             "job_description": folder / "Job-Description.pdf"}
    paths["cv"].write_bytes(pdf)
    paths["cover_letter"].write_bytes(letter_pdf)
    paths["cover_letter_text"].write_text(letter_text(letter), encoding="utf-8")
    paths["job_description"].write_bytes(posting_pdf)
    return {**paths, "pages": pages, "trimmed": trimmed}


def cv_data(profile: dict, plan: dict, today: date | None = None) -> dict:
    ident, extras = profile["identity"], profile.get("extras") or {}
    jobs = sorted(plan["experience"], key=lambda e: profile["experience"][e["index"]]["start"],
                  reverse=True)
    return {
        "kind": "cv", "title": f"{ident['full_name']} - CV", **_header(profile),
        "summary": plan["summary"],
        "experience": [{"title": exp["title"], "company": exp["company"], "location": exp["location"],
                        "dates": f"{_month(exp['start'])} - {_month(exp['end'])}",
                        "bullets": list(entry["bullets"])}
                       for entry in jobs for exp in [profile["experience"][entry["index"]]]],
        "projects": [{"name": p["name"], "tech": ", ".join(p.get("technologies", [])),
                      "description": p["description"]}
                     for p in (extras["projects"][n] for n in plan["projects"])],
        "skills": plan["skills"],
        "education": [{"school": e["school"], "dates": _years(e, today or date.today()),
                       "degree": f"{e['degree']} in {e['field']}",
                       "detail": f"GPA {e['gpa']}" if e.get("gpa") else ""}
                      for e in profile["education"]],
        "education_first": profile["summary"]["total_years_experience"] < 2,
        "certifications": [", ".join(str(x) for x in (c["name"], c.get("issuer"), c.get("year")) if x)
                           for c in extras.get("certifications", [])],
        "publications": [", ".join(str(x) for x in (p["title"], p.get("venue"), p.get("year")) if x)
                         for p in extras.get("publications", [])],
        "languages": ", ".join(f"{lang['language']} ({lang['level'].capitalize()})"
                               for lang in profile["skills"]["languages"]),
    }


def letter_data(profile: dict, job: dict, plan: dict, today: date) -> dict:
    name = profile["identity"]["full_name"]
    return {"kind": "letter", "title": f"{name} - Cover letter", **_header(profile),
            "date": f"{today.day} {MONTHS[today.month - 1]} {today.year}",
            "recipient": _line(job.get("company_name")),
            "subject": f"Application for {_line(job['title'])}",
            "greeting": "Dear Hiring Manager,", "paragraphs": plan["cover_letter"],
            "closing": "Sincerely,"}


def letter_text(letter: dict) -> str:
    """The cover letter as plain text: greeting, paragraphs, closing and name."""
    return "\n\n".join([letter["greeting"], *letter["paragraphs"],
                         f"{letter['closing']}\n{letter['name']}"]) + "\n"


def job_data(job: dict, paper: str) -> dict:
    """The posting as stored (untrusted text, placed as text only): its fields, the warnings
    of the safety check, and the description, one paragraph per line."""
    work_modes = ", ".join(m.capitalize() for m in job.get("work_modes") or [])
    rows = [("Location", job.get("location")), ("Work mode", work_modes),
            ("Employment type", job.get("employment_type")), ("Seniority", job.get("seniority")),
            ("Salary", job.get("salary")), ("Posted", job.get("posted_at")),
            ("Company website", job.get("company_website")), ("Posting", job.get("url")),
            ("Apply", job.get("apply_url")), ("Job id", job["id"])]
    safety = job.get("safety") or {}
    description = CONTROL.sub("", job.get("description") or "")
    return {"kind": "job", "paper": paper, "title": _line(job["title"]),
            "company": _line(job.get("company_name")),
            "details": [{"label": label, "value": _line(str(value), 2000)} for label, value in rows if value],
            "warnings": safety.get("reasons", []) if safety.get("verdict") == "warn" else [],
            "paragraphs": [line.strip() for line in description.splitlines() if line.strip()]}


def compile_pdf(data: dict) -> bytes:
    return typst.compile(str(TEMPLATE), sys_inputs={"data": json.dumps(data, ensure_ascii=False)})


def fit(data: dict, max_pages: int) -> tuple[bytes, int, int]:
    """Compile, trimming the least relevant content until the CV fits. Returns the PDF, its
    pages and how many items were trimmed (the agent orders bullets most relevant first)."""
    trimmed = 0
    while True:
        pdf = compile_pdf(data)
        pages = len(PdfReader(io.BytesIO(pdf)).pages)
        if pages <= max_pages or not _trim(data):
            return pdf, pages, trimmed
        trimmed += 1


def _trim(data: dict) -> bool:
    """Drop one item: a bullet of the oldest job with more than two, else the last project,
    else a bullet of the oldest job with more than one. False when nothing is left to drop."""
    oldest_first = data["experience"][::-1]
    if job := next((j for j in oldest_first if len(j["bullets"]) > 2), None):
        job["bullets"].pop()
    elif data["projects"]:
        data["projects"].pop()
    elif job := next((j for j in oldest_first if len(j["bullets"]) > 1), None):
        job["bullets"].pop()
    else:
        return False
    return True


def ats_check(pdf: bytes, expected: dict[str, str]) -> list[str]:
    """The names of the expected texts not found in the text read back from the PDF
    (compared without whitespace, so line breaks do not matter)."""
    text = _squash("".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages))
    return [name for name, value in expected.items() if value and _squash(value) not in text]


def _expected_cv(cv: dict) -> dict[str, str]:
    expected = {"name": cv["name"], "email": cv["email"], "summary": cv["summary"]}
    headings = ["Experience", "Education", "Technical Skills"] + (["Projects"] if cv["projects"] else [])
    expected |= {f"heading {h}": h for h in headings}
    expected |= {f"experience {i} bullet {j}": bullet for i, job in enumerate(cv["experience"], 1)
                 for j, bullet in enumerate(job["bullets"], 1)}
    return expected


def _expected_letter(letter: dict) -> dict[str, str]:
    return {"name": letter["name"], "email": letter["email"],
            **{f"paragraph {n}": p for n, p in enumerate(letter["paragraphs"], 1)}}


def date_dashes(pdf: bytes) -> list[str]:
    """Each place in the text read back from the PDF where a year touches a non-ASCII dash."""
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    return [match.group() for match in DATE_DASH.finditer(text)]


def _squash(text: str) -> str:
    return "".join(unicodedata.normalize("NFC", text).split())


def _header(profile: dict) -> dict:
    ident, place = profile["identity"], profile["identity"]["location"]
    contact: list = [f"{place['city']}, {place['country']}"]
    if ident.get("phone"):
        contact.append(ident["phone"])
    contact.append({"text": ident["email"], "url": f"mailto:{ident['email']}"})
    for key in ("linkedin", "github", "portfolio"):
        if url := ident.get(key):
            shown = re.sub(r"^(https?://)?(www\.)?", "", url).rstrip("/")
            contact.append({"text": shown, "url": url if "://" in url else f"https://{url}"})
    paper = "us-letter" if place["country"] in LETTER_COUNTRIES else "a4"
    return {"name": ident["full_name"], "email": ident["email"], "contact": contact, "paper": paper}


def _month(value: str | None) -> str:
    """'2023-03' -> 'Mar 2023'; no end -> 'Present'."""
    if not value:
        return "Present"
    year, month = value.split("-")
    return f"{MONTHS[int(month) - 1][:3]} {year}"


def _years(education: dict, today: date) -> str:
    """'2015 - 2020'; no end year -> 'Present'; an end year still ahead -> '(expected)'."""
    end = education.get("end_year")
    if not end:
        return f"{education['start_year']} - Present"
    return f"{education['start_year']} - {end}" + (" (expected)" if end > today.year else "")


def _line(text: str | None, limit: int = 150) -> str:
    """Posting text on one line, at most `limit` characters (a title, a company name, a link)."""
    return " ".join(CONTROL.sub("", text or "").split())[:limit]


def file_stem(full_name: str) -> str:
    """'Ayşe Yılmaz' -> 'Ayse-Yilmaz': ASCII letters and digits, words joined by '-'."""
    return "-".join(ascii_words(full_name)) or "Candidate"


def ascii_words(text: str) -> list[str]:
    """'Ayşe Yılmaz' -> ['Ayse', 'Yilmaz']: the text's letters and digits as ASCII, by word."""
    text = unicodedata.normalize("NFKD", text.replace("ı", "i").replace("İ", "I"))
    return re.findall(r"[A-Za-z0-9]+", "".join(c for c in text if not unicodedata.combining(c)))
