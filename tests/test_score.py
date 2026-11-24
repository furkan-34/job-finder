import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobfinder import cli, jobs, overview
from jobfinder.score import batches, rules, settings, shortlist

NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
SAFE = {"verdict": "pass", "reasons": [], "model_score": 0.01, "checked_at": NOW.isoformat()}
JOB = {"id": "linkedin:1", "data_source": "APIFY_LINKEDIN_SCRAPER", "source_id": "1",
       "url": "https://www.linkedin.com/jobs/view/1", "title": "Senior Backend Engineer",
       "company_name": "Acme Software GmbH", "location": "Istanbul, Turkey",
       "work_modes": ["hybrid"], "employment_type": "Full-time", "seniority": "Mid-Senior level",
       "description": "We build payment services in Python.", "status": "new",
       "created_at": NOW.isoformat(), "safety": SAFE}
EXTRACTED = {"industry": "Financial Services", "seniority": "senior", "min_years_experience": 5,
             "required_skills": ["Python", "PostgreSQL"], "preferred_skills": ["Kafka"],
             "required_languages": ["English"], "salary": None, "visa_sponsorship": None,
             "model": "haiku", "extracted_at": NOW.isoformat()}


def reasons(profile, **changes):
    return rules.check({**JOB, **changes}, profile["preferences"], profile["skills"]["languages"])


def test_matching_posting_passes(example_profile):
    assert reasons(example_profile) == []
    assert reasons(example_profile, extracted=EXTRACTED) == []


def test_exclude_words_reject_a_posting_with_one_in_title_company_or_description(example_profile):
    def check(**changes):
        return rules.check({**JOB, **changes}, example_profile["preferences"], example_profile["skills"]["languages"],
                           ["Commission Only", "unpaid"])
    assert check() == []
    assert check(title="Sales, commission only") == ["excluded_word:commission only"]
    assert check(description="This role is UNPAID at first.") == ["excluded_word:unpaid"]
    assert check(description="An unpaidish word does not count") == []


def test_dealbreaker_company_ignores_legal_form(example_profile):
    example_profile["preferences"]["dealbreakers"]["companies"] = ["ACME Software"]
    assert reasons(example_profile) == ["dealbreaker_company:acme software"]


@pytest.mark.parametrize("field, text", [
    ("title", "Senior Backend Engineer, Crypto"),
    ("description", "You will build our CRYPTO exchange."),
    ("company_name", "Crypto Labs"),
])
def test_dealbreaker_keyword_anywhere_in_the_text(example_profile, field, text):
    assert reasons(example_profile, **{field: text}) == ["dealbreaker_keyword:crypto"]


def test_dealbreaker_keyword_is_a_whole_word(example_profile):
    assert reasons(example_profile, description="We use cryptography for payments.") == []
    example_profile["preferences"]["dealbreakers"]["keywords"] = ["C++"]
    assert reasons(example_profile, description="Modern C++ and Rust") == ["dealbreaker_keyword:c++"]


def test_employment_type(example_profile):
    assert reasons(example_profile, employment_type="Part-time") == ["employment_type:part_time"]
    assert reasons(example_profile, employment_type="Other") == []  # unknown: never rejects
    assert reasons(example_profile, employment_type=None) == []


def test_seniority_from_the_source(example_profile):
    assert reasons(example_profile, seniority="Entry level") == ["seniority:entry_level"]
    assert reasons(example_profile, seniority="Associate") == ["seniority:associate"]
    assert reasons(example_profile, seniority="Not Applicable") == []
    example_profile["preferences"]["seniority"] = ["mid"]
    assert reasons(example_profile, seniority="Associate") == []


def test_work_mode(example_profile):
    assert reasons(example_profile, work_modes=["onsite"]) == []  # Istanbul allows onsite
    example_profile["preferences"]["locations"] = [{"place": "EU", "work_modes": ["remote"]}]
    assert reasons(example_profile, work_modes=["hybrid", "onsite"]) == ["work_mode:hybrid+onsite"]
    assert reasons(example_profile, work_modes=[]) == []


def test_extracted_industry_language_and_seniority(example_profile):
    gambling = {**EXTRACTED, "industry": "Online Gambling & Betting"}
    assert reasons(example_profile, extracted=gambling) == ["dealbreaker_industry:gambling"]
    german = {**EXTRACTED, "required_languages": ["German", "english"]}
    assert reasons(example_profile, extracted=german) == ["language:german"]
    # The candidate wants senior: mid and lead are one step away, junior and principal two.
    for level, expected in [("mid", []), ("lead", []), ("junior", ["seniority:junior"]),
                            ("principal", ["seniority:principal"]), (None, [])]:
        assert reasons(example_profile, extracted={**EXTRACTED, "seniority": level}) == expected


def test_check_store_reads_only_safe_new_postings(example_profile):
    store = {"jobs": [
        {**JOB, "id": "a"},
        {**JOB, "id": "b", "title": "Crypto Engineer"},
        {**JOB, "id": "c", "safety": {**SAFE, "verdict": "block", "reasons": ["fee_request"]}},
        {k: v for k, v in JOB.items() if k != "safety"} | {"id": "d"},  # not checked yet
        {**JOB, "id": "e", "status": "applied"},
        {**JOB, "id": "f", "safety": {**SAFE, "verdict": "warn", "reasons": ["link_shortener:bit.ly"]}},
    ]}
    for job in store["jobs"]:  # six different postings, not one in six copies
        job["description"] = f"{job['description']} Team {job['id']}."
    counts = rules.check_store(store, example_profile, now=NOW)
    assert counts == {"checked": 3, "passed": 2, "rejected": 1,
                      "reasons": {"dealbreaker_keyword:crypto": 1}, "same_posting_changed": 0}
    by_id = {job["id"]: job for job in store["jobs"]}
    assert by_id["b"]["filter"] == {"passed": False, "reasons": ["dealbreaker_keyword:crypto"],
                                    "checked_at": NOW.isoformat()}
    assert all("filter" not in by_id[i] for i in "cde")
    assert all(jobs.validate(job) == [] for job in store["jobs"])
    # Filtered once; again only with recheck (after the profile changed).
    assert rules.check_store(store, example_profile)["checked"] == 0
    example_profile["preferences"]["dealbreakers"]["keywords"] = []
    assert rules.check_store(store, example_profile, recheck=True)["passed"] == 3


def test_settings_validate_and_default(tmp_path):
    path = tmp_path / "settings.json"
    assert settings.load(path) == settings.DEFAULTS
    saved = settings.save({**settings.DEFAULTS, "matcher_model": "opus", "min_score": 80}, path)
    assert settings.load(path)["matcher_model"] == "opus" and saved["min_score"] == 80
    for bad in ({"matcher_model": "gpt"}, {"min_score": 101}, {"shortlist_size": 0},
                {"max_per_run": "10"}):
        with pytest.raises(settings.SettingsError):
            settings.save({**settings.DEFAULTS, **bad}, path)


def scored(job_id, score, **changes):
    match = {"score": score, "summary": "Good fit.", "strengths": ["Python"], "gaps": [],
             "model": "sonnet", "scored_at": NOW.isoformat()}
    return {**JOB, "id": job_id, "filter": {"passed": True, "reasons": [], "checked_at": NOW.isoformat()},
            "extracted": EXTRACTED, "match": match, **changes}


def test_shortlist_best_first_with_warnings():
    warn = {**SAFE, "verdict": "warn", "reasons": ["apply_domain_unknown:example.xyz"]}
    store = {"jobs": [
        scored("low", 60), scored("mid", 75), scored("top", 90, safety=warn),
        scored("rejected", 95, filter={"passed": False, "reasons": ["work_mode:onsite"],
                                       "checked_at": NOW.isoformat()}),
        scored("decided", 99, status="rejected"),
        {**JOB, "id": "unscored"},
    ]}
    assert all(jobs.validate(job) == [] for job in store["jobs"])
    entries = shortlist.ranked(store, min_score=70, top=10)
    assert [e["id"] for e in entries] == ["top", "mid"]
    assert entries[0]["warnings"] == ["apply_domain_unknown:example.xyz"] and entries[1]["warnings"] == []
    assert entries[0]["score"] == 90 and entries[0]["title"] == JOB["title"]
    assert "description" not in entries[0]
    assert [e["id"] for e in shortlist.ranked(store, min_score=0, top=1)] == ["top"]
    assert [e["id"] for e in shortlist.ranked(store, min_score=0, top=None)] == ["top", "mid", "low"]


def test_schema_caps_llm_written_fields():
    assert jobs.validate(scored("a", 80)) == []
    too_long = scored("a", 80, extracted={**EXTRACTED, "required_skills": ["x" * 61]})
    assert jobs.validate(too_long) != []
    assert jobs.validate(scored("a", 101)) != []
    assert jobs.validate({**JOB, "filter": {"passed": True, "reasons": ["no value"],
                                            "checked_at": NOW.isoformat()}}) != []


def test_cli_filter_and_list(tmp_path, monkeypatch, capsys, example_profile):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(example_profile), encoding="utf-8")
    jobs_path = tmp_path / "jobs.json"
    jobs.save({"jobs": [{**JOB, "id": "a", "description": "A third team."}, {**JOB, "id": "b", "employment_type": "Internship",
                                              "description": "Another team."},
                        scored("c", 88), scored("d", 86)]}, jobs_path)
    monkeypatch.setattr(cli, "PROFILE_JSON", profile_path)
    monkeypatch.setattr(cli, "JOBS_JSON", jobs_path)
    monkeypatch.setattr(cli, "SCORE_SETTINGS_JSON", tmp_path / "settings.json")
    assert cli.main(["score", "filter"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["checked"] == 2 and out["reasons"] == {"employment_type:internship": 1}
    assert cli.main(["score", "configure", "--min-score", "85", "--shortlist-size", "1"]) == 0
    assert cli.main(["score", "configure", "--min-score", "200"]) == 1
    capsys.readouterr()
    assert cli.main(["score", "list"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["min_score"] == 85 and [job["id"] for job in out["jobs"]] == ["c"]
    assert cli.main(["score", "list", "--all"]) == 0  # not cut to the shortlist size
    assert [job["id"] for job in json.loads(capsys.readouterr().out)["jobs"]] == ["c", "d"]


def test_cli_filter_needs_a_valid_profile(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "PROFILE_JSON", tmp_path / "missing.json")
    assert cli.main(["score", "filter"]) == 1
    assert "create-profile" in json.loads(capsys.readouterr().out)["error"]


def filtered(job_id, **changes):
    return {**JOB, "id": job_id, "filter": {"passed": True, "reasons": [], "checked_at": NOW.isoformat()},
            **changes}


def answer(path, entries, fence=False):
    text = json.dumps({"jobs": entries})
    path.write_text(f"```json\n{text}\n```\n" if fence else text, encoding="utf-8")
    return path


def test_make_batches_only_for_postings_that_need_the_step(tmp_path):
    batches_dir, results_dir = tmp_path / "batches", tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "extract-9.json").write_text("{}")  # an old answer goes with the old batches
    store = {"jobs": [filtered(f"j{i}") for i in range(5)] + [
        filtered("done", extracted=EXTRACTED),
        filtered("rejected", filter={"passed": False, "reasons": ["work_mode:onsite"],
                                     "checked_at": NOW.isoformat()}),
        filtered("blocked", safety={**SAFE, "verdict": "block", "reasons": ["fee_request"]}),
        {**JOB, "id": "unfiltered"},
    ]}
    made = batches.make(store, "extract", limit=4, batches_dir=batches_dir,
                        results_dir=results_dir, size=3)
    assert made["count"] == 4 and made["remaining"] == 1
    assert [b["count"] for b in made["batches"]] == [3, 1]
    assert made["batches"][0]["result_path"] == str(results_dir / "extract-1.json")
    assert not (results_dir / "extract-9.json").exists()
    first = json.loads((batches_dir / "extract-1.json").read_text())
    assert first["step"] == "extract" and [p["id"] for p in first["jobs"]] == ["j0", "j1", "j2"]
    assert set(first["jobs"][0]) == set(batches.POSTING_FIELDS)  # no safety, status or links
    match = batches.make(store, "match", limit=50, batches_dir=batches_dir, results_dir=results_dir)
    posting = json.loads(Path(match["batches"][0]["path"]).read_text())["jobs"][0]
    assert match["count"] == 1 and posting["id"] == "done"
    assert posting["extracted"]["required_skills"] == ["Python", "PostgreSQL"]
    assert "model" not in posting["extracted"]
    assert batches.make({"jobs": []}, "extract", 50, batches_dir, results_dir)["batches"] == []
    assert not list(batches_dir.glob("extract-*.json"))


def test_save_extraction_checks_every_value_and_filters_again(tmp_path, example_profile):
    store = {"jobs": [filtered("a"), filtered("b"), filtered("c"), filtered("d"), filtered("x")]}
    made = batches.make(store, "extract", 50, tmp_path / "b", tmp_path / "r", size=4)
    fields = {k: v for k, v in EXTRACTED.items() if k not in ("model", "extracted_at")}
    result = answer(tmp_path / "r" / "extract-1.json", [
        {"id": "a", **fields},
        {"id": "b", **fields, "industry": "Sports betting and gambling"},
        {"id": "c", **fields, "seniority": "ceo"},           # not in the enum
        {"id": "x", **fields},                               # in the store, not in this batch
        {"id": "a", **fields, "industry": "Crypto"},          # answered twice: the first one counts
        {"id": "zzz"}, "not an object",
    ], fence=True)
    out = batches.save(store, Path(made["batches"][0]["path"]), result, "sonnet", example_profile, NOW)
    assert out == {"step": "extract", "saved": 2, "invalid": 1, "unknown": 2, "missing": 1,
                   "first_invalid": "c /extracted/seniority", "rejected": 1,
                   "reasons": {"dealbreaker_industry:gambling": 1}}
    by_id = {job["id"]: job for job in store["jobs"]}
    assert by_id["a"]["extracted"] == {**EXTRACTED, "model": "haiku", "extracted_at": NOW.isoformat()}
    assert by_id["a"]["filter"]["passed"] and not by_id["b"]["filter"]["passed"]
    assert "extracted" not in by_id["c"] and "extracted" not in by_id["x"]
    assert all(jobs.validate(job) == [] for job in store["jobs"])


def test_save_fills_left_out_fields_and_rejects_non_json(tmp_path, example_profile):
    store = {"jobs": [filtered("a")]}
    made = batches.make(store, "extract", 50, tmp_path / "b", tmp_path / "r")
    batch = Path(made["batches"][0]["path"])
    out = batches.save(store, batch, answer(tmp_path / "r" / "x.json", [{"id": "a"}]),
                       "sonnet", example_profile, NOW)
    assert out["saved"] == 1 and store["jobs"][0]["extracted"]["required_skills"] == []
    (tmp_path / "r" / "bad.json").write_text("Here is the JSON you asked for")
    with pytest.raises(batches.BatchError):
        batches.save(store, batch, tmp_path / "r" / "bad.json", "sonnet", example_profile, NOW)
    with pytest.raises(batches.BatchError):
        batches.save(store, tmp_path / "r" / "x.json", tmp_path / "r" / "x.json", "sonnet",
                     example_profile, NOW)


def test_save_match(tmp_path, example_profile):
    store = {"jobs": [filtered("a", extracted=EXTRACTED), filtered("b", extracted=EXTRACTED)]}
    made = batches.make(store, "match", 50, tmp_path / "b", tmp_path / "r")
    store["jobs"][1]["status"] = "rejected"  # decided while the agent was working
    result = answer(tmp_path / "r" / "match-1.json", [
        {"id": "a", "score": 82, "summary": "Strong backend fit.", "strengths": ["Python"],
         "gaps": ["No Kafka"]},
        {"id": "b", "score": 90, "summary": "x", "strengths": [], "gaps": []},
    ])
    out = batches.save(store, Path(made["batches"][0]["path"]), result, "opus", example_profile, NOW)
    assert out == {"step": "match", "saved": 1, "invalid": 0, "unknown": 0, "missing": 0}
    assert store["jobs"][0]["match"] == {"score": 82, "summary": "Strong backend fit.",
                                         "strengths": ["Python"], "gaps": ["No Kafka"],
                                         "model": "opus", "scored_at": NOW.isoformat()}
    assert "match" not in store["jobs"][1]
    too_many = answer(tmp_path / "r" / "m2.json", [{"id": "a", "score": 70, "summary": "",
                                                    "strengths": ["a", "b", "c", "d"], "gaps": []}])
    store["jobs"][0].pop("match")
    assert batches.save(store, Path(made["batches"][0]["path"]), too_many, "opus",
                        example_profile, NOW)["first_invalid"] == "a /match/strengths"


def test_cli_batch_save_list_flow(tmp_path, monkeypatch, capsys, example_profile):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(example_profile), encoding="utf-8")
    jobs_path = tmp_path / "jobs.json"
    jobs.save({"jobs": [{**JOB, "id": "a"}]}, jobs_path)
    for name, value in [("PROFILE_JSON", profile_path), ("PROFILE_MD", tmp_path / "profile.md"),
                        ("JOBS_JSON", jobs_path),
                        ("SCORE_SETTINGS_JSON", tmp_path / "settings.json"),
                        ("SCORE_BATCHES_DIR", tmp_path / "batches"),
                        ("SCORE_RESULTS_DIR", tmp_path / "results")]:
        monkeypatch.setattr(cli, name, value)

    def run(*argv):
        code = cli.main(list(argv))
        return code, json.loads(capsys.readouterr().out)

    assert run("score", "filter")[1]["passed"] == 1
    _, made = run("score", "batch", "extract")
    batch = made["batches"][0]
    fields = {k: v for k, v in EXTRACTED.items() if k not in ("model", "extracted_at")}
    answer(Path(batch["result_path"]), [{"id": "a", **fields}])
    assert run("score", "save", batch["path"], batch["result_path"])[1]["saved"] == 1
    assert run("score", "configure", "--matcher-model", "opus")[0] == 0
    assert run("score", "batch", "match")[0] == 1  # the matcher reads profile.md
    (tmp_path / "profile.md").write_text("# Candidate profile\n")
    _, made = run("score", "batch", "match")
    batch = made["batches"][0]
    answer(Path(batch["result_path"]), [{"id": "a", "score": 77, "summary": "Good.",
                                         "strengths": [], "gaps": []}])
    assert run("score", "save", batch["path"], batch["result_path"])[1]["saved"] == 1
    _, listed = run("score", "list")
    assert [(job["id"], job["score"]) for job in listed["jobs"]] == [("a", 77)]
    assert jobs.load(jobs_path)["jobs"][0]["match"]["model"] == "opus"
    assert run("score", "save", batch["path"], str(tmp_path / "nope.json"))[0] == 1


def test_match_batch_carries_the_profile_and_a_short_description(tmp_path):
    store = {"jobs": [filtered("a", extracted=EXTRACTED, description="x " * 5000)]}
    made = batches.make(store, "match", 50, tmp_path / "b", tmp_path / "r", profile_md="# Profile\n")
    batch = json.loads(Path(made["batches"][0]["path"]).read_text())
    assert batch["profile"] == "# Profile\n" and len(batch["jobs"][0]["description"]) <= 6002


def copy_in(job_id, city, **changes):
    """The same posting in another city: only the city in the location and the text differs."""
    return {**JOB, "id": job_id, "location": f"{city}, Turkey",
            "description": f"We build payment services in Python. The team sits in {city}.",
            "created_at": f"2026-10-0{job_id[-1]}T08:00:00+00:00", **changes}


def test_one_posting_is_kept_of_each_group_of_copies(example_profile):
    store = {"jobs": [copy_in("j1", "Ankara"), copy_in("j2", "Istanbul"), copy_in("j3", "Izmir"),
                      {**copy_in("j4", "Bursa"), "description": "A different team in Bursa."}]}
    counts = rules.check_store(store, example_profile, now=NOW)
    by_id = {job["id"]: job for job in store["jobs"]}
    # Istanbul is the candidate's location, so that copy is read, not the first one saved.
    assert by_id["j2"]["filter"]["passed"] and by_id["j4"]["filter"]["passed"]
    for job_id in ("j1", "j3"):
        assert by_id[job_id]["filter"] == {"passed": False, "reasons": ["same_posting:j2"],
                                           "checked_at": NOW.isoformat()}
    assert counts["passed"] == 2 and counts["reasons"] == {"same_posting:j2": 2}
    assert all(jobs.validate(job) == [] for job in store["jobs"])
    assert [job["id"] for job in batches.pending(store, "extract")] == ["j2", "j4"]
    # Again, and after the preferences changed: the same result.
    assert rules.check_store(store, example_profile)["same_posting_changed"] == 0
    rules.check_store(store, example_profile, recheck=True)
    assert [by_id[i]["filter"]["reasons"] for i in ("j1", "j2", "j3")] == [
        ["same_posting:j2"], [], ["same_posting:j2"]]


def test_the_copy_already_worked_on_is_kept(example_profile):
    store = {"jobs": [copy_in("j2", "Istanbul"), copy_in("j1", "Ankara", status="applied")]}
    rules.check_store(store, example_profile, now=NOW)
    assert store["jobs"][0]["filter"]["reasons"] == ["same_posting:j1"]
    assert "filter" not in store["jobs"][1]  # never refiltered: it is past `new`


def test_a_copy_failing_its_own_rules_keeps_its_reasons(example_profile):
    store = {"jobs": [copy_in("j1", "Istanbul"), copy_in("j2", "Ankara", employment_type="Internship")]}
    rules.check_store(store, example_profile, now=NOW)
    assert store["jobs"][1]["filter"]["reasons"] == ["employment_type:internship"]
    assert store["jobs"][0]["filter"]["passed"]


def test_group_key():
    a = copy_in("j1", "Ankara", title="Senior Backend Engineer (m/w/d)")
    assert rules.group_key(a) == rules.group_key(copy_in("j2", "Istanbul"))
    assert rules.group_key(a) != rules.group_key({**a, "company_name": "Other"})
    assert rules.group_key({**a, "description": None}) is None


def test_lists_name_the_cities_of_the_copies(example_profile):
    store = {"jobs": [copy_in("j1", "Ankara"), scored("j2", 90, location="Istanbul, Turkey",
                      description="We build payment services in Python. The team sits in Istanbul.")]}
    rules.check_store(store, example_profile, now=NOW)
    top = shortlist.ranked(store, 70, None)[0]
    assert top["also_in"] == ["Ankara, Turkey"]
    assert top["people_search"]["recruiter"].endswith("keywords=Acme%20Software%20recruiter")
    rows = {row["id"]: row for row in overview.listing(store)["jobs"]}
    assert rows["j2"]["also_in"] == ["Ankara, Turkey"] and rows["j1"]["stage"] == "filtered_out"
