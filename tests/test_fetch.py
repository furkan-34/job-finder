import json

from test_sources import ITEM, FakeClient

from jobfinder.fetch.runner import run_searches
from jobfinder.sources.actors import load

LINKEDIN = load()[0]

DATA = {"searches": [
    {"id": "a", "keywords": "Backend Engineer", "location": "Istanbul", "limit": 10},
    {"id": "b", "keywords": "Platform Engineer", "location": "Berlin", "limit": 20},
    {"id": "off", "keywords": "x", "location": "y", "limit": 5, "enabled": False},
]}
OTHER = {**ITEM, "id": "2", "title": "Platform Engineer"}


def run(client, tmp_path, date_posted="anyTime", actors=(LINKEDIN,)):
    return run_searches(client, DATA, list(actors), date_posted, raw_dir=tmp_path / "raw",
                        runs_dir=tmp_path / "runs", jobs_json=tmp_path / "jobs.json")


def test_runs_enabled_searches_and_writes_summary(tmp_path):
    client = FakeClient()
    result = run(client, tmp_path)

    assert [kwargs["run_input"]["keywords"] for _, kwargs in client.calls] == [
        "Backend Engineer", "Platform Engineer"]
    assert [kwargs["max_items"] for _, kwargs in client.calls] == [10, 20]
    assert all(kwargs["run_input"]["datePosted"] == "anyTime" for _, kwargs in client.calls)
    assert result["ok"] and result["total_count"] == 2 and result["total_cost_usd"] == 0.01

    saved = json.loads(open(result["run_path"], encoding="utf-8").read())
    assert [(s["data_source"], s["search_id"]) for s in saved["searches"]] == [
        ("APIFY_LINKEDIN_SCRAPER", "a"), ("APIFY_LINKEDIN_SCRAPER", "b")]
    assert ITEM["descriptionText"] not in json.dumps(saved)
    raw = json.loads(open(saved["searches"][0]["raw_path"], encoding="utf-8").read())
    assert raw["search_id"] == "a" and raw["items"] == [ITEM]
    assert "/APIFY_LINKEDIN_SCRAPER/" in saved["searches"][0]["raw_path"]


def test_each_chosen_actor_runs_every_search_and_tags_its_postings(tmp_path):
    second = {**LINKEDIN, "name": "APIFY_SECOND", "actor_id": "someone/other-scraper"}
    client = FakeClient(items=(ITEM,))
    result = run(client, tmp_path, actors=(second, LINKEDIN))
    assert [actor_id for actor_id, _ in client.calls] == [
        "someone/other-scraper", "someone/other-scraper", LINKEDIN["actor_id"], LINKEDIN["actor_id"]]
    store = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    # Same site and id: stored once, tagged with the actor that found it first.
    assert [(j["id"], j["data_source"]) for j in store["jobs"]] == [("linkedin:1", "APIFY_SECOND")]
    assert result["total_new"] == 1


def test_postings_go_to_one_store_and_are_never_written_twice(tmp_path):
    # Both searches return the same posting: only the first adds it.
    first = run(FakeClient(), tmp_path)
    assert [(s["new"], s["already_saved"]) for s in first["searches"]] == [(1, 0), (0, 1)]
    store = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert [job["id"] for job in store["jobs"]] == ["linkedin:1"]
    assert store["jobs"][0]["search_id"] == "a"

    # A later stage changes the entry; the next fetch must not undo it.
    store["jobs"][0]["status"] = "shortlist"
    (tmp_path / "jobs.json").write_text(json.dumps(store), encoding="utf-8")
    second = run(FakeClient(items=(ITEM, OTHER)), tmp_path)
    assert second["total_new"] == 1
    store = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert [(job["id"], job["status"]) for job in store["jobs"]] == [
        ("linkedin:1", "shortlist"), ("linkedin:2", "new")]


def test_one_failed_search_does_not_stop_the_others(tmp_path):
    class FlakyClient(FakeClient):
        def actor(self, actor_id):
            ns = super().actor(actor_id)
            inner = ns.call

            def call(**kwargs):
                if kwargs["run_input"]["keywords"] == "Backend Engineer":
                    raise ConnectionError("timeout")
                return inner(**kwargs)
            ns.call = call
            return ns

    result = run(FlakyClient(), tmp_path, "pastWeek")
    assert result["ok"]
    assert "Could not reach Apify" in result["searches"][0]["error"]
    assert result["searches"][1]["count"] == 1


def test_refuses_when_credit_is_short(tmp_path):
    big = {"searches": [{"id": c, "keywords": "k", "location": "l", "limit": 1000} for c in "abcd"]}
    client = FakeClient()  # $3.75 left
    result = run_searches(client, big, [LINKEDIN], raw_dir=tmp_path, runs_dir=tmp_path,
                          jobs_json=tmp_path / "jobs.json")
    assert not result["ok"] and "Not enough Apify credit" in result["error"]
    assert client.calls == []
    assert json.loads(open(result["run_path"]).read())["error"] == result["error"]
    assert not (tmp_path / "jobs.json").exists()


def test_a_repost_under_a_new_id_is_counted_not_stored(tmp_path):
    run(FakeClient(), tmp_path)
    result = run(FakeClient(items=({**ITEM, "id": "99"},)), tmp_path)
    assert [(s["new"], s["already_saved"], s["reposts"]) for s in result["searches"]] == [
        (0, 0, 1), (0, 0, 1)]
    assert result["total_new"] == 0 and result["total_reposts"] == 2
    store = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert [job["id"] for job in store["jobs"]] == ["linkedin:1"]


def test_new_postings_get_the_safety_check(tmp_path):
    # ITEM's description is a prompt injection.
    clean = {**ITEM, "id": "2", "title": "Platform Engineer", "descriptionText": "Python, Go."}
    scam = {**ITEM, "id": "3", "title": "Data Entry", "descriptionText": "Application fee: $30"}
    result = run(FakeClient(items=(ITEM, clean, scam)), tmp_path)
    assert result["safety"] == {"checked": 3, "pass": 1, "warn": 0, "block": 2,
                                "reasons": {"fee_request": 1, "prompt_injection": 1}, "model": False}
    store = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert [j["safety"]["verdict"] for j in store["jobs"]] == ["block", "pass", "block"]
    assert run(FakeClient(items=(ITEM,)), tmp_path)["safety"]["checked"] == 0
