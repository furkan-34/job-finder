import json
from datetime import UTC, date, datetime

import pytest

from jobfinder import apply, cli, jobs

NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
JOB = {"id": "linkedin:1", "data_source": "APIFY_LINKEDIN_SCRAPER", "source_id": "1",
       "url": "https://www.linkedin.com/jobs/view/1", "apply_url": None,
       "title": "Senior Backend Engineer", "company_name": "Acme Software GmbH",
       "location": "Istanbul, Turkey", "work_modes": ["hybrid"],
       "description": "We build payment services in Python.", "status": "cv_ready",
       "created_at": "2026-10-20T08:00:00+00:00",
       "safety": {"verdict": "pass", "reasons": [], "model_score": 0.01,
                  "checked_at": "2026-10-20T08:00:00+00:00"}}
DOCUMENTS = {"cv": "cv/linkedin-1/Ayse-Yilmaz-CV.pdf",
             "cover_letter": "cv/linkedin-1/Ayse-Yilmaz-Cover-Letter.pdf",
             "language": "en", "model": "opus", "created_at": NOW.isoformat()}


def job(job_id="linkedin:1", score=None, **changes):
    entry = {**JOB, "id": job_id, **changes}
    if score is not None:
        entry["match"] = {"score": score, "summary": "", "strengths": [], "gaps": [],
                          "scored_at": NOW.isoformat()}
    return entry


def test_ready_lists_cv_ready_postings_best_first(tmp_path):
    store = {"jobs": [job("a", 70), job("b", 90), job("c"), job("d", 99, status="approved"),
                      job("e", 95, safety={"verdict": "block", "reasons": ["fee_request"]})]}
    entries = apply.ready(store, tmp_path)
    assert [entry["id"] for entry in entries] == ["b", "a", "c"]
    assert "description" not in entries[0]


def test_ready_gives_the_link_warnings_and_documents(tmp_path):
    (tmp_path / "cv" / "linkedin-1").mkdir(parents=True)
    (tmp_path / "cv" / "linkedin-1" / "Ayse-Yilmaz-CV.pdf").write_bytes(b"%PDF")
    store = {"jobs": [job(apply_url="https://jobs.example.xyz/1",
                          safety={"verdict": "warn", "reasons": ["apply_domain_unknown:example.xyz"]},
                          documents=DOCUMENTS)]}
    entry = apply.ready(store, tmp_path)[0]
    assert entry["link"] == "https://jobs.example.xyz/1"
    assert entry["warnings"] == ["apply_domain_unknown:example.xyz"]
    assert entry["documents"] == {
        "cv": {"path": str(tmp_path / "cv/linkedin-1/Ayse-Yilmaz-CV.pdf"), "exists": True},
        "cover_letter": {"path": str(tmp_path / "cv/linkedin-1/Ayse-Yilmaz-Cover-Letter.pdf"),
                         "exists": False}}


def test_ready_lists_the_text_letter_and_the_posting_pdf_when_there(tmp_path):
    folder = "cv/24-10-2026/acme-software-gmbh-1"
    documents = {**DOCUMENTS, "cover_letter_text": f"{folder}/Ayse-Yilmaz-Cover-Letter.txt",
                 "job_description": f"{folder}/Job-Description.pdf"}
    (tmp_path / folder).mkdir(parents=True)
    (tmp_path / folder / "Ayse-Yilmaz-Cover-Letter.txt").write_text("Dear team,")
    entry = apply.ready({"jobs": [job(documents=documents)]}, tmp_path)[0]
    assert list(entry["documents"]) == ["cv", "cover_letter", "cover_letter_text", "job_description"]
    assert entry["documents"]["cover_letter_text"]["exists"] is True
    assert entry["documents"]["job_description"]["exists"] is False
    assert jobs.validate(job(documents=documents)) == []


def test_link_falls_back_to_the_posting_page(tmp_path):
    assert apply.ready({"jobs": [job()]}, tmp_path)[0]["link"] == JOB["url"]





def test_mark_applied_today_or_on_a_given_day():
    entry = job(documents=DOCUMENTS)
    assert apply.mark_applied(entry, now=NOW)["applied_at"] == "2026-10-23T08:00:00+00:00"
    assert entry["status"] == "applied"
    assert jobs.validate(entry) == []
    entry = job()
    apply.mark_applied(entry, date(2026, 10, 21), now=NOW)
    assert entry["application"] == {"applied_at": "2026-10-21T00:00:00+00:00"}


@pytest.mark.parametrize("changes, day, message", [
    ({"status": "approved"}, None, "only a posting with status 'cv_ready'"),
    ({"status": "applied", "application": {"applied_at": "2026-10-22T09:00:00+00:00"}}, None,
     "already marked as applied"),
    ({}, date(2026, 10, 24), "in the future"),
    ({}, date(2026, 10, 19), "before the posting was saved"),
])
def test_mark_applied_refuses(changes, day, message):
    entry = job(**changes)
    with pytest.raises(apply.ApplyError, match=message):
        apply.mark_applied(entry, day, now=NOW)
    assert entry["status"] == changes.get("status", "cv_ready")


def test_parse_day():
    today = date(2026, 10, 23)
    assert apply.parse_day("today", today) == today
    assert apply.parse_day("yesterday", today) == date(2026, 10, 22)
    assert apply.parse_day("2026-10-01", today) == date(2026, 10, 1)
    with pytest.raises(apply.ApplyError):
        apply.parse_day("last week", today)


def test_cli_list_mark(tmp_path, monkeypatch, capsys):
    jobs_path = tmp_path / "jobs.json"
    jobs.save({"jobs": [job("a", 80), job("b", status="new")]}, jobs_path)
    monkeypatch.setattr(cli, "JOBS_JSON", jobs_path)

    def run(*argv):
        code = cli.main(list(argv))
        return code, json.loads(capsys.readouterr().out)

    code, listed = run("apply", "list")
    assert code == 0 and [entry["id"] for entry in listed["jobs"]] == ["a"]
    assert listed["jobs"][0]["link"] == JOB["url"]
    assert run("apply", "mark", "nope") == (1, {"ok": False, "error": "no posting with id 'nope'"})
    assert run("apply", "mark", "b")[0] == 1
    assert run("apply", "mark", "a", "--date", "soon")[0] == 1
    code, marked = run("apply", "mark", "a")
    assert code == 0 and marked["status"] == "applied"
    stored = jobs.load(jobs_path)["jobs"][0]
    assert stored["status"] == "applied" and stored["application"]["applied_at"]
    assert run("apply", "mark", "a")[0] == 1
    assert run("apply", "list")[1]["count"] == 0
