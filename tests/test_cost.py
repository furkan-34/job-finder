import io
import json
from datetime import date
from pathlib import Path

import pytest

from jobfinder import cli, cost

PRICING = cost.load_pricing()
USAGE = {"input_tokens": 10, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 5000,
         "output_tokens": 200,
         "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 1000}}


def assistant(message_id, blocks=("text",), model="claude-opus-5-5", usage=USAGE, at="2026-10-24T10:00:00Z",
              sidechain=False):
    """One response as Claude Code writes it: a line per content block, each repeating the usage."""
    return [{"type": "assistant", "isSidechain": sidechain, "timestamp": at, "requestId": f"req-{message_id}",
             "message": {"id": message_id, "model": model, "usage": usage, "content": [block]}}
            for block in [_block(kind) for kind in blocks]]


def _block(kind):
    if isinstance(kind, dict):
        return kind
    return {"type": "thinking", "thinking": ""} if kind == "thinking" else {"type": kind, "text": "..."}


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def write(path: Path, entries) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries) + "{not json\n")
    return path


@pytest.fixture
def data_dir(tmp_path):
    folder = tmp_path / "data" / "cv" / "24-10-2026" / "acme-1"
    folder.mkdir(parents=True)
    (folder / "input.json").write_text(json.dumps({"posting": {"id": "linkedin:1"}}))
    folder = tmp_path / "data" / "apply" / "24-10-2026" / "acme-1"
    folder.mkdir(parents=True)
    (folder / "input.json").write_text(json.dumps({"id": "linkedin:1"}))
    batch = tmp_path / "data" / "score" / "batches"
    batch.mkdir(parents=True)
    (batch / "match-1.json").write_text(json.dumps({"step": "match", "jobs": [{"id": "a"}, {"id": "b"}]}))
    return tmp_path / "data"


def test_split_response_is_counted_once():
    entries = assistant("m1", ("thinking", "tool_use", "tool_use")) + assistant("m2")
    items = cost.responses(entries)
    assert [item["id"] for item in items] == ["m1", "m2"]
    total = cost.summarize(items, PRICING)
    assert total["requests"] == 2
    assert total["tokens"] == {"input": 20, "cache_write_5m": 0, "cache_write_1h": 2000,
                               "cache_read": 10000, "output": 400}
    # Opus 5.5: 20 x $4 + 2000 x $8 + 10000 x $0.20 + 400 x $20, per million
    assert total["cost_usd"] == pytest.approx((20 * 4 + 2000 * 8 + 10000 * 0.2 + 400 * 20) / 1e6)


def test_old_usage_without_split_is_a_5_minute_write():
    usage = {"input_tokens": 1, "cache_creation_input_tokens": 100, "output_tokens": 1}
    [item] = cost.responses(assistant("m1", usage=usage))
    assert item["tokens"]["cache_write_5m"] == 100 and item["tokens"]["cache_write_1h"] == 0


def test_haiku_long_prompt_price_and_unknown_model():
    small = {"input": 1000, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0, "output": 1000}
    large = {**small, "cache_read": 200_000}
    assert cost.price_of("claude-haiku-5-5", small, PRICING) == pytest.approx((1000 * 0.1 + 1000 * 0.5) / 1e6)
    assert cost.price_of("claude-haiku-5-5", large, PRICING) == pytest.approx(
        (1000 * 0.5 + 200_000 * 0.05 + 1000 * 2.5) / 1e6)
    assert cost.price_of("claude-haiku-4-5-20251001", small, PRICING) == pytest.approx(6000 / 1e6)
    total = cost.summarize(cost.responses(assistant("m1", model="claude-unknown-9")), PRICING)
    assert total["cost_usd"] == 0 and total["unknown_models"] == ["claude-unknown-9"]
    assert total["by_model"]["claude-unknown-9"]["cost_usd"] is None


def test_synthetic_messages_are_skipped():
    assert cost.responses(assistant("m1", model="<synthetic>")) == []


def test_agent_run_links_cv_writer_to_its_posting(tmp_path, data_dir):
    entries = [user("Input file: data/cv/24-10-2026/acme-1/input.json")] + assistant("m1")
    run = cost.agent_run(entries, "cv-writer", tmp_path, data_dir, PRICING)
    assert run["stage"] == "cv" and run["job_id"] == "linkedin:1" and run["requests"] == 1


def test_agent_run_links_job_sender_and_batches(tmp_path, data_dir):
    run = cost.agent_run([user("Input file: data/apply/24-10-2026/acme-1/input.json")] + assistant("m1"),
                         "job-sender", tmp_path, data_dir, PRICING)
    assert run["stage"] == "apply" and run["job_id"] == "linkedin:1"
    run = cost.agent_run([user("Batch file: data/score/batches/match-1.json")] + assistant("m1"),
                         "job-matcher", tmp_path, data_dir, PRICING)
    assert run["job_id"] is None and run["job_ids"] == ["a", "b"]


def test_agent_run_ignores_files_outside_data(tmp_path, data_dir):
    (tmp_path / "other.json").write_text(json.dumps({"id": "x"}))
    run = cost.agent_run([user("Input file: other.json")] + assistant("m1"), "cv-writer", tmp_path,
                         data_dir, PRICING)
    assert run["job_id"] is None and run["input"] == "other.json"


def test_main_runs_split_by_command_and_skill():
    skill = {"type": "tool_use", "name": "Skill", "input": {"skill": "execute-cv"}}
    entries = (assistant("m0")
               + [user("<command-message>write-cv</command-message>\n"
                       "<command-name>/write-cv</command-name>\n<command-args></command-args>")]
               + assistant("m1") + assistant("m2", ("text", skill)) + assistant("m3")
               + assistant("s1", sidechain=True))
    runs = cost.main_runs(entries, PRICING)
    assert [(run["command"], run["requests"]) for run in runs] == [
        (None, 1), ("/write-cv", 2), ("/execute-cv", 1)]


def test_hook_records_agent_and_resumed_agent_once(tmp_path, data_dir, monkeypatch):
    usage_path = data_dir / "cost" / "usage.json"
    transcript = tmp_path / "s1" / "subagents" / "agent-a1.jsonl"
    prompt = [user("Input file: data/apply/24-10-2026/acme-1/input.json")]
    event = {"hook_event_name": "SubagentStop", "session_id": "s1", "agent_id": "a1",
             "agent_type": "job-sender", "agent_transcript_path": str(transcript), "cwd": str(tmp_path)}
    write(transcript, prompt + assistant("m1"))
    cost.record_agent(event, usage_path, data_dir, PRICING)
    write(transcript, prompt + assistant("m1") + assistant("m2"))  # resumed with SendMessage, stops again
    cost.record_agent(event, usage_path, data_dir, PRICING)
    usage = cost.load_usage(usage_path)
    assert list(usage["runs"]) == ["s1/a1"]
    assert usage["runs"]["s1/a1"]["requests"] == 2 and usage["runs"]["s1/a1"]["job_id"] == "linkedin:1"


def test_hook_never_fails(monkeypatch, tmp_path):
    log = tmp_path / "errors.log"
    monkeypatch.setattr(cost, "COST_ERRORS_LOG", log)
    for text in ("{bad", json.dumps({"hook_event_name": "Stop", "transcript_path": str(tmp_path / "gone.jsonl")}),
                 json.dumps({"hook_event_name": "SubagentStop", "agent_type": "cv-writer"})):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        assert cost.hook_main() == 0
    assert log.exists()


def test_hook_skips_other_agents(monkeypatch):
    called = []
    monkeypatch.setattr(cost, "record_agent", lambda event: called.append(event))
    for agent_type in ("", "Explore"):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
            {"hook_event_name": "SubagentStop", "agent_type": agent_type})))
        assert cost.hook_main() == 0
    assert called == []


def test_record_main_replaces_the_session(tmp_path):
    usage_path = tmp_path / "usage.json"
    transcript = tmp_path / "s1.jsonl"
    event = {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(transcript)}
    write(transcript, [user("<command-name>/execute-cv</command-name>")] + assistant("m1"))
    cost.record_main(event, usage_path, PRICING)
    write(transcript, [user("<command-name>/execute-cv</command-name>")] + assistant("m1") + assistant("m2"))
    cost.record_main(event, usage_path, PRICING)
    runs = cost.load_usage(usage_path)["runs"]
    assert list(runs) == ["s1/main/0"] and runs["s1/main/0"]["requests"] == 2


def _usage(tmp_path, data_dir):
    usage_path = data_dir / "cost" / "usage.json"
    for agent_id, agent_type, prompt, at in (
            ("a1", "cv-writer", "Input file: data/cv/24-10-2026/acme-1/input.json", "2026-10-23T10:00:00Z"),
            ("a2", "cv-writer", "Input file: data/cv/24-10-2026/acme-1/input.json", "2026-10-24T10:00:00Z"),
            ("a3", "job-sender", "Input file: data/apply/24-10-2026/acme-1/input.json", "2026-10-24T11:00:00Z")):
        transcript = write(tmp_path / "s1" / "subagents" / f"agent-{agent_id}.jsonl",
                           [user(prompt)] + assistant(agent_id, at=at))
        cost.record_agent({"session_id": "s1", "agent_id": agent_id, "agent_type": agent_type,
                           "agent_transcript_path": str(transcript), "cwd": str(tmp_path)},
                          usage_path, data_dir, PRICING)
    return cost.load_usage(usage_path)


def test_report_by_job(tmp_path, data_dir):
    usage = _usage(tmp_path, data_dir)
    store = {"jobs": [{"id": "linkedin:1", "title": "Backend Engineer", "company_name": "Acme"}]}
    result = cost.report(usage, store, "job")
    [row] = result["rows"]
    assert row["title"] == "Backend Engineer"
    assert row["stages"]["cv"]["runs"] == 2 and row["stages"]["apply"]["runs"] == 1
    assert row["total_tokens"] == 3 * 6210
    assert result["total"]["runs"] == 3 and result["estimate"] is True
    assert cost.report(usage, store, "job", since=date(2026, 10, 24))["rows"][0]["stages"]["cv"]["runs"] == 1
    assert cost.report(usage, store, "job", job_ids=["other"])["rows"] == []


def test_report_by_agent_and_bad_group(tmp_path, data_dir):
    usage = _usage(tmp_path, data_dir)
    rows = {row["agent"]: row["runs"] for row in cost.report(usage, None, "agent")["rows"]}
    assert rows == {"cv-writer": 2, "job-sender": 1}
    with pytest.raises(cost.CostError):
        cost.report(usage, None, "week")


def test_refresh_reads_late_lines(tmp_path, data_dir):
    usage = _usage(tmp_path, data_dir)
    transcript = Path(usage["runs"]["s1/a3"]["transcript"])
    write(transcript, [user("Input file: data/apply/24-10-2026/acme-1/input.json")]
          + assistant("a3") + assistant("late"))
    transcript_gone = Path(usage["runs"]["s1/a1"]["transcript"])
    transcript_gone.unlink()
    assert cost.refresh(usage, data_dir, PRICING) == 2
    assert usage["runs"]["s1/a3"]["requests"] == 2 and usage["runs"]["s1/a1"]["requests"] == 1


def test_cli_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "COST_USAGE_JSON", tmp_path / "usage.json")
    monkeypatch.setattr(cli, "JOBS_JSON", tmp_path / "jobs.json")
    assert cli.main(["cost", "report", "--by", "agent"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["rows"] == [] and out["refreshed"] == 0
    assert cli.main(["cost", "report", "--since", "24-10-2026"]) == 1
