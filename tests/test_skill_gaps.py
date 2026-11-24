import json
from datetime import UTC, datetime

from jobfinder import cli, jobs
from jobfinder.score import skill_gaps

NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
TODAY = NOW.date()
SAFE = {"verdict": "pass", "reasons": [], "model_score": 0.01, "checked_at": NOW.isoformat()}
PASSED = {"passed": True, "reasons": [], "checked_at": NOW.isoformat()}


def posting(job_id, required=(), preferred=(), score=None, created_at=NOW, **changes):
    job = {"id": job_id, "title": f"Engineer {job_id}", "company_name": "Acme", "status": "new",
           "created_at": created_at.isoformat(), "safety": SAFE, "filter": PASSED,
           "extracted": {"required_skills": list(required), "preferred_skills": list(preferred)}}
    if score is not None:
        job["match"] = {"score": score}
    return {**job, **changes}


def report(example_profile, *postings, **options):
    return skill_gaps.report({"jobs": list(postings)}, example_profile, today=TODAY, **options)


def test_only_skills_the_profile_lacks_are_listed(example_profile):
    # Python is in the skill list, Kafka in a job's technologies, K8s an alias of Kubernetes,
    # Communication a generic skill
    found = report(example_profile, posting("a", ["Python", "Go", "K8s", "Communication"], ["Kafka", "Rust"]))
    assert [s["skill"] for s in found["skills"]] == ["Go", "Rust"]
    assert found["postings"] == 1


def test_weight_is_required_two_preferred_one_times_the_score(example_profile):
    found = report(example_profile, posting("a", ["Go"], score=80), posting("b", [], ["Go"], score=60),
                   posting("c", ["Rust"]))
    go, rust = found["skills"]
    assert go == {"skill": "Go", "key": "go", "weight": 2.2, "postings": 2, "required": 1, "preferred": 1,
                  "avg_score": 70, "examples": [
                      {"id": "a", "title": "Engineer a", "company_name": "Acme"},
                      {"id": "b", "title": "Engineer b", "company_name": "Acme"}]}
    assert rust["weight"] == 1.0 and rust["avg_score"] is None  # unscored counts half


def test_aliases_are_one_skill_counted_once_per_posting(example_profile):
    found = report(example_profile, posting("a", ["Golang"], ["Go"]), posting("b", [], ["go "]))
    assert found["skills"] == [{"skill": "Go", "key": "go", "weight": 1.5, "postings": 2, "required": 1,
                                "preferred": 1, "avg_score": None, "examples": [
                                    {"id": "a", "title": "Engineer a", "company_name": "Acme"},
                                    {"id": "b", "title": "Engineer b", "company_name": "Acme"}]}]


def test_blocked_filtered_old_and_unread_postings_are_left_out(example_profile):
    old = datetime(2026, 7, 1, tzinfo=UTC)
    unread = posting("e", ["Rust"])
    del unread["extracted"]
    found = report(example_profile, posting("a", ["Go"]),
                   posting("b", ["Rust"], safety={**SAFE, "verdict": "block"}),
                   posting("c", ["Rust"], filter={**PASSED, "passed": False}),
                   posting("d", ["Rust"], created_at=old), unread)
    assert found["postings"] == 1 and [s["skill"] for s in found["skills"]] == ["Go"]
    assert report(example_profile, posting("d", ["Rust"], created_at=old), days=200)["postings"] == 1


def test_top_cuts_the_list(example_profile):
    found = report(example_profile, posting("a", ["Go", "Rust", "Scala"]), top=2)
    assert found["count"] == 3 and len(found["skills"]) == 2


def test_cli_gaps(tmp_path, monkeypatch, capsys, example_profile):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(example_profile), encoding="utf-8")
    jobs_path = tmp_path / "jobs.json"
    jobs_path.write_text(json.dumps({"jobs": [posting("a", ["Go"], score=90,
                                                       created_at=datetime.now(UTC))]}), encoding="utf-8")
    monkeypatch.setattr(cli, "PROFILE_JSON", profile_path)
    monkeypatch.setattr(cli, "JOBS_JSON", jobs_path)
    monkeypatch.setattr(jobs, "load", lambda path: json.loads(path.read_text(encoding="utf-8")))
    assert cli.main(["score", "gaps"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["days"] == 90 and [s["skill"] for s in out["skills"]] == ["Go"]
    assert cli.main(["score", "gaps", "--top", "0"]) == 1


def test_keys_tell_c_cpp_and_csharp_apart():
    assert [skill_gaps.key(s) for s in ("C", "C++", "C#", "Apache Kafka", "'; rm -rf ~")] == [
        "c", "c-plus-plus", "c-sharp", "apache-kafka", "rm-rf"]


def test_cli_add_skill_writes_the_name_from_the_report(tmp_path, monkeypatch, capsys, example_profile):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(example_profile), encoding="utf-8")
    jobs_path = tmp_path / "jobs.json"
    jobs_path.write_text(json.dumps({"jobs": [posting("a", ["Golang", "C++"], created_at=datetime.now(UTC))]}),
                         encoding="utf-8")
    monkeypatch.setattr(cli, "PROFILE_JSON", profile_path)
    monkeypatch.setattr(cli, "JOBS_JSON", jobs_path)
    monkeypatch.setattr(jobs, "load", lambda path: json.loads(path.read_text(encoding="utf-8")))
    assert cli.main(["profile", "add-skill", "c-plus-plus", "advanced"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["added"] == {"name": "C++", "level": "advanced"} and out["errors_at_pointer"] == []
    saved = json.loads(profile_path.read_text(encoding="utf-8"))
    assert saved["skills"]["technical"][-1] == {"name": "C++", "level": "advanced"}
    assert cli.main(["profile", "add-skill", "c-plus-plus", "advanced"]) == 1  # no longer missing
    assert cli.main(["profile", "add-skill", "rust", "expert"]) == 1  # not asked for
