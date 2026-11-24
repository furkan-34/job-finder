import copy
import json

import pytest

from jobfinder import safety
from jobfinder.paths import ROOT


@pytest.fixture
def example_profile() -> dict:
    data = json.loads((ROOT / "examples" / "profile.example.json").read_text(encoding="utf-8"))
    return copy.deepcopy(data)


@pytest.fixture(autouse=True)
def no_downloaded_model(monkeypatch):
    """Tests never load the classifier in data/safety/model/; they pass a fake scorer."""
    monkeypatch.setattr(safety, "load_scorer", lambda rules: None)
