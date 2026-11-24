import json
from datetime import UTC, datetime

import pytest

from jobfinder import cli, jobs
from jobfinder.fetch.runner import run_searches
from jobfinder.score.rules import EMPLOYMENT_TYPES
from jobfinder.sources import actors as registry
from jobfinder.sources import ats
from jobfinder.sources.apify import SourceError
from jobfinder.sources.mapping import to_job

ENTRIES = {a["name"]: a for a in registry.load()}
GREENHOUSE, LEVER, ASHBY = ENTRIES["GREENHOUSE"], ENTRIES["LEVER"], ENTRIES["ASHBY"]
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)

# Shaped like each service's answer (fields as seen on the live APIs), with short made-up text.
GH_ITEM = {"absolute_url": "https://job-boards.greenhouse.io/acme/jobs/8860302002",
           "data_compliance": [{"type": "gdpr", "requires_consent": False}], "internal_job_id": 6547054002,
           "location": {"name": "Remote, Germany"}, "metadata": [], "id": 8860302002,
           "updated_at": "2026-10-25T15:24:46-04:00", "requisition_id": "R1", "title": "Senior Backend Engineer",
           "company_name": "Acme", "first_published": "2026-10-24T11:31:50-04:00", "language": "en",
           "application_deadline": None,
           "content": "&lt;p&gt;Build &lt;strong&gt;APIs&lt;/strong&gt; in Python &amp;amp; Go.&lt;/p&gt;"
                      "&lt;ul&gt;&lt;li&gt;5 years&lt;/li&gt;&lt;/ul&gt;",
           "departments": [{"id": 1, "name": "Engineering", "child_ids": [], "parent_id": None}],
           "offices": [{"id": 2, "name": "Berlin", "location": "Berlin, Germany", "child_ids": [], "parent_id": None}]}
LEVER_ITEM = {"additionalPlain": "Salary: 70k EUR.", "additional": "<div>Salary: 70k EUR.</div>",
              "categories": {"commitment": "Permanent", "department": "Engineering", "location": "Istanbul",
                             "team": "Platform", "allLocations": ["Istanbul", "Ankara"]},
              "createdAt": 1792767640768, "descriptionPlain": "We build payments.",
              "description": "<div>We build payments.</div>", "id": "2a55d6c9-c564-443a-be3e-a3851db5a93b",
              "lists": [{"text": "Requirements", "content": "<li>Python</li><li>PostgreSQL</li>"}],
              "text": "Senior Backend Engineer", "country": "TR", "workplaceType": "hybrid",
              "opening": "", "openingPlain": "", "descriptionBody": "", "descriptionBodyPlain": "",
              "hostedUrl": "https://jobs.lever.co/acme/2a55d6c9-c564-443a-be3e-a3851db5a93b",
              "applyUrl": "https://jobs.lever.co/acme/2a55d6c9-c564-443a-be3e-a3851db5a93b/apply"}
ASHBY_ITEM = {"id": "7458d4e9-da2e-47bd-98cb-adfda43d42b2", "title": "Backend Engineer, Senior",
              "department": "Engineering", "team": "Core", "employmentType": "FullTime",
              "location": "Remote - European Union", "shouldDisplayCompensationOnJobPostings": True,
              "secondaryLocations": [{"location": "Amsterdam", "address": {"postalAddress": {
                  "addressCountry": "Netherlands", "addressLocality": "Amsterdam"}}}],
              "publishedAt": "2026-10-20T14:29:08.532+00:00", "isListed": True, "isRemote": True,
              "workplaceType": "Remote", "address": {"postalAddress": {
                  "postalCode": "", "addressRegion": "", "addressCountry": "European Union", "addressLocality": ""}},
              "jobUrl": "https://jobs.ashbyhq.com/acme/7458d4e9-da2e-47bd-98cb-adfda43d42b2",
              "applyUrl": "https://jobs.ashbyhq.com/acme/7458d4e9-da2e-47bd-98cb-adfda43d42b2/application",
              "descriptionHtml": "<p>Own the core API.</p>", "descriptionPlain": "Own the core API.",
              "compensation": {"compensationTierSummary": "€90K – €120K", "compensationTiers": [],
                               "summaryComponents": []}}
SEARCH = {"id": "be-de", "keywords": "Senior Backend Engineer remote", "location": "Germany", "limit": 5}


def entry(actor: dict, **boards) -> dict:
    return {**actor, "boards": boards or {"acme": "Acme"}}


def answer(kind: str, items: list[dict]):
    return items if kind == "lever" else {"jobs": items}


def test_the_three_boards_are_free_keyless_sources_in_all():
    for actor in (GREENHOUSE, LEVER, ASHBY):
        assert actor["kind"] in ats.KINDS and actor["cost_per_1000_results_usd"] == 0 and actor["boards"]
        assert not registry.uses_apify([actor])
    assert {"GREENHOUSE", "LEVER", "ASHBY"} <= {a["name"] for a in registry.select(registry.load(), ["all"])}


def test_search_values_drop_work_mode_words_and_look_the_country_up():
    values = ats.search_values(GREENHOUSE, "Senior Backend Engineer remote", "Berlin, Germany", 25, "pastWeek")
    assert values["keywords"] == ["senior", "backend", "engineer"] and values["days"] == 7
    assert {"germany", "deutschland", "munich", "berlin"} <= set(values["location_words"])
    assert values["boards"] == list(GREENHOUSE["boards"]) and values["limit"] == 25
    assert ats.location_words(GREENHOUSE, "Worldwide") is None
    assert ats.search_values(GREENHOUSE, "Go", "Turkey", 5, "anyTime")["days"] is None


@pytest.mark.parametrize("actor, keywords, location, date_posted, message", [
    (GREENHOUSE, "Python", "Istanbul, Atlantis", "anyTime", "'atlantis' is not in `countries`"),
    (GREENHOUSE, "remote hybrid", "Germany", "anyTime", "only words GREENHOUSE leaves out"),
    (GREENHOUSE, "Python", "Germany", "lastYear", "date_posted must be one of"),
    ({**GREENHOUSE, "boards": {}}, "Python", "Germany", "anyTime", "has no company boards")])
def test_search_values_refuse_what_they_would_have_to_guess(actor, keywords, location, date_posted, message):
    with pytest.raises(SourceError, match=message):
        ats.search_values(actor, keywords, location, 5, date_posted)


@pytest.mark.parametrize("actor, item, location", [
    (GREENHOUSE, GH_ITEM, "Germany"), (LEVER, LEVER_ITEM, "Istanbul, Turkey"), (ASHBY, ASHBY_ITEM, "EU")])
def test_fetch_filters_by_title_location_and_date(actor, item, location):
    other_title = {**item, "id": "x1", "title": "Data Scientist", "text": "Data Scientist"}
    other_place = {**item, "id": "x2", "location": {"name": "Tokyo, Japan"} if actor is GREENHOUSE else "Tokyo",
                   "offices": [], "categories": {"location": "Tokyo"}, "secondaryLocations": [], "address": {}}
    old = {**item, "id": "x3", "first_published": "2025-01-01T00:00:00Z", "createdAt": 1735689600000,
           "publishedAt": "2025-01-01T00:00:00Z"}
    urls = []

    def get(url):
        urls.append(url)
        return answer(actor["kind"], [item, other_title, other_place, old])

    run, params, items = ats.fetch(entry(actor), "Senior Backend Engineer remote", location, 5, "pastMonth",
                                   get, NOW)
    assert [i["id"] for i in items] == [item["id"]]
    assert items[0]["_board"] == {"name": "acme", "company": "Acme"}
    assert {k: v for k, v in items[0].items() if k != "_board"} == item  # otherwise unchanged
    assert run.id is None and run.usage_total_usd == 0
    assert params["title_words"] == ["senior", "backend", "engineer"] and params["posted_within_days"] == 30
    assert urls == [ats.URLS[actor["kind"]].format(board="acme")]
    _, _, any_date = ats.fetch(entry(actor), "Backend", location, 5, "anyTime", get, NOW)
    assert {i["id"] for i in any_date} == {item["id"], "x3"}


def test_fetch_keeps_the_newest_up_to_the_limit_across_boards():
    def get(url):
        board = url.split("/boards/")[1].split("/")[0]
        return {"jobs": [{**GH_ITEM, "id": f"{board}-{day}", "first_published": f"2026-10-{day:02d}T00:00:00Z"}
                         for day in (3, 9, 21)]}

    _, _, items = ats.fetch(entry(GREENHOUSE, a="A", b="B"), "Backend", "Germany", 3, "anyTime", get, NOW)
    assert [i["id"] for i in items] in (["a-21", "b-21", "a-9"], ["b-21", "a-21", "a-9"])
    assert {i["_board"]["company"] for i in items} == {"A", "B"}


def test_ashby_leaves_unlisted_postings_out():
    _, _, items = ats.fetch(entry(ASHBY), "Backend", "Worldwide", 5, "anyTime",
                            lambda url: {"jobs": [{**ASHBY_ITEM, "isListed": False}]}, NOW)
    assert items == []


@pytest.mark.parametrize("actor, bad", [(GREENHOUSE, {"error": "boom"}), (LEVER, {"ok": False}),
                                        (ASHBY, [ASHBY_ITEM])])
def test_fetch_rejects_an_unexpected_answer(actor, bad):
    with pytest.raises(SourceError, match="unexpected shape for board 'acme'"):
        ats.fetch(entry(actor), "Backend", "Germany", 5, "anyTime", lambda url: bad, NOW)


def test_greenhouse_item_maps_to_a_valid_job():
    job = to_job({**GH_ITEM, "_board": {"name": "acme", "company": "Acme Inc"}}, GREENHOUSE, NOW)
    assert job["id"] == "greenhouse:8860302002" and job["data_source"] == "GREENHOUSE"
    assert job["company_name"] == "Acme" and job["url"] == job["apply_url"] == GH_ITEM["absolute_url"]
    assert job["description"] == "Build APIs in Python & Go.\n5 years"
    assert job["location"] == "Remote, Germany" and job["posted_at"] == GH_ITEM["first_published"]
    assert jobs.validate(job) == []


def test_lever_item_maps_to_a_valid_job():
    job = to_job({**LEVER_ITEM, "_board": {"name": "acme", "company": "Acme"}}, LEVER, NOW)
    assert job["id"] == "lever:2a55d6c9-c564-443a-be3e-a3851db5a93b" and job["company_name"] == "Acme"
    assert job["apply_url"] == LEVER_ITEM["applyUrl"] and job["url"] == LEVER_ITEM["hostedUrl"]
    assert job["description"] == "We build payments.\n\nRequirements\n\nPython\nPostgreSQL\n\nSalary: 70k EUR."
    assert job["work_modes"] == ["hybrid"] and job["employment_type"] == "Permanent"
    assert job["posted_at"] == datetime.fromtimestamp(1792767640.768, UTC).isoformat()
    assert jobs.validate(job) == []


def test_ashby_item_maps_to_a_valid_job():
    job = to_job({**ASHBY_ITEM, "_board": {"name": "acme", "company": "Acme"}}, ASHBY, NOW)
    assert job["id"] == "ashby:7458d4e9-da2e-47bd-98cb-adfda43d42b2" and job["company_name"] == "Acme"
    assert job["work_modes"] == ["remote"] and job["employment_type"] == "FullTime"
    assert job["salary"] == "€90K – €120K" and job["description"] == "Own the core API."
    assert jobs.validate(job) == []


def test_the_rule_filter_knows_the_boards_employment_types():
    assert {jobs.normalize(t): EMPLOYMENT_TYPES.get(jobs.normalize(t)) for t in
            ("FullTime", "PartTime", "Intern", "Permanent", "Full-time")} == {
        "fulltime": "full_time", "parttime": "part_time", "intern": "internship",
        "permanent": "full_time", "full time": "full_time"}


def test_run_searches_reads_each_board_once_per_run(tmp_path, monkeypatch):
    calls = []

    def get(url, name, board):
        calls.append(url)
        return {"jobs": [GH_ITEM, {**GH_ITEM, "id": 2, "title": "Senior Backend Engineer, Payments"}]}

    monkeypatch.setattr(ats, "_get", get)
    monkeypatch.setattr(ats, "_CACHE", {})
    actor = entry(GREENHOUSE)
    data = {"searches": [SEARCH, {**SEARCH, "id": "be-de-2", "keywords": "Backend Engineer"}]}
    result = run_searches(None, data, [actor], "anyTime", raw_dir=tmp_path / "raw",
                          runs_dir=tmp_path / "runs", jobs_json=tmp_path / "jobs.json")
    assert result["ok"] and result["total_new"] == 2 and result["total_cost_usd"] == 0
    assert len(calls) == 1  # the second search reads the board from the cache
    store = jobs.load(tmp_path / "jobs.json")
    assert {j["id"] for j in store["jobs"]} == {"greenhouse:8860302002", "greenhouse:2"}
    assert all(j["safety"]["verdict"] in ("pass", "warn") for j in store["jobs"])
    raw = json.loads(min((tmp_path / "raw" / "GREENHOUSE").glob("*.json")).read_text(encoding="utf-8"))
    assert raw["items"][0] == {**GH_ITEM, "_board": {"name": "acme", "company": "Acme"}}
    assert raw["run_id"] is None and raw["input"]["boards"] == ["acme"]
    assert "Python" not in json.dumps(result)


def test_a_missing_board_fails_the_search_alone(tmp_path, monkeypatch):
    def get(url, name, board):
        raise SourceError(f"{name}: board {board!r} was not found (HTTP 404)")

    monkeypatch.setattr(ats, "_get", get)
    monkeypatch.setattr(ats, "_CACHE", {})
    result = run_searches(None, {"searches": [SEARCH]}, [entry(LEVER), next(a for a in registry.load() if a["name"] == "FREEHIRE")], raw_dir=tmp_path,
                          runs_dir=tmp_path, jobs_json=tmp_path / "jobs.json")
    assert "not found (HTTP 404)" in result["searches"][0]["error"]
    assert result["searches"][1]["data_source"] == "FREEHIRE"  # the next source still ran


def test_cli_fetch_run_does_not_ask_for_a_token(tmp_path, monkeypatch, capsys):
    searches = tmp_path / "searches.json"
    searches.write_text(json.dumps({"searches": [SEARCH]}), encoding="utf-8")
    monkeypatch.setattr(cli, "SEARCHES_JSON", searches)
    monkeypatch.setattr(cli, "make_client", lambda: pytest.fail("the boards need no Apify client"))
    monkeypatch.setattr(cli, "run_searches", lambda client, data, actors, date_posted: {
        "ok": True, "client": client, "sources": [a["name"] for a in actors]})
    assert cli.main(["fetch", "run", "--data-source", "GREENHOUSE", "--data-source", "LEVER",
                     "--data-source", "ASHBY"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "client": None,
                                                   "sources": ["GREENHOUSE", "LEVER", "ASHBY"]}
    assert cli.main(["source", "test", "--data-source", "ASHBY", "--keywords", "x",
                     "--location", "Turkey"]) == 1
    assert "use /search" in json.loads(capsys.readouterr().out)["error"]
