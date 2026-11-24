import json
from datetime import timedelta
from pathlib import Path

from jobfinder import apply_test, send
from test_send import NOW, RULES, SETTINGS, answer, data, job  # noqa: F401 - data is a fixture

TEST = {"commit": "abc1234", "changed": False, "sender_model": "sonnet"}
TOKENS = {"input": 100, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 800, "output": 100}


def run_test(store, profile, data, now, submitted=True):
    made = send.make(store, profile, "linkedin:1", RULES, SETTINGS, data / "apply", data, now, test=TEST)
    answer(made, status="ready_to_submit", filled=[], uploaded=["cv"])
    assert send.step(store, RULES, SETTINGS, data / "apply", now)["next"] == "submit"  # nothing to confirm
    if submitted:
        answer(made, status="submitted", url="https://www.linkedin.com/jobs/view/1/done")
    else:
        answer(made, status="stopped", reason="captcha")
    result = send.step(store, RULES, SETTINGS, data / "apply", now + timedelta(minutes=4))
    return made, result


def agent_run(input_path, tokens=TOKENS, cost=0.01):
    return {"agent_type": "job-sender", "input": input_path, "root": "/", "requests": 5,
            "tokens": tokens, "cost_usd": cost, "by_model": {"claude-sonnet-5-5": {}},
            "started_at": NOW.isoformat(), "ended_at": NOW.isoformat()}


def test_a_test_run_is_sent_and_the_posting_can_be_sent_again(data, example_profile):
    store = {"jobs": [job()]}
    made, result = run_test(store, example_profile, data, NOW)
    assert result["result"] == "submitted" and result["test"] is True
    assert store["jobs"][0]["status"] == "cv_ready" and "application" not in store["jobs"][0]
    assert send.sent_on(store, NOW.date()) == 0
    folder = Path(made["input_path"]).parent
    assert folder.parent.name == send.TESTS_FOLDER
    test = json.loads((folder / send.TEST_FILE).read_text())
    assert test["commit"] == "abc1234" and test["result"] == "submitted" and test["steps"] == 2
    store["jobs"][0]["status"] = "applied"  # even marked by hand or by /track
    again, result = run_test(store, example_profile, data, NOW + timedelta(hours=1))
    assert result["result"] == "submitted" and again["input_path"] != made["input_path"]


def test_choice_and_pick(data):
    store = {"jobs": [job(status="applied")]}
    path = data / "apply" / "test.json"
    assert apply_test.choice(store, path) == {"job_id": None}
    apply_test.pick(store, "linkedin:1", path, NOW)
    chosen = apply_test.choice(store, path)
    assert chosen["job_id"] == "linkedin:1" and chosen["blockers"] != ["status:applied"]


def test_report_compares_with_the_previous_and_the_first(data, example_profile):
    store = {"jobs": [job()]}
    first, _ = run_test(store, example_profile, data, NOW)
    second, _ = run_test(store, example_profile, data, NOW + timedelta(hours=1), submitted=False)
    usage = {"runs": {
        "s/1": agent_run(first["input_path"]),
        "s/2": agent_run(second["input_path"], {**TOKENS, "cache_read": 300}, 0.005),
        "s/main/0": {"agent_type": "main", "command": "/test-apply", "requests": 3, "tokens": TOKENS,
                     "cost_usd": 0.02, "started_at": (NOW - timedelta(minutes=1)).isoformat(),
                     "ended_at": (NOW + timedelta(minutes=5)).isoformat()},
        "s/other": agent_run(str(data / "apply" / "24-10-2026" / "x" / "input.json"))}}
    report = apply_test.report(usage, data / "apply")
    assert report["count"] == 2
    one, two = report["tests"]
    assert one["agent"]["total_tokens"] == 1000 and one["command"]["total_tokens"] == 1000
    assert one["minutes"] == 4.0 and one["vs_previous"] is None
    assert two["result"] == "stopped" and two["reason"] == "captcha"
    assert two["command"]["runs"] == 0  # its turn has not ended yet
    assert two["vs_previous"]["total_tokens"] == -500
    assert two["vs_previous"]["total_tokens_percent"] == -50.0
    assert two["vs_first"]["cost_percent"] == -50.0
