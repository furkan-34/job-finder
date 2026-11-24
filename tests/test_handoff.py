import json
from pathlib import Path

import pytest
from test_score import EXTRACTED, JOB, NOW

from jobfinder import handoff, jobs, send
from jobfinder.score import batches

FIELDS = {k: v for k, v in EXTRACTED.items() if k not in ("model", "extracted_at")}


@pytest.fixture
def data(tmp_path, monkeypatch, example_profile):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(example_profile), encoding="utf-8")
    jobs_path = tmp_path / "jobs.json"
    jobs.save({"jobs": [{**JOB, "id": "a", "filter": {"passed": True, "reasons": [],
                                                    "checked_at": NOW.isoformat()}}]}, jobs_path)
    for name, value in [("PROFILE_JSON", profile_path), ("JOBS_JSON", jobs_path),
                        ("SCORE_SETTINGS_JSON", tmp_path / "settings.json"),
                        ("SCORE_BATCHES_DIR", tmp_path / "batches"),
                        ("SCORE_RESULTS_DIR", tmp_path / "results")]:
        monkeypatch.setattr(handoff, name, value)
    made = batches.make(jobs.load(jobs_path), "extract", 10, tmp_path / "batches", tmp_path / "results")
    return {"jobs": jobs_path, "batch": made["batches"][0]}


def event(prompt: str, text: str, agent: str = "job-extractor", status: str = "completed") -> dict:
    return {"hook_event_name": "PostToolUse", "tool_name": "Agent",
            "tool_input": {"subagent_type": agent, "prompt": prompt, "description": "x"},
            "tool_response": {"status": status, "agentId": "a1", "totalTokens": 9,
                              "content": [{"type": "text", "text": text}]}}


def replaced(output: dict) -> dict:
    response = output["hookSpecificOutput"]["updatedToolOutput"]
    assert response["agentId"] == "a1" and response["totalTokens"] == 9  # the rest is kept
    return json.loads(response["content"][0]["text"])


def test_extract_answer_is_saved_and_replaced_by_the_save_output(data):
    answer = "Here it is.\n```json\n" + json.dumps({"jobs": [{"id": "a", **FIELDS}]}) + "\n```\n"
    result = replaced(handoff.handle(event(f"Batch file: {data['batch']['path']}", answer), NOW))
    assert result["saved_by"] == "jobfinder.handoff" and result["ok"] and result["saved"] == 1
    assert "Python" not in json.dumps(result)  # counts and paths, no value from the answer
    assert json.loads(Path(data["batch"]["result_path"]).read_text())["jobs"][0]["id"] == "a"
    assert jobs.load(data["jobs"])["jobs"][0]["extracted"]["industry"] == "Financial Services"


def test_a_bad_answer_or_prompt_is_reported_not_raised(data, tmp_path):
    result = replaced(handoff.handle(event(f"Batch file: {data['batch']['path']}", "no json"), NOW))
    assert result["ok"] is False and "not JSON" in result["error"]
    elsewhere = tmp_path / "other" / "extract-1.json"
    result = replaced(handoff.handle(event(f"Batch file: {elsewhere}", "{}"), NOW))
    assert result["ok"] is False and "not a batch file" in result["error"]
    assert replaced(handoff.handle(event("Do something", "{}"), NOW))["ok"] is False


def test_other_agents_and_background_launches_are_left_alone(data):
    assert handoff.handle(event("Batch file: x.json", "{}", agent="Explore")) is None
    assert handoff.handle(event("Batch file: x.json", "{}", status="async_launched")) is None
    assert handoff.handle({"hook_event_name": "PostToolUse", "tool_name": "Read"}) is None


def test_sender_answer_is_written_to_the_open_run(tmp_path, monkeypatch):
    folder = tmp_path / "run"
    folder.mkdir()
    (tmp_path / send.APPLY_RUN_JSON.name).write_text(json.dumps({"id": "a", "folder": str(folder)}))
    path = handoff.write_sender_answer('```json\n{"id": "a", "status": "stopped"}\n```', tmp_path)
    assert path == folder / "answer.json"
    assert json.loads(path.read_text()) == {"id": "a", "status": "stopped"}
    (tmp_path / send.APPLY_RUN_JSON.name).unlink()
    assert handoff.write_sender_answer('{"id": "a"}', tmp_path) is None  # no run open


def test_review_answer_goes_to_review_json_and_is_saved_as_a_review(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(handoff, "CV_DIR", tmp_path / "cv")
    monkeypatch.setattr(handoff, "save_cv", lambda inp, ans, now, **kw: calls.append((ans.name, kw["reviewed"]))
                        or {"ok": True})
    folder = tmp_path / "cv" / "24-10-2026" / "acme-1"
    folder.mkdir(parents=True)
    for agent, name, reviewed in (("cv-writer", "answer.json", False), ("cv-reviewer", "review.json", True)):
        result = replaced(handoff.handle(event(f"Input file: {folder / 'input.json'}", "```json\n{}\n```",
                                               agent=agent), NOW))
        assert result["ok"] and result["answer_path"] == str(folder / name)
        assert (folder / name).read_text() == "{}\n"
    assert calls == [("answer.json", False), ("review.json", True)]
    result = replaced(handoff.handle(event(f"Input file: {tmp_path / 'input.json'}", "{}", agent="cv-reviewer"), NOW))
    assert result["ok"] is False and "not a cv-reviewer input" in result["error"]
