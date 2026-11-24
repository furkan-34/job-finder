import json

import pytest

from jobfinder.sources.actors import load, select
from jobfinder.sources.apify import SourceError


def test_registry_in_repo_is_valid():
    actors = load()
    assert "APIFY_LINKEDIN_SCRAPER" in [a["name"] for a in actors]


def test_select():
    actors = [{"name": "A"}, {"name": "B"}]
    assert select(actors, None) == actors and select(actors, []) == actors
    assert select(actors, ["all"]) == actors
    assert select(actors, ["B", "B"]) == [{"name": "B"}]
    with pytest.raises(SourceError, match="Unknown data source"):
        select(actors, ["C"])


def test_bad_registry_is_rejected(tmp_path):
    path = tmp_path / "actors.json"
    entry = {"name": "A", "actor_id": "x/y", "site": "s", "cost_per_1000_results_usd": 1,
             "input": {}, "output": {"source_id": "id", "title": "title"}}
    for actors, message in [([entry, entry], "listed twice"),
                            ([{**entry, "name": "lower"}], "UPPER_SNAKE_CASE"),
                            ([{**entry, "output": {"source_id": "id"}}], "no 'title'"),
                            ([{**entry, "output": {**entry["output"],
                                                   "salary": {"field": "s", "transform": "x"}}}],
                             "unknown transform")]:
        path.write_text(json.dumps({"actors": actors}))
        with pytest.raises(SourceError, match=message):
            load(path)
