import io
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pypdf import PdfReader

from jobfinder import cli, jobs
from jobfinder.cv import render, settings, writer

NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
DAY = "23-10-2026"  # the folder of NOW.date()
SAFE = {"verdict": "pass", "reasons": [], "model_score": 0.01, "checked_at": NOW.isoformat()}
EXTRACTED = {"industry": "Fintech", "seniority": "senior", "min_years_experience": 5,
             "required_skills": ["Python", "Go", "Kubernetes"], "preferred_skills": ["Kafka"],
             "required_languages": ["English"], "salary": None, "visa_sponsorship": None,
             "model": "haiku", "extracted_at": NOW.isoformat()}
JOB = {"id": "linkedin:1", "data_source": "APIFY_LINKEDIN_SCRAPER", "source_id": "1",
       "url": "https://www.linkedin.com/jobs/view/1", "title": "Senior Backend Engineer",
       "company_name": "Acme Software GmbH", "location": "Istanbul, Turkey",
       "work_modes": ["hybrid"], "description": "We build payment services in Python and Go.",
       "status": "approved", "created_at": NOW.isoformat(), "safety": SAFE, "extracted": EXTRACTED}
REWRITE = "Led the zero-downtime migration of 12 services to Kubernetes."
ANSWER = {"id": "linkedin:1",
          "summary": "Backend engineer with 6 years of Python; cut p95 latency from 420 ms to 140 ms.",
          "skills": [{"category": "Languages", "items": ["python"]},
                     {"category": "Data", "items": ["PostgreSQL", "Kafka"]}],
          "experience": [{"ref": "exp-1", "bullets": [{"ref": "exp-1-a2", "text": REWRITE},
                                                      {"ref": "exp-1-a1"}]},
                         {"ref": "exp-2", "bullets": [{"ref": "exp-2-a1"}]}],
          "projects": [],
          "cover_letter": ["I am applying for the Senior Backend Engineer role at Acme.",
                           "At Example Payments I led the migration of 12 services to Kubernetes.",
                           "I would welcome a conversation."]}


def check(profile, **changes):
    return writer.check({**ANSWER, **changes}, JOB, profile)


def squashed(pdf: bytes | Path) -> str:
    data = pdf.read_bytes() if isinstance(pdf, Path) else pdf
    return render._squash("".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages))


def test_approve_only_safe_undecided_postings():
    store = {"jobs": [{**JOB, "id": "a", "status": "new"}, {**JOB, "id": "b", "status": "shortlist"},
                      {**JOB, "id": "c", "status": "new", "safety": {**SAFE, "verdict": "block"}},
                      {**{k: v for k, v in JOB.items() if k != "safety"}, "id": "d", "status": "new"},
                      {**JOB, "id": "e", "status": "applied"}]}
    result = jobs.approve(store, ["a", "b", "c", "d", "e", "x", "a"])
    assert result["approved"] == ["a", "b"]
    assert result["skipped"] == [{"id": "c", "reason": "blocked"}, {"id": "d", "reason": "not_checked"},
                                 {"id": "e", "reason": "status:applied"}, {"id": "x", "reason": "unknown"}]
    assert [job["status"] for job in store["jobs"]] == ["approved", "approved", "new", "new", "applied"]


def test_input_has_no_contact_details_and_numbers_every_item(tmp_path, example_profile):
    store = {"jobs": [JOB, {**JOB, "id": "linkedin:2", "status": "new"},
                      {**JOB, "id": "linkedin:3", "safety": {**SAFE, "verdict": "block"}}]}
    made = writer.make(store, example_profile, tmp_path / "cv", today=NOW.date())
    assert [entry["id"] for entry in made["jobs"]] == ["linkedin:1"]
    path = Path(made["jobs"][0]["input_path"])
    assert path == tmp_path / "cv" / DAY / "acme-software-gmbh-1" / "input.json"
    text = path.read_text(encoding="utf-8")
    for contact in ("Ayşe", "ayse.yilmaz@example.com", "+90 555", "linkedin.com/in", "github.com"):
        assert contact not in text
    given = json.loads(text)
    assert given["posting"]["description"] == JOB["description"]
    assert given["posting"]["extracted"]["required_skills"] == ["Python", "Go", "Kubernetes"]
    assert "safety" not in given["posting"] and "url" not in given["posting"]
    first = given["profile"]["experience"][0]
    assert first["ref"] == "exp-1" and first["achievements"][1]["ref"] == "exp-1-a2"


def test_folder_is_company_and_source_id():
    assert writer.key(JOB) == "acme-software-gmbh-1"
    assert writer.key({**JOB, "company_name": "Şişecam A.Ş. / İstanbul", "source_id": "77"}) == "sisecam-a-s-istanbul-77"
    assert writer.key({**JOB, "company_name": "../../etc " + "x" * 80, "source_id": "a/b"}) == "etc-" + "x" * 56 + "-a-b"
    assert writer.key({**JOB, "company_name": None}) == "1"


def test_make_one_posting_again_and_unknown_one(tmp_path, example_profile):
    store = {"jobs": [{**JOB, "status": "cv_ready"}]}
    assert writer.make(store, example_profile, tmp_path)["count"] == 0  # only approved ones
    old = tmp_path / DAY / "acme-software-gmbh-1" / "answer.json"
    old.parent.mkdir(parents=True)
    old.write_text("{}")
    assert writer.make(store, example_profile, tmp_path, "linkedin:1", NOW.date())["count"] == 1
    assert not old.exists()  # an old answer never meets a new input
    with pytest.raises(writer.WriterError):
        writer.make(store, example_profile, tmp_path, "linkedin:9")


def test_check_skill_groups_keep_only_profile_skills(example_profile):
    result = check(example_profile, skills=[
        {"category": "Languages", "items": ["python", "Go", "Python"]},
        {"category": "Cloud  & DevOps", "items": ["Kubernetes", "AWS", "python"]},
        {"category": "Top 3!", "items": ["Django"]},
        {"category": "Other", "items": ["Rust"]}])
    assert result["plan"]["skills"] == [{"category": "Languages", "items": ["Python"]},
                                        {"category": "Cloud & DevOps", "items": ["Kubernetes", "AWS"]},
                                        {"category": "Skills", "items": ["Django"]}]
    assert result["dropped"]["skills"] == 2  # Go and Rust; repeats are not counted
    fallback = check(example_profile, skills=[{"category": "Other", "items": ["Rust"]}])["plan"]["skills"]
    assert fallback == [{"category": "Skills", "items": ["Python", "PostgreSQL", "Kubernetes"]}]


def test_check_picks_bullets_by_reference(example_profile):
    a1, a2 = example_profile["experience"][0]["achievements"]
    tailored = "Reduced payment API p95 latency from 420 ms to 140 ms by redesigning the PostgreSQL caching layer."
    result = check(example_profile, experience=[
        {"ref": "exp-1", "bullets": [{"ref": "exp-1-a1", "text": tailored.replace(" by ", "   by ")},
                                     {"ref": "exp-1-a2", "text": "Led migration of 15 services to Kubernetes."},
                                     {"ref": "exp-1-a1", "text": "Again."},
                                     {"ref": "exp-1-a9", "text": "Invented."}]},
        {"ref": "exp-7", "bullets": [{"ref": "exp-7-a1"}]}],
        projects=["proj-1"])
    # Whitespace squashed; a new number falls back to the achievement; the second job was
    # not listed, so all of its achievements, word for word.
    assert result["plan"]["experience"] == [
        {"index": 0, "bullets": [tailored, a2]},
        {"index": 1, "bullets": example_profile["experience"][1]["achievements"]}]
    assert result["plan"]["projects"] == []
    assert result["dropped"] == {"skills": 0, "achievements": 2, "rewrites": 1, "projects": 1}


@pytest.mark.parametrize("text, kept", [
    ("Built the order-tracking service in Django and Redis, handling 2M events/day.", True),  # the job's tools
    ("Built the order-tracking service, letting the team go faster at 2M events/day.", True),  # prose "go"
    ("Built an order-tracking service in Go handling 2M events per day.", False),  # asked for, not in the job
    ("Built the order-tracking service on Kafka, handling 2M events/day.", False),  # another job's tool
    ("Built the order-tracking service handling 3M events/day.", False),
    ("Built the order-tracking service handling 2M events/day for 40 teams.", False),
    ("Built the order-tracking service (2M events/day), see https://example.com.", False),
    ("Built the order-tracking service handling 2M events/day‮.", False),
    ("Built the order-tracking service handling 2M events/day " + "x" * 200, False),
])
def test_check_rewrites_keep_the_facts_of_their_achievement(example_profile, text, kept):
    (source,) = example_profile["experience"][1]["achievements"]
    plan = check(example_profile, experience=[{"ref": "exp-2", "bullets": [{"ref": "exp-2-a1", "text": text}]}])["plan"]
    assert plan["experience"][1]["bullets"] == [text if kept else source]


@pytest.mark.parametrize("changes, error", [
    ({"summary": "Backend engineer with 9 years of Python."}, "summary: number_not_in_profile"),
    ({"summary": "Grew revenue by 35%."}, "summary: number_not_in_profile"),
    ({"summary": "See https://example.com for more."}, "summary: link"),
    ({"summary": "Write to me at someone@example.org."}, "summary: email"),
    ({"summary": "Backend engineer‮ reversed."}, "summary: hidden_characters"),
    ({"summary": "Backend engineer working in Go and Python."}, "summary: skill_not_in_profile"),
    ({"cover_letter": ["Fine.", "I write Go every day."]}, "cover_letter/1: skill_not_in_profile"),
    ({"cover_letter": ["Fine.", "Call me on 0555 123 45 67."]}, "cover_letter/1: number_not_in_profile"),
    ({"cover_letter": ["x" * 1000] * 4}, "cover_letter: too_long"),
    ({"id": "linkedin:2"}, "id: mismatch"),
    ({"cover_letter": ["Only one paragraph."]}, "/cover_letter: minItems"),
    ({"skills": "Python"}, "/skills: type"),
    ({"skills": [{"category": "x" * 31, "items": []}]}, "/skills/0/category: maxLength"),
    ({"experience": [{"ref": "exp-1"}]}, "/experience/0/bullets: required"),
    ({"experience": [{"ref": "exp-1", "bullets": [{"text": "x"}]}]}, "/experience/0/bullets/0/ref: required"),
    ({"summary": None}, "/summary: type"),
])
def test_check_rejects_broken_texts(example_profile, changes, error):
    assert error in check(example_profile, **changes)["errors"]


def test_check_missing_field(example_profile):
    answer = {k: v for k, v in ANSWER.items() if k != "summary"}
    assert writer.check(answer, JOB, example_profile)["errors"] == ["/summary: required"]
    assert writer.check([], JOB, example_profile)["errors"] == ["/: type"]


def test_check_numbers_and_skills_from_the_profile_are_fine(example_profile):
    fine = "6 years; 12 services; 2M events/day; GPA 3.3/4.0; since 2023; 140 ms; S3, p95 and K8s."
    assert check(example_profile, summary=fine)["errors"] == []
    assert check(example_profile, summary="Python and Kafka; keen to go further.")["errors"] == []
    assert check(example_profile, summary="1,000 services.")["errors"] == ["summary: number_not_in_profile"]
    # The job's own title and company may carry numbers.
    assert check(example_profile, summary="Ready for level 88.")["errors"] != []
    assert writer.check({**ANSWER, "summary": "Ready for level 88."}, {**JOB, "title": "Engineer 88"},
                        example_profile)["errors"] == []


@pytest.mark.parametrize("text", [
    "I run Postgres and K8s in production.",                 # aliases of PostgreSQL, Kubernetes
    "Communication with product teams is daily work.",       # generic skill
    "Leadership of a small team.",
    "I enjoy working closely with R&D.",                     # not the skill R
    "Go-to-market speed matters.",                           # not the skill Go
    "I was on the 24/7 on-call rotation.",                   # not a fact
    "I work daily with Python 3.12 and PostgreSQL 16.",      # versions of profile skills
    "I passed the AWS Certified Developer exam.",            # in the profile's certifications
])
def test_check_does_not_reject_true_texts(example_profile, text):
    job = {**JOB, "extracted": {**EXTRACTED, "required_skills": [
        "Postgres", "K8s", "Communication", "Leadership", "R", "Go", "Amazon Web Services"]}}
    assert writer.check({**ANSWER, "cover_letter": ["Fine.", text]}, job, example_profile)["errors"] == []


def test_check_skill_named_only_in_an_achievement_is_in_the_profile(example_profile):
    example_profile["experience"][1]["achievements"][0] += " Packaged it with Docker."
    job = {**JOB, "extracted": {**EXTRACTED, "preferred_skills": ["Docker"]}}
    answer = {**ANSWER, "summary": "Backend engineer who ships Docker images."}
    assert writer.check(answer, job, example_profile)["errors"] == []


def test_check_problems_name_the_values(example_profile):
    job = {**JOB, "extracted": {**EXTRACTED, "required_skills": ["Go", "Rust"]}}
    result = writer.check({**ANSWER, "cover_letter": ["Fine.", "I write Go and Rust; a team of 8."]},
                          job, example_profile)
    assert result["errors"] == ["cover_letter/1: number_not_in_profile", "cover_letter/1: skill_not_in_profile"]
    assert result["problems"] == [
        {"field": "cover_letter/1", "code": "number_not_in_profile", "values": ["8"]},
        {"field": "cover_letter/1", "code": "skill_not_in_profile", "values": ["Go", "Rust"]}]


def test_check_aliases_keep_the_profile_spelling(example_profile):
    plan = check(example_profile, skills=[{"category": "Data", "items": ["Postgres", "postgresql", "k8s"]}])["plan"]
    assert plan["skills"] == [{"category": "Data", "items": ["PostgreSQL", "Kubernetes"]}]
    text = "Cut p95 latency of the payment API from 420 ms to 140 ms by redesigning the Postgres cache."
    plan = check(example_profile, experience=[{"ref": "exp-1", "bullets": [{"ref": "exp-1-a1", "text": text}]}])["plan"]
    assert plan["experience"][0]["bullets"] == [text]


def test_save_renders_pdfs_and_moves_to_cv_ready(tmp_path, example_profile):
    store = {"jobs": [dict(JOB)]}
    cv_dir = tmp_path / "cv"
    made = writer.make(store, example_profile, cv_dir, today=NOW.date())["jobs"][0]
    Path(made["answer_path"]).write_text("```json\n" + json.dumps(ANSWER) + "\n```\n", encoding="utf-8")
    result = writer.save(store, example_profile, Path(made["input_path"]), Path(made["answer_path"]),
                         cv_dir, "opus", NOW)
    job = store["jobs"][0]
    assert result["ok"] and job["status"] == "cv_ready"
    folder = f"cv/{DAY}/acme-software-gmbh-1"
    assert job["documents"] == {"cv": f"{folder}/Ayse-Yilmaz-CV.pdf",
                                "cover_letter": f"{folder}/Ayse-Yilmaz-Cover-Letter.pdf",
                                "cover_letter_text": f"{folder}/Ayse-Yilmaz-Cover-Letter.txt",
                                "job_description": f"{folder}/Job-Description.pdf",
                                "language": "en", "model": "opus", "created_at": NOW.isoformat()}
    assert jobs.validate(job) == []
    assert (result["pages"], result["trimmed"]) == (1, 0)
    assert result["dropped"] == {"skills": 0, "achievements": 0, "rewrites": 0, "projects": 0}

    cv_path = tmp_path / job["documents"]["cv"]
    reader = PdfReader(cv_path)
    assert len(reader.pages) == 1 and reader.metadata.author == "Ayşe Yılmaz"
    text = squashed(cv_path)
    sections = ["AyşeYılmaz", "Summary", "Experience", "TechnicalSkills", "Education", "Certifications",
                "LanguagesTurkish"]
    assert [text.index(s) for s in sections] == sorted(text.index(s) for s in sections)
    a1 = example_profile["experience"][0]["achievements"][0]
    for expected in ("ayse.yilmaz@example.com", "linkedin.com/in/example", "SeniorBackendEngineer",
                     "ExamplePayments", "Mar2023-Present", REWRITE, a1, "Languages:Python",
                     "Data:PostgreSQL,Kafka", "BScinComputerEngineering", "GPA3.3/4.0"):
        assert render._squash(expected) in text
    assert text.index(render._squash(REWRITE)) < text.index(render._squash(a1))  # the chosen order

    letter = squashed(tmp_path / job["documents"]["cover_letter"])
    for expected in ("23 October 2026", "Acme Software GmbH", "Application for Senior Backend Engineer",
                     "Dear Hiring Manager,", *ANSWER["cover_letter"], "Sincerely,", "Ayşe Yılmaz"):
        assert render._squash(expected) in letter
    assert (tmp_path / job["documents"]["cover_letter_text"]).read_text(encoding="utf-8") == "\n\n".join(
        ["Dear Hiring Manager,", *ANSWER["cover_letter"], "Sincerely,\nAyşe Yılmaz"]) + "\n"

    posting = squashed(tmp_path / job["documents"]["job_description"])
    for expected in ("Senior Backend Engineer", "Acme Software GmbH", "Location: Istanbul, Turkey",
                     "Work mode: Hybrid", "Posting: https://www.linkedin.com/jobs/view/1", "Description",
                     JOB["description"]):
        assert render._squash(expected) in posting
    assert "Safetywarnings" not in posting  # verdict pass


def test_review_is_checked_and_rendered_again_or_the_saved_documents_stay(tmp_path, example_profile):
    store = {"jobs": [dict(JOB)]}
    made = writer.make(store, example_profile, tmp_path, today=NOW.date())["jobs"][0]
    inp, ans = Path(made["input_path"]), Path(made["answer_path"])
    review = inp.parent / writer.REVIEW
    review.write_text(json.dumps(ANSWER))
    with pytest.raises(writer.WriterError):  # nothing saved yet to review
        writer.save(store, example_profile, inp, review, tmp_path, "sonnet", NOW, reviewed=True)
    ans.write_text(json.dumps(ANSWER))
    assert writer.save(store, example_profile, inp, ans, tmp_path, "sonnet", NOW)["ok"]
    job = store["jobs"][0]
    cv_path = tmp_path.parent / job["documents"]["cv"]
    before = cv_path.read_bytes()

    review.write_text(json.dumps({**ANSWER, "summary": "Backend engineer, 99% uptime."}))
    result = writer.save(store, example_profile, inp, review, tmp_path, "sonnet", NOW, reviewed=True)
    assert result == {"ok": False, "id": "linkedin:1", "errors": ["summary: number_not_in_profile"],
                      "kept": "the documents saved before the review"}
    assert not (inp.parent / "rejection.json").exists()  # no second try for a review
    assert cv_path.read_bytes() == before and "reviewed" not in job["documents"]

    better = "Backend engineer who builds payment services in Python."
    review.write_text(json.dumps({**ANSWER, "summary": better}))
    result = writer.save(store, example_profile, inp, review, tmp_path, "sonnet", NOW, reviewed=True)
    assert result["ok"] and job["documents"]["reviewed"] is True and job["status"] == "cv_ready"
    assert jobs.validate(job) == []
    assert render._squash(better) in squashed(cv_path)
    writer.make(store, example_profile, tmp_path, "linkedin:1", today=NOW.date())
    assert not review.exists()  # a new batch starts without the old review


def test_save_rejects_and_leaves_the_posting_approved(tmp_path, example_profile):
    store = {"jobs": [dict(JOB)]}
    made = writer.make(store, example_profile, tmp_path)["jobs"][0]
    inp, ans = Path(made["input_path"]), Path(made["answer_path"])
    with pytest.raises(writer.WriterError):  # an input outside data/cv/<day>/<posting>/
        writer.save(store, example_profile, inp, ans, tmp_path / "other", "opus")
    ans.write_text(json.dumps({**ANSWER, "summary": "Ten years at 3 FAANG companies, 99% uptime."}))
    result = writer.save(store, example_profile, inp, ans, tmp_path, "opus")
    rejection = inp.parent / "rejection.json"
    assert result == {"ok": False, "id": "linkedin:1", "errors": ["summary: number_not_in_profile"],
                      "rejection_path": str(rejection)}
    # The values go only to the file the agent reads on its second try.
    assert json.loads(rejection.read_text())["problems"] == [
        {"field": "summary", "code": "number_not_in_profile", "values": ["99"]}]
    assert store["jobs"][0]["status"] == "approved" and "documents" not in store["jobs"][0]
    ans.write_text("not json")
    with pytest.raises(writer.WriterError):
        writer.save(store, example_profile, inp, ans, tmp_path, "opus")
    example_profile["experience"][0]["achievements"].append("Something new.")
    ans.write_text(json.dumps(ANSWER))
    assert writer.save(store, example_profile, inp, ans, tmp_path, "opus")["errors"] == [
        "profile: changed since cv batch; run it again"]
    store["jobs"][0]["status"] = "applied"
    with pytest.raises(writer.WriterError):
        writer.save(store, example_profile, inp, ans, tmp_path, "opus")


def test_save_reports_a_pdf_that_fails_the_ats_check(tmp_path, example_profile, monkeypatch):
    monkeypatch.setattr(render, "ats_check", lambda pdf, expected: ["summary"] if "summary" in expected else [])
    store = {"jobs": [dict(JOB)]}
    made = writer.make(store, example_profile, tmp_path, today=NOW.date())["jobs"][0]
    Path(made["answer_path"]).write_text(json.dumps(ANSWER))
    result = writer.save(store, example_profile, Path(made["input_path"]), Path(made["answer_path"]),
                         tmp_path, "opus")
    assert result["errors"] == ["render: text read back from the PDF is missing: summary"]
    assert store["jobs"][0]["status"] == "approved"
    assert not list((tmp_path / DAY / "acme-software-gmbh-1").glob("*.pdf"))


def test_text_is_never_typst_markup(example_profile):
    plan = check(example_profile)["plan"]
    plan["summary"] = '#set page(paper: "a5") = Heading *bold* _it_ $x^2$ <label> @ref // not a comment'
    page = PdfReader(io.BytesIO(render.compile_pdf(render.cv_data(example_profile, plan)))).pages[0]
    assert render._squash(plan["summary"]) in render._squash(page.extract_text())
    assert float(page.mediabox.width) == pytest.approx(595.3, abs=0.5)  # still A4


def test_posting_pdf_is_text_only(example_profile):
    job = {**JOB, "title": '#set page(paper: "a5") Engineer', "apply_url": "https://x.example/" + "a" * 300,
           "safety": {**SAFE, "verdict": "warn", "reasons": ["apply_domain_unknown:x.example"]},
           "description": "About us\n\n*not bold* = no heading\x07\n• $x$ <label> @ref // kept"}
    page = PdfReader(io.BytesIO(render.compile_pdf(render.job_data(job, "a4")))).pages[0]
    text = render._squash(page.extract_text())
    for expected in (job["title"], "Safety warnings", "apply_domain_unknown:x.example", "About us",
                     "*not bold* = no heading", "• $x$ <label> @ref // kept", "a" * 300):
        assert render._squash(expected) in text
    assert float(page.mediabox.width) == pytest.approx(595.3, abs=0.5)  # still A4


def test_cv_may_take_two_pages(tmp_path, example_profile):
    bullet = ("Designed, built and ran a service used by every product team, with tests, dashboards, "
              "alerts and runbooks for it, and taught the other teams how to run their own services on it.")
    plan = {**check(example_profile)["plan"],
            "experience": [{"index": 0, "bullets": [bullet] * 16}, {"index": 1, "bullets": [bullet] * 8}]}
    files = render.write(example_profile, JOB, plan, tmp_path, NOW.date())
    assert (files["pages"], files["trimmed"]) == (2, 0)


def test_fit_trims_the_oldest_job_first(example_profile):
    bullet = ("Designed, built and ran a service used by every product team, with tests, dashboards, "
              "alerts and runbooks for it, and taught the other teams how to run their own services on it.")
    plan = {**check(example_profile)["plan"],
            "experience": [{"index": 0, "bullets": [bullet] * 20}, {"index": 1, "bullets": [bullet] * 20}]}
    data = render.cv_data(example_profile, plan)
    pdf, pages, trimmed = render.fit(data, 1)
    kept = [len(job["bullets"]) for job in data["experience"]]  # newest first
    assert pages == len(PdfReader(io.BytesIO(pdf)).pages) == 1
    assert kept[1] == 2 and kept[0] > 2 and trimmed == 40 - sum(kept)


def test_trim_order():
    data = {"experience": [{"bullets": ["a", "b", "c"]}, {"bullets": ["d", "e", "f"]}], "projects": ["p"]}
    steps = []
    while render._trim(data):
        steps.append(([len(job["bullets"]) for job in data["experience"]], len(data["projects"])))
    assert steps == [([3, 2], 1), ([2, 2], 1), ([2, 2], 0), ([2, 1], 0), ([1, 1], 0)]


def test_ats_check_names_what_the_pdf_lacks(example_profile):
    pdf = render.compile_pdf(render.cv_data(example_profile, check(example_profile)["plan"]))
    assert render.ats_check(pdf, {"name": "Ayşe Yılmaz", "bullet": REWRITE}) == []
    assert render.ats_check(pdf, {"name": "Ayşe Yılmaz", "other": "Not on the page"}) == ["other"]


def test_education_first_for_new_graduates(example_profile):
    example_profile["summary"]["total_years_experience"] = 1
    plan = check(example_profile, summary="Backend engineer.")["plan"]
    text = squashed(render.compile_pdf(render.cv_data(example_profile, plan)))
    assert text.index("Education") < text.index("Experience")


def test_projects_when_picked(tmp_path, example_profile):
    example_profile["extras"]["projects"] = [{"name": "Ledger", "description": "An open-source ledger.",
                                              "technologies": ["Rust"]}]
    plan = check(example_profile, projects=["proj-1"],
                 skills=[{"category": "Languages", "items": ["Rust"]}])["plan"]
    assert plan["skills"] == [{"category": "Languages", "items": ["Rust"]}] and plan["projects"] == [0]
    files = render.write(example_profile, JOB, plan, tmp_path, date(2026, 10, 23))
    text = squashed(files["cv"])
    assert "Projects" in text and "Ledger|Rust" in text and "Anopen-sourceledger." in text


def test_render_helpers(example_profile):
    assert render.file_stem("Ayşe Yılmaz") == "Ayse-Yilmaz"
    assert render.file_stem("İsmail Öztürk-Çağlar") == "Ismail-Ozturk-Caglar"
    assert render._month("2020-07") == "Jul 2020" and render._month(None) == "Present"
    header = render._header(example_profile)
    assert header["contact"] == [
        "Istanbul, TR", "+90 555 000 00 00",
        {"text": "ayse.yilmaz@example.com", "url": "mailto:ayse.yilmaz@example.com"},
        {"text": "linkedin.com/in/example", "url": "https://www.linkedin.com/in/example"},
        {"text": "github.com/example", "url": "https://github.com/example"}]
    assert header["paper"] == "a4"
    example_profile["identity"]["location"]["country"] = "US"
    assert render._header(example_profile)["paper"] == "us-letter"


def test_settings(tmp_path):
    path = tmp_path / "settings.json"
    assert settings.load(path) == {"writer_model": "sonnet", "review": True}
    assert settings.save({"writer_model": "sonnet", "review": False}, path)["review"] is False
    with pytest.raises(settings.SettingsError):
        settings.save({"writer_model": "haiku", "review": False}, path)
    with pytest.raises(settings.SettingsError):
        settings.save({"writer_model": "sonnet", "review": "yes"}, path)


def test_cli_approve_batch_save_flow(tmp_path, monkeypatch, capsys, example_profile):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(example_profile), encoding="utf-8")
    jobs_path = tmp_path / "jobs" / "jobs.json"
    jobs.save({"jobs": [{**JOB, "status": "new"}]}, jobs_path)
    for name, value in [("PROFILE_JSON", profile_path), ("JOBS_JSON", jobs_path),
                        ("CV_DIR", tmp_path / "cv"), ("CV_SETTINGS_JSON", tmp_path / "cv" / "settings.json")]:
        monkeypatch.setattr(cli, name, value)

    def run(*argv):
        code = cli.main(list(argv))
        return code, json.loads(capsys.readouterr().out)

    assert run("cv", "batch")[1]["count"] == 0
    assert run("jobs", "approve", "linkedin:1", "linkedin:9")[1]["skipped"] == [
        {"id": "linkedin:9", "reason": "unknown"}]
    assert run("cv", "configure", "--writer-model", "sonnet")[0] == 0
    _, made = run("cv", "batch")
    entry = made["jobs"][0]
    Path(entry["answer_path"]).write_text(json.dumps({**ANSWER, "summary": "11 years."}))
    code, saved = run("cv", "save", entry["input_path"], entry["answer_path"])
    assert code == 1 and saved["errors"] == ["summary: number_not_in_profile"]
    Path(entry["answer_path"]).write_text(json.dumps(ANSWER))
    code, saved = run("cv", "save", entry["input_path"], entry["answer_path"])
    assert code == 0 and saved["documents"]["model"] == "sonnet" and saved["pages"] == 1
    stored = jobs.load(jobs_path)["jobs"][0]
    assert stored["status"] == "cv_ready" and (tmp_path / stored["documents"]["cv"]).exists()
    assert run("cv", "batch", "--id", "linkedin:1")[1]["count"] == 1  # written again on request
    assert run("cv", "batch", "--id", "linkedin:9")[0] == 1


def test_date_ranges_use_an_ascii_hyphen(example_profile):
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(
        render.compile_pdf(render.cv_data(example_profile, check(example_profile)["plan"], NOW.date())))).pages)
    assert "2015 - 2020" in text and "- Present" in text and not render.DATE_DASH.search(text)
    assert render.date_dashes(render.compile_pdf(render.cv_data(example_profile, check(example_profile)["plan"]))) == []


def test_education_ending_after_this_year_is_expected(example_profile):
    example_profile["education"][0]["end_year"] = 2027
    data = render.cv_data(example_profile, check(example_profile)["plan"], NOW.date())
    assert data["education"][0]["dates"] == "2015 - 2027 (expected)"
    example_profile["education"][0]["end_year"] = 2026
    assert render.cv_data(example_profile, check(example_profile)["plan"], NOW.date())["education"][0]["dates"] == "2015 - 2026"
    example_profile["education"][0]["end_year"] = None
    assert render.cv_data(example_profile, check(example_profile)["plan"], NOW.date())["education"][0]["dates"] == "2015 - Present"


def test_date_dashes_finds_a_year_next_to_a_unicode_dash(example_profile):
    plan = check(example_profile)["plan"]
    plan["summary"] = "Backend engineer, 2019–2023 at a bank and 2023 — today elsewhere; v1-2 is fine."
    found = render.date_dashes(render.compile_pdf(render.cv_data(example_profile, plan, NOW.date())))
    assert found == ["2019–", "2023 —"]


def test_write_rejects_a_cv_with_unicode_date_dashes(tmp_path, example_profile):
    plan = check(example_profile)["plan"]
    plan["summary"] = "Backend engineer since 2019–2020."
    with pytest.raises(render.RenderError, match="ASCII date ranges"):
        render.write(example_profile, JOB, plan, tmp_path, NOW.date())
