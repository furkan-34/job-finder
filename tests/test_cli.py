import json

import pytest

from jobfinder import cli


@pytest.fixture
def profile_file(tmp_path, monkeypatch, example_profile):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(example_profile), encoding="utf-8")
    monkeypatch.setattr(cli, "PROFILE_JSON", path)
    monkeypatch.setattr(cli, "PROFILE_MD", tmp_path / "profile.md")
    return path


def test_set_then_missing(profile_file, capsys):
    assert cli.main(["profile", "set", "/meta/pending_confirmations", "[]"]) == 0
    assert cli.main(["profile", "set", "/identity/phone", "null"]) == 0
    capsys.readouterr()
    cli.main(["profile", "missing"])
    out = json.loads(capsys.readouterr().out)
    assert out == {"pending_confirmations": [], "missing": [], "complete": True}


def test_set_rejects_non_json(profile_file, capsys):
    assert cli.main(["profile", "set", "/identity/city", "Istanbul"]) == 1


def test_render_writes_profile_md(profile_file):
    assert cli.main(["profile", "render"]) == 0
    assert (profile_file.parent / "profile.md").read_text().startswith("# Candidate profile")


def test_render_refuses_invalid_profile(profile_file):
    cli.main(["profile", "set", "/preferences/dealbreakers/confirmed", "false"])
    assert cli.main(["profile", "render"]) == 1


def test_configure_then_validate_uses_saved_data_sources(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "SEARCHES_JSON", tmp_path / "searches.json")
    monkeypatch.setattr(cli, "FETCH_SETTINGS_JSON", tmp_path / "settings.json")
    monkeypatch.delenv("JOBFINDER_DATA_SOURCES", raising=False)
    (tmp_path / "searches.json").write_text(json.dumps({"searches": [
        {"id": "a", "keywords": "k", "location": "l", "limit": 10}]}))
    assert cli.main(["fetch", "configure", "--data-source", "NOPE"]) == 1
    assert cli.main(["fetch", "configure", "--data-source", "APIFY_LINKEDIN_SCRAPER",
                     "--date-posted", "pastWeek"]) == 0
    capsys.readouterr()
    cli.main(["fetch", "settings"])
    saved = json.loads(capsys.readouterr().out)
    assert saved["data_sources"] == ["APIFY_LINKEDIN_SCRAPER"] and saved["date_posted"] == "pastWeek"
    assert cli.main(["search", "validate"]) == 0
    assert json.loads(capsys.readouterr().out)["data_sources"] == ["APIFY_LINKEDIN_SCRAPER"]


def test_data_source_env_overrides_settings(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "SEARCHES_JSON", tmp_path / "searches.json")
    monkeypatch.setattr(cli, "FETCH_SETTINGS_JSON", tmp_path / "settings.json")
    (tmp_path / "searches.json").write_text(json.dumps({"searches": [
        {"id": "a", "keywords": "k", "location": "l", "limit": 10}]}))
    monkeypatch.setenv("JOBFINDER_DATA_SOURCES", "NOPE")
    assert cli.main(["search", "validate"]) == 1
    assert "Unknown data source(s): NOPE" in capsys.readouterr().out
    monkeypatch.setenv("JOBFINDER_DATA_SOURCES", "APIFY_LINKEDIN_SCRAPER")
    assert cli.main(["search", "validate"]) == 0
    assert json.loads(capsys.readouterr().out)["data_sources"] == ["APIFY_LINKEDIN_SCRAPER"]


def test_jobs_dedupe_dry_run_then_for_real(monkeypatch, capsys, tmp_path):
    path = tmp_path / "jobs.json"
    monkeypatch.setattr(cli, "JOBS_JSON", path)
    job = {"title": "Dev", "company_name": "Acme", "location": "Berlin", "status": "new"}
    path.write_text(json.dumps({"jobs": [{**job, "id": "linkedin:1"}, {**job, "id": "linkedin:2"}]}))
    assert cli.main(["jobs", "dedupe", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["removed_ids"] == ["linkedin:2"] and len(json.loads(path.read_text())["jobs"]) == 2
    assert cli.main(["jobs", "dedupe"]) == 0
    assert json.loads(capsys.readouterr().out)["after"] == 1
    assert [j["id"] for j in json.loads(path.read_text())["jobs"]] == ["linkedin:1"]


def test_jobs_import_maps_every_raw_file_once(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "JOBS_JSON", tmp_path / "jobs.json")
    monkeypatch.setattr(cli, "RAW_DIR", tmp_path / "raw")
    (tmp_path / "raw" / "APIFY_LINKEDIN_SCRAPER").mkdir(parents=True)
    item = {"id": "1", "title": "Dev", "companyName": "Acme", "location": "Berlin"}
    for stamp, items in (("20261020T080000Z", [item]),
                         ("20261021T080000Z", [item, {**item, "id": "2"}])):
        (tmp_path / "raw" / "APIFY_LINKEDIN_SCRAPER" / f"{stamp}_a.json").write_text(json.dumps(
            {"data_source": "APIFY_LINKEDIN_SCRAPER", "search_id": "a", "items": items}))
    assert cli.main(["jobs", "import"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["total_new"], out["total_already_saved"], out["total_reposts"]) == (1, 1, 1)
    stored = json.loads((tmp_path / "jobs.json").read_text())["jobs"]
    assert [j["id"] for j in stored] == ["linkedin:1"]
