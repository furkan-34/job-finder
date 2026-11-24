import json
from datetime import UTC, datetime

import pytest
from test_sources import FakeClient

from jobfinder import jobs
from jobfinder.fetch.runner import run_searches
from jobfinder.sources import actors as registry
from jobfinder.sources.apify import SourceError
from jobfinder.sources.mapping import build_input, to_job

ENTRIES = {a["name"]: a for a in registry.load()}
INDEED = ENTRIES["APIFY_INDEED_UK_SCRAPER"]
TOTALJOBS = ENTRIES["APIFY_TOTALJOBS_SCRAPER"]
# Shaped like the field names on each actor's Apify page (not yet compared with a live
# run), with short made-up text.
INDEED_ITEM = {
    "key": "abc123def456", "url": "https://uk.indeed.com/viewjob?jk=abc123def456",
    "jobUrl": "https://uk.indeed.com/viewjob?jk=abc123def456", "title": "Backend Engineer",
    "datePublished": "2026-10-20T09:15:00Z", "dateOnIndeed": "2026-10-19T08:00:00Z",
    "location": {"countryCode": "GB", "city": "London", "postalCode": "EC1A"},
    "employer": {"name": "Acme Ltd", "companyPageUrl": "https://uk.indeed.com/cmp/Acme",
                 "corporateWebsite": "https://acme.example", "employeesCount": "201 to 500"},
    "jobTypes": {"CF3CP": "Full-time"}, "baseSalary": {"min": 45000, "max": 55000,
                                                       "unitOfWork": "YEAR", "currencyCode": "GBP"},
    "description": {"text": "Python and Postgres, 3 years.", "html": "<p>Python and Postgres.</p>"}}
TOTALJOBS_ITEM = {
    "id": "107251000", "title": "Platform Engineer", "url": "https://www.totaljobs.com/job/platform-engineer/acme-ltd-job107251000",
    "datePosted": "2026-10-21T07:00:00Z", "location": "London", "postCode": "EC1A",
    "company": {"id": "9", "name": "Acme Ltd", "url": "https://www.totaljobs.com/jobs/acme-ltd",
                "website": "https://acme.example", "employees": "201-500"},
    "textSnippet": "Kubernetes...", "workType": "Permanent", "salary": "£60,000 - £70,000 per annum",
    "description": "<p>Kubernetes and Terraform.</p><ul><li>3 years</li></ul>"}
SEARCH = {"id": "be-ldn", "keywords": "Backend Engineer hybrid", "location": "London, United Kingdom",
          "limit": 3}


def test_both_are_part_of_all():
    names = [a["name"] for a in registry.select(registry.load(), ["all"])]
    assert "APIFY_INDEED_UK_SCRAPER" in names and "APIFY_TOTALJOBS_SCRAPER" in names
    assert registry.uses_apify([INDEED, TOTALJOBS]) and "kind" not in INDEED and "kind" not in TOTALJOBS


def test_indeed_input_searches_the_city_in_the_uk():
    assert build_input(INDEED, "Backend Engineer hybrid", "London, United Kingdom", 25, "pastWeek") == {
        "country": "uk", "title": "Backend Engineer", "location": "London", "limit": 25, "datePosted": "7"}
    assert build_input(INDEED, "Go", "UK", 5, "past24Hours")["location"] == ""


def test_totaljobs_input_searches_the_city_in_the_uk():
    assert build_input(TOTALJOBS, "Backend Engineer remote", "Leeds, England", 25, "past24Hours") == {
        "keywords": "Backend Engineer", "location": "Leeds", "radius": "10", "postedWithin": "1",
        "limit": 25}


@pytest.mark.parametrize("entry", [INDEED, TOTALJOBS])
def test_any_time_sends_no_date_filter(entry):
    run_input = build_input(entry, "Python", "London, UK", 5, "anyTime")
    assert "datePosted" not in run_input and "postedWithin" not in run_input


@pytest.mark.parametrize("entry", [INDEED, TOTALJOBS])
@pytest.mark.parametrize("keywords, location, date_posted, message", [
    ("Python", "Berlin, Germany", "anyTime", "'Germany' is not in `location_countries`"),
    ("Python", "Worldwide", "anyTime", "not in `location_countries`"),
    ("remote", "London, UK", "anyTime", "leaves out"),
    ("Python", "London, UK", "pastMonth", "has no 'pastMonth' filter")])
def test_input_refuses_what_it_would_have_to_guess(entry, keywords, location, date_posted, message):
    with pytest.raises(SourceError, match=message):
        build_input(entry, keywords, location, 5, date_posted)


def test_indeed_item_maps_to_a_valid_job():
    job = to_job(INDEED_ITEM, INDEED, datetime.now(UTC))
    assert job["id"] == "indeed:abc123def456" and job["data_source"] == "APIFY_INDEED_UK_SCRAPER"
    assert job["company_name"] == "Acme Ltd" and job["location"] == "London"
    assert job["company_website"] == "https://acme.example"
    assert job["description"] == "Python and Postgres, 3 years."
    assert job["posted_at"] == INDEED_ITEM["datePublished"]
    assert jobs.validate(job) == []


def test_totaljobs_item_maps_to_a_valid_job():
    job = to_job(TOTALJOBS_ITEM, TOTALJOBS, datetime.now(UTC))
    assert job["id"] == "totaljobs:107251000" and job["company_name"] == "Acme Ltd"
    assert job["employment_type"] == "Permanent" and job["salary"] == TOTALJOBS_ITEM["salary"]
    assert job["description"] == "Kubernetes and Terraform.\n3 years"
    assert job["apply_url"] == job["url"] == TOTALJOBS_ITEM["url"]
    assert jobs.validate(job) == []


@pytest.mark.parametrize("entry, item, actor_id, new_id", [
    (INDEED, INDEED_ITEM, "valig/indeed-jobs-scraper", "indeed:abc123def456"),
    (TOTALJOBS, TOTALJOBS_ITEM, "valig/totaljobs-jobs-scraper", "totaljobs:107251000")])
def test_run_searches_stores_postings(tmp_path, entry, item, actor_id, new_id):
    client = FakeClient(items=[item])
    result = run_searches(client, {"searches": [SEARCH]}, [entry], "pastWeek", raw_dir=tmp_path / "raw",
                          runs_dir=tmp_path / "runs", jobs_json=tmp_path / "jobs.json")
    assert result["ok"] and result["total_new"] == 1
    called_id, kwargs = client.calls[0]
    assert called_id == actor_id and kwargs["max_items"] == 3 and kwargs["run_input"]["location"] == "London"
    store = jobs.load(tmp_path / "jobs.json")
    assert [j["id"] for j in store["jobs"]] == [new_id]
    assert store["jobs"][0]["safety"]["verdict"] in ("pass", "warn")
    raw = json.loads(next((tmp_path / "raw" / entry["name"]).glob("*.json")).read_text(encoding="utf-8"))
    assert raw["items"] == [item]
    assert "Kubernetes" not in json.dumps(result) and "Postgres" not in json.dumps(result)


def test_a_search_outside_the_uk_fails_alone(tmp_path):
    data = {"searches": [{**SEARCH, "id": "de", "location": "Berlin, Germany"}, SEARCH]}
    result = run_searches(FakeClient(items=[INDEED_ITEM]), data, [INDEED], raw_dir=tmp_path,
                          runs_dir=tmp_path, jobs_json=tmp_path / "jobs.json")
    assert "location_countries" in result["searches"][0]["error"]
    assert result["searches"][1]["new"] == 1
