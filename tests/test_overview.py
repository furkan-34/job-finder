import json

from jobfinder import cli, overview

CHECKED = {"verdict": "pass", "reasons": [], "checked_at": "2026-10-20T08:00:00+00:00"}


def job(n: int, status="new", **changes) -> dict:
    return {"id": f"linkedin:{n}", "title": f"Engineer {n}", "company_name": "Acme",
            "url": f"https://www.linkedin.com/jobs/view/{n}", "status": status,
            "created_at": f"2026-10-{10 + n:02d}T08:00:00+00:00", "safety": CHECKED, **changes}


def scored(n: int, score: int, status="new", **changes) -> dict:
    return job(n, status, filter={"passed": True}, match={"score": score}, **changes)


def test_stage_is_the_furthest_step_reached():
    assert overview.stage({**job(1), "safety": None}) == "unchecked"
    assert overview.stage(job(1, safety={"verdict": "block", "reasons": ["fee_request"]})) == "blocked"
    assert overview.stage(job(1, filter={"passed": False})) == "filtered_out"
    assert overview.stage(job(1, filter={"passed": True})) == "unscored"
    assert overview.stage(scored(1, 50)) == "new"
    assert overview.stage(scored(1, 90, "cv_ready")) == "cv_ready"


def test_deleted_postings_are_never_listed_or_counted():
    store = {"jobs": [scored(1, 90, "cv_ready"), scored(2, 80, "deleted"),
                      job(3, "deleted", safety={"verdict": "block", "reasons": ["x"]})]}
    assert overview.stage(store["jobs"][2]) == "deleted"
    result = overview.listing(store)
    assert result["total"] == 1 and result["counts"] == {"cv_ready": 1}
    assert [row["id"] for row in result["jobs"]] == ["linkedin:1"]


def test_blocked_posting_shows_no_text():
    row = overview.entry(job(1, safety={"verdict": "block", "reasons": ["prompt_injection"]}))
    assert row == {"id": "linkedin:1", "stage": "blocked"}


def test_listing_counts_and_orders():
    warn = {"verdict": "warn", "reasons": ["link_shortener"], "checked_at": "x"}
    store = {"jobs": [scored(1, 60), scored(2, 80), scored(3, 75, "applied", safety=warn,
                      application={"applied_at": "2026-10-21T09:00:00+00:00"},
                      tracking={"outcome": "interview"}),
                      job(4, filter={"passed": False})]}
    result = overview.listing(store)
    assert result["total"] == 4
    assert result["counts"] == {"applied": 1, "new": 2, "filtered_out": 1}
    assert [row["id"] for row in result["jobs"]] == ["linkedin:3", "linkedin:2", "linkedin:1",
                                                     "linkedin:4"]
    first = result["jobs"][0]
    assert first["warnings"] == ["link_shortener"] and first["outcome"] == "interview"
    assert first["applied_at"].startswith("2026-10-21")
    only_new = overview.listing(store, ["new"], top=1)
    assert only_new["count"] == 1 and only_new["jobs"][0]["id"] == "linkedin:2"


def test_cli_jobs_list(tmp_path, monkeypatch, capsys):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"jobs": [scored(1, 70, "shortlist")]}), encoding="utf-8")
    monkeypatch.setattr(cli, "JOBS_JSON", path)
    assert cli.main(["jobs", "list", "--stage", "shortlist"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"] == {"shortlist": 1} and out["jobs"][0]["score"] == 70


def test_cli_apply_configure_saves_and_checks(tmp_path, monkeypatch, capsys):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(cli, "APPLY_SETTINGS_JSON", path)
    assert cli.main(["apply", "configure", "--confirm-before-submit", "false",
                     "--max-per-day", "5"]) == 0
    saved = json.loads(path.read_text())
    assert saved["confirm_before_submit"] is False and saved["max_per_day"] == 5
    capsys.readouterr()
    assert cli.main(["apply", "configure", "--max-per-day", "0"]) == 1
    assert json.loads(path.read_text())["max_per_day"] == 5
    assert cli.main(["apply", "settings"]) == 0


def test_people_search_links_are_encoded_search_text():
    links = overview.people_search({"company_name": "Kahve & Çay Ltd. Şti.",
                                    "title": "Senior Backend Engineer (m/w/d)"})
    base = "https://www.linkedin.com/search/results/people/?keywords="
    assert links == {"recruiter": base + "Kahve%20%26%20%C3%87ay%20recruiter",
                     "team": base + "Kahve%20%26%20%C3%87ay%20Senior%20Backend%20Engineer"}
    assert overview.people_search({"company_name": None, "title": "Engineer"}) is None


def test_people_search_only_from_scoring_on():
    rows = {row["id"]: row for row in overview.listing({"jobs": [
        scored(1, 80, "applied", application={"applied_at": "2026-10-21T09:00:00+00:00"}),
        scored(2, 40), job(3, filter={"passed": False}), job(4),
        job(5, safety={"verdict": "block", "reasons": ["fee_request"]})]})["jobs"]}
    assert rows["linkedin:1"]["people_search"]["recruiter"].endswith("Acme%20recruiter")
    assert rows["linkedin:2"]["people_search"] is not None
    assert rows["linkedin:3"]["people_search"] is None and rows["linkedin:4"]["people_search"] is None
    assert "people_search" not in rows["linkedin:5"]
