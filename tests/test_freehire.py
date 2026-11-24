import json
import urllib.parse
from datetime import UTC, datetime

import pytest

from jobfinder import cli, jobs
from jobfinder.fetch.runner import run_searches
from jobfinder.sources import actors as registry
from jobfinder.sources import freehire
from jobfinder.sources.apify import SourceError
from jobfinder.sources.mapping import to_job

FREEHIRE = next(a for a in registry.load() if a["name"] == "FREEHIRE")
LINKEDIN = registry.load()[0]
# Shaped like freehire's answer (fields as seen on the live API), with short made-up text.
ITEM = {"public_slug": "python-java-developer-vitelco-h2azajsu", "source": "whatjobs-tr",
        "url": "https://tr.whatjobs.com/pub_api__cpl__2185978__7091?utm_campaign=publisher",
        "title": "Python & Java Developer", "company": "Vitelco", "location": "Turkey",
        "description": "Python ile en az 5 yil deneyim.", "countries": ["tr"],
        "posted_at": "2026-10-12T06:21:40Z", "closed_at": None,
        "enrichment": {"category": "software_engineering", "employment_type": "contract"}}
SEARCH = {"id": "py-tr", "keywords": "Python Developer hybrid", "location": "Istanbul, Turkey", "limit": 3}


def query(url: str) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))


def test_freehire_is_part_of_all():
    assert FREEHIRE["kind"] == "freehire"
    assert [a["name"] for a in registry.select(registry.load(), ["all"])] == [
        "APIFY_LINKEDIN_SCRAPER", "APIFY_REED_SCRAPER", "APIFY_INDEED_UK_SCRAPER", "APIFY_TOTALJOBS_SCRAPER",
        "FREEHIRE", "GREENHOUSE", "LEVER", "ASHBY"]
    assert [a["name"] for a in registry.select(registry.load(), ["FREEHIRE"])] == ["FREEHIRE"]
    assert registry.uses_apify([LINKEDIN]) and not registry.uses_apify([FREEHIRE])


def test_params_drop_work_mode_words_and_look_the_country_up():
    assert freehire.build_params(FREEHIRE, "Python Developer hybrid", "Istanbul, Turkey", 25,
                                 "pastWeek") == {"q": "Python Developer", "countries": "tr", "limit": 25,
                                                 "posted_within_days": 7}
    # anyTime leaves the day filter out
    assert "posted_within_days" not in freehire.build_params(FREEHIRE, "Go", "Berlin, Germany", 5, "anyTime")
    assert freehire.country(FREEHIRE, "Türkiye") == "tr"


@pytest.mark.parametrize("keywords, location, date_posted, message", [
    ("Python", "Istanbul, Atlantis", "anyTime", "'atlantis' is not in `countries`"),
    ("remote hybrid", "Istanbul, Turkey", "anyTime", "only words freehire's search drops"),
    ("Python", "Istanbul, Turkey", "lastYear", "date_posted must be one of")])
def test_params_refuse_what_they_would_have_to_guess(keywords, location, date_posted, message):
    with pytest.raises(SourceError, match=message):
        freehire.build_params(FREEHIRE, keywords, location, 5, date_posted)


def test_fetch_pages_until_the_limit_or_the_last_page():
    urls = []

    def get(url):
        urls.append(url)
        offset, limit = int(query(url)["offset"]), int(query(url)["limit"])
        return {"data": [{**ITEM, "public_slug": f"p{offset + n}"} for n in range(min(limit, 30 - offset))]}

    run, params, items = freehire.fetch(FREEHIRE, "Python", "Turkey", 60, "anyTime", get)
    assert [(int(query(u)["offset"]), int(query(u)["limit"])) for u in urls] == [(0, 25), (25, 25)]
    assert len(items) == 30 and run.id is None and run.usage_total_usd == 0 and params["countries"] == "tr"
    _, _, few = freehire.fetch(FREEHIRE, "Python", "Turkey", 2, "anyTime", get)
    assert [i["public_slug"] for i in few] == ["p0", "p1"]


def test_fetch_rejects_an_answer_without_a_data_list():
    with pytest.raises(SourceError, match="unexpected shape"):
        freehire.fetch(FREEHIRE, "Python", "Turkey", 5, "anyTime", lambda url: {"error": "boom"})


def test_item_maps_to_a_valid_job():
    job = to_job(ITEM, FREEHIRE, datetime.now(UTC))
    assert job["id"] == "freehire:python-java-developer-vitelco-h2azajsu"
    assert job["data_source"] == "FREEHIRE" and job["company_name"] == "Vitelco"
    assert job["apply_url"] == job["url"] == ITEM["url"] and job["employment_type"] == "contract"
    assert job["work_modes"] == [] and job["posted_at"] == ITEM["posted_at"]
    assert jobs.validate(job) == []


def test_run_searches_needs_no_apify_client(tmp_path, monkeypatch):
    pages = []

    def get(url):
        pages.append(query(url))
        return {"data": [ITEM, {**ITEM, "public_slug": "other-1", "title": "Backend Engineer"}]}

    monkeypatch.setattr(freehire, "_get", get)
    result = run_searches(None, {"searches": [SEARCH]}, [FREEHIRE], "pastWeek", raw_dir=tmp_path / "raw",
                          runs_dir=tmp_path / "runs", jobs_json=tmp_path / "jobs.json")
    assert result["ok"] and result["total_count"] == 2 and result["total_new"] == 2
    assert result["total_cost_usd"] == 0 and pages[0]["q"] == "Python Developer"
    assert [s["data_source"] for s in result["searches"]] == ["FREEHIRE"]
    store = jobs.load(tmp_path / "jobs.json")
    assert {j["id"] for j in store["jobs"]} == {"freehire:python-java-developer-vitelco-h2azajsu",
                                                 "freehire:other-1"}
    assert all(j["safety"]["verdict"] in ("pass", "warn") for j in store["jobs"])
    raw = json.loads(next((tmp_path / "raw" / "FREEHIRE").glob("*.json")).read_text(encoding="utf-8"))
    assert raw["items"][0] == ITEM and raw["run_id"] is None and raw["input"]["countries"] == "tr"
    assert ITEM["description"] not in json.dumps(result)


def test_a_search_in_an_unknown_country_fails_alone(tmp_path):
    data = {"searches": [{**SEARCH, "id": "a", "location": "Atlantis"}]}
    result = run_searches(None, data, [FREEHIRE], raw_dir=tmp_path, runs_dir=tmp_path,
                          jobs_json=tmp_path / "jobs.json")
    assert result["ok"] is False and "not in `countries`" in result["searches"][0]["error"]


def test_cli_fetch_run_does_not_ask_for_a_token(tmp_path, monkeypatch, capsys):
    searches = tmp_path / "searches.json"
    searches.write_text(json.dumps({"searches": [SEARCH]}), encoding="utf-8")
    monkeypatch.setattr(cli, "SEARCHES_JSON", searches)
    monkeypatch.setattr(cli, "make_client", lambda: pytest.fail("freehire needs no Apify client"))
    monkeypatch.setattr(cli, "run_searches", lambda client, data, actors, date_posted: {
        "ok": True, "client": client, "sources": [a["name"] for a in actors]})
    assert cli.main(["fetch", "run", "--data-source", "FREEHIRE"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "client": None, "sources": ["FREEHIRE"]}
    assert cli.main(["source", "test", "--data-source", "FREEHIRE", "--keywords", "x",
                     "--location", "Turkey"]) == 1
    assert "use /search" in json.loads(capsys.readouterr().out)["error"]


def test_retry_pause_follows_retry_after_within_bounds():
    assert [freehire._pause(v) for v in ("3", "0", "999", None, "soon")] == [3.0, 1.0, 10.0, 2.0, 2.0]
