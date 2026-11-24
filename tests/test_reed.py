import json
from datetime import UTC, datetime

import pytest
from test_sources import FakeClient

from jobfinder import jobs
from jobfinder.fetch.runner import run_searches
from jobfinder.sources import actors as registry
from jobfinder.sources.apify import SourceError
from jobfinder.sources.mapping import build_input, to_job

REED = next(a for a in registry.load() if a["name"] == "APIFY_REED_SCRAPER")
# Shaped like the actor's example output on its Apify page (field names and types as
# documented there, not yet compared with a live run), with short made-up text.
ITEM = {"job_id": "56972754", "job_url": "https://www.reed.co.uk/jobs/backend-engineer/56972754",
        "title": "Backend Engineer", "description_text": "Python and Postgres, 3 years.",
        "description_html": "<p>Python and Postgres, 3 years.</p>",
        "salary_min": 45000, "salary_max": 55000, "salary_currency": "GBP",
        "salary_period": "per annum", "salary_display": "£ 45,000 - £ 55,000 per annum",
        "location": "London", "town": "London", "county": "Greater London", "country_code": "GB",
        "remote_working": "Hybrid", "location_type": "hybrid", "is_remote": False,
        "contract_type": "Permanent", "is_full_time": True, "recruiter_name": "Acme Ltd",
        "recruiter_type": "employer", "recruiter_profile_url": "https://www.reed.co.uk/jobs/acme-ltd/p1",
        "posted_date": "2026-10-20T09:15:00", "expiry_date": "2026-11-30T00:00:00",
        "applications": None, "is_easy_apply": True, "external_url": None,
        "is_external_redirect": False, "scraped_at": "2026-10-26T10:00:00Z"}
SEARCH = {"id": "be-ldn", "keywords": "Backend Engineer hybrid", "location": "London, United Kingdom",
          "limit": 3}


def test_reed_is_part_of_all():
    assert "kind" not in REED and registry.uses_apify([REED])
    assert "APIFY_REED_SCRAPER" in [a["name"] for a in registry.select(registry.load(), ["all"])]


def test_input_drops_work_mode_words_and_searches_the_city():
    assert build_input(REED, "Backend Engineer hybrid", "London, United Kingdom", 25, "pastWeek") == {
        "keywords": "Backend Engineer", "location": "London", "proximity": 10,
        "datePosted": "lastweek", "maxItems": 25, "fetchDetails": True, "monitorMode": False}
    # a country-only location searches the whole UK
    assert build_input(REED, "Go", "UK", 5, "anyTime")["location"] == ""
    assert build_input(REED, "Go", "Leeds, England", 5, "past24Hours")["datePosted"] == "today"


@pytest.mark.parametrize("keywords, location, date_posted, message", [
    ("Python", "Berlin, Germany", "anyTime", "'Germany' is not in `location_countries`"),
    ("Python", "Worldwide", "anyTime", "not in `location_countries`"),
    ("remote", "London, UK", "anyTime", "only words APIFY_REED_SCRAPER leaves out"),
    ("Python", "London, UK", "pastMonth", "has no 'pastMonth' filter")])
def test_input_refuses_what_it_would_have_to_guess(keywords, location, date_posted, message):
    with pytest.raises(SourceError, match=message):
        build_input(REED, keywords, location, 5, date_posted)


def test_item_maps_to_a_valid_job():
    job = to_job(ITEM, REED, datetime.now(UTC))
    assert job["id"] == "reed:56972754" and job["data_source"] == "APIFY_REED_SCRAPER"
    assert job["company_name"] == "Acme Ltd" and job["work_modes"] == ["hybrid"]
    assert job["description"] == ITEM["description_text"] and job["posted_at"] == ITEM["posted_date"]
    # easy apply: no external link, so the Reed page is the apply link
    assert job["apply_url"] == job["url"] == ITEM["job_url"]
    assert jobs.validate(job) == []


def test_an_external_apply_link_is_kept():
    item = {**ITEM, "external_url": "https://acme.example/careers/1", "is_easy_apply": False}
    assert to_job(item, REED, datetime.now(UTC))["apply_url"] == "https://acme.example/careers/1"


def test_run_searches_stores_reed_postings(tmp_path):
    client = FakeClient(items=[ITEM, {**ITEM, "job_id": "2", "title": "Platform Engineer"}])
    result = run_searches(client, {"searches": [SEARCH]}, [REED], "pastWeek", raw_dir=tmp_path / "raw",
                          runs_dir=tmp_path / "runs", jobs_json=tmp_path / "jobs.json")
    assert result["ok"] and result["total_new"] == 2
    actor_id, kwargs = client.calls[0]
    assert actor_id == "scrapersdelight/reed-jobs-scraper" and kwargs["max_items"] == 3
    assert kwargs["run_input"]["location"] == "London" and kwargs["run_input"]["datePosted"] == "lastweek"
    store = jobs.load(tmp_path / "jobs.json")
    assert {j["id"] for j in store["jobs"]} == {"reed:56972754", "reed:2"}
    assert all(j["safety"]["verdict"] in ("pass", "warn") for j in store["jobs"])
    raw = json.loads(next((tmp_path / "raw" / "APIFY_REED_SCRAPER").glob("*.json")).read_text(encoding="utf-8"))
    assert raw["items"][0] == ITEM
    assert ITEM["description_text"] not in json.dumps(result)


def test_a_search_outside_the_uk_fails_alone(tmp_path):
    data = {"searches": [{**SEARCH, "id": "de", "location": "Berlin, Germany"}, SEARCH]}
    result = run_searches(FakeClient(items=[ITEM]), data, [REED], raw_dir=tmp_path, runs_dir=tmp_path,
                          jobs_json=tmp_path / "jobs.json")
    assert "location_countries" in result["searches"][0]["error"]
    assert result["searches"][1]["new"] == 1
