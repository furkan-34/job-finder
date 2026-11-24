from jobfinder.searches import summary, validate

SEARCH = {"id": "backend-istanbul", "keywords": "Senior Backend Engineer hybrid",
          "location": "Istanbul, Turkey", "limit": 25}


def test_valid_file_and_summary():
    data = {"searches": [SEARCH, {**SEARCH, "id": "off", "enabled": False}]}
    actors = [{"name": "A", "cost_per_1000_results_usd": 1.0},
              {"name": "B", "cost_per_1000_results_usd": 3.0}]
    assert validate(data) == []
    assert summary(data, actors) == {"searches": [SEARCH], "data_sources": ["A", "B"],
                                     "max_postings_per_run": 50, "max_cost_per_run_usd": 0.1}


def test_reports_each_problem():
    data = {"searches": [
        {**SEARCH, "id": "Bad ID", "limit": 0},
        {**SEARCH, "keywords": " "},
        {**SEARCH, "limit": True},
    ]}
    assert validate(data) == [
        "/searches/0/id: lowercase letters, digits and '-' only",
        "/searches/0/limit: must be an integer from 1 to 1000",
        "/searches/1/keywords: must be a non-empty string",
        "/searches/2/id: duplicate 'backend-istanbul'",
        "/searches/2/limit: must be an integer from 1 to 1000",
    ]


def test_needs_an_enabled_search():
    assert validate({"searches": [{**SEARCH, "enabled": False}]}) == ["no enabled searches"]
    assert validate({}) == ["top level must be an object with a 'searches' list"]
