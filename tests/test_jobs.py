import json
from datetime import UTC, datetime

from jobfinder import jobs
from jobfinder.sources import mapping
from jobfinder.sources.actors import load

NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
ACTOR = next(a for a in load() if a["name"] == "APIFY_LINKEDIN_SCRAPER")
ITEM = {"id": 4012, "link": "https://www.linkedin.com/jobs/view/4012", "title": "Backend Engineer",
        "companyName": "Acme", "location": "Istanbul, Turkey", "salaryInfo": ["$80K", "$100K"],
        "workplaceTypes": ["Hybrid"], "employmentType": "Full-time",
        "seniorityLevel": "Mid-Senior level", "postedAt": "2026-10-22",
        "applicantsCount": "37", "descriptionText": "Requirements:\n- Python"}


def test_linkedin_item_maps_to_the_standard_shape():
    job = mapping.to_job(ITEM, ACTOR, NOW, "backend-istanbul", "fetch/raw/x.json")
    assert jobs.validate(job) == []
    assert job["id"] == "linkedin:4012"
    assert job["data_source"] == "APIFY_LINKEDIN_SCRAPER"  # the actor's name in the registry
    assert job["work_modes"] == ["hybrid"] and job["salary"] == "$80K - $100K"
    assert job["description"] == ITEM["descriptionText"]  # kept as is; read in stage 2d
    assert job["status"] == "new" and job["created_at"] == "2026-10-23T08:00:00+00:00"


def test_data_source_comes_from_the_actor_entry():
    other = {**ACTOR, "name": "APIFY_LINKEDIN_OTHER"}
    job = mapping.to_job(ITEM, other, NOW)
    assert job["data_source"] == "APIFY_LINKEDIN_OTHER" and job["id"] == "linkedin:4012"


def test_sparse_item_still_valid_and_item_without_id_dropped():
    job = mapping.to_job({"id": "9", "title": "Dev"}, ACTOR, NOW)
    assert jobs.validate(job) == [] and job["work_modes"] == []
    assert mapping.to_job({"title": "no id"}, ACTOR, NOW) is None


def test_add_skips_known_ids_and_save_roundtrips(tmp_path):
    store = jobs.load(tmp_path / "jobs.json")
    a = mapping.to_job(ITEM, ACTOR, NOW)
    b = mapping.to_job({**ITEM, "id": "5", "title": "Data Engineer"}, ACTOR, NOW)
    assert jobs.add(store, [a, a, b]) == (2, 1, 0)
    jobs.save(store, tmp_path / "jobs.json")
    again = jobs.load(tmp_path / "jobs.json")
    assert [j["id"] for j in again["jobs"]] == ["linkedin:4012", "linkedin:5"]
    assert again["updated_at"] and not (tmp_path / "jobs.json.tmp").exists()


def test_fingerprint_ignores_case_accents_punctuation_and_legal_form():
    a = {"company_name": "Ünal Yazılım A.Ş.", "title": "Backend Engineer (m/w/d)",
         "location": "İstanbul, Istanbul, Türkiye"}
    b = {"company_name": "UNAL YAZILIM", "title": "backend engineer",
         "location": "Istanbul, Turkey"}
    assert jobs.fingerprint(a) == jobs.fingerprint(b) == "unal yazilim|backend engineer|istanbul"
    assert jobs.fingerprint({"company_name": "Acme Holding Ltd. Şti.", "title": "Dev"}) == "acme|dev|"
    assert jobs.fingerprint({**b, "location": "Ankara, Turkey"}) != jobs.fingerprint(b)
    assert jobs.fingerprint({**b, "title": "Senior Backend Engineer"}) != jobs.fingerprint(b)
    assert jobs.fingerprint({"title": "Dev"}) is None  # no company: never matched


def test_add_skips_reposts_under_a_new_id():
    store = {"jobs": [mapping.to_job(ITEM, ACTOR, NOW)]}
    repost = mapping.to_job({**ITEM, "id": "777", "companyName": "ACME Inc."}, ACTOR, NOW)
    anonymous = [mapping.to_job({"id": str(i), "title": "Dev"}, ACTOR, NOW) for i in (1, 2)]
    assert jobs.add(store, [repost, *anonymous]) == (2, 0, 1)
    assert [j["id"] for j in store["jobs"]] == ["linkedin:4012", "linkedin:1", "linkedin:2"]


def test_deleted_posting_stays_as_a_marker_so_a_search_does_not_add_it_again():
    first = mapping.to_job(ITEM, ACTOR, NOW)
    store = {"jobs": [first]}
    assert jobs.delete(store, [first["id"], "linkedin:9", first["id"]]) == {"deleted": [first["id"]], "unknown": ["linkedin:9"]}
    assert store["jobs"][0]["status"] == "deleted" and jobs.validate(store["jobs"][0]) == []
    repost = mapping.to_job({**ITEM, "id": "777"}, ACTOR, NOW)  # same company, title and city under a new id
    assert jobs.add(store, [mapping.to_job(ITEM, ACTOR, NOW), repost]) == (0, 1, 1)
    assert len(store["jobs"]) == 1


def test_dedupe_removes_later_reposts_that_are_still_new():
    first = mapping.to_job(ITEM, ACTOR, NOW)
    worked_on = {**mapping.to_job({**ITEM, "id": "2"}, ACTOR, NOW), "status": "shortlist"}
    repost = mapping.to_job({**ITEM, "id": "3"}, ACTOR, NOW)
    store = {"jobs": [first, worked_on, repost]}
    assert jobs.dedupe(store) == ["linkedin:3"]
    assert [j["id"] for j in store["jobs"]] == ["linkedin:4012", "linkedin:2"]


def test_one_mapper_reads_any_actor_from_its_output_entry():
    actor = {"name": "APIFY_OTHER", "site": "other", "input": {"q": "{keywords}", "n": "{limit}",
                                                              "fixed": True},
             "output": {"source_id": "job.key", "title": "job.name",
                        "company_name": "employer.name",
                        "work_modes": {"field": "remote", "transform": "work_modes"}}}
    item = {"job": {"key": 7, "name": "Dev"}, "employer": {"name": "Acme"}, "remote": "Remote"}
    job = mapping.to_job(item, actor, NOW)
    assert jobs.validate(job) == []
    assert (job["id"], job["title"], job["company_name"], job["work_modes"]) == (
        "other:7", "Dev", "Acme", ["remote"])
    assert mapping.build_input(actor, "Dev", "Berlin", 25, "anyTime") == {
        "q": "Dev", "n": 25, "fixed": True}


def test_import_raw_maps_a_raw_file_and_reports_invalid_items(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"data_source": "APIFY_LINKEDIN_SCRAPER", "search_id": "s",
                               "items": [ITEM, {"title": "no id"}, {"id": "5", "title": "Dev",
                                                                   "applicantsCount": [1]}]}))
    store = {"jobs": []}
    result = jobs.import_raw(store, raw, [ACTOR])
    assert (result["count"], result["new"], result["invalid"]) == (3, 2, 1)
    assert result["first_invalid"] == "item has no id"
    assert [j["search_id"] for j in store["jobs"]] == ["s", "s"]
    again = jobs.import_raw(store, raw, [ACTOR])
    assert (again["new"], again["already_saved"]) == (0, 2)
    assert "error" in jobs.import_raw(store, raw, [{**ACTOR, "name": "APIFY_ELSE"}])


def test_for_agent_closes_up_and_cuts_a_description():
    from jobfinder.jobs import for_agent
    text = "Requirements:\n\n\n  Python   and Go  \nWe are an equal opportunity employer, all welcome.\n" \
           "We are an equal opportunity employer, all welcome.\n"
    assert for_agent(text) == "Requirements:\n\nPython and Go\nWe are an equal opportunity employer, all welcome."
    assert for_agent(None) is None
    cut = for_agent("word " * 2000, limit=100)
    assert len(cut) <= 102 and cut.endswith(" …") and "wor …" not in cut
