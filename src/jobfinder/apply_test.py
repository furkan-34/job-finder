"""Test applications (/test-apply): the same /apply flow on one posting the candidate picks,
measured each time so that changes to the agent, its prompts or its model can be compared.

    choice    the posting picked for the tests (data/apply/test.json, asked once) and what keeps
              the agent from sending it (missing documents, safety), never its status
    pick      remember the posting for every later test
    stamp     what is being tested: the git commit (and whether files were changed), the
              job-sender model; written into the run's test.json by `apply batch --test`
    report    one row per test run (data/apply/tests/*/test.json): result, steps, duration and
              the tokens and estimated dollars of the agent (its runs read that folder's input),
              with the change against the previous and the first test, and those of the
              /test-apply command apart: the Stop hook records a command when its turn ends, so
              the test being reported has none yet (the next report has it)

A test run sends the form for real, every time, on the same posting: the posting keeps its
status and the run is not counted in max_per_day. Nothing here calls an LLM.
"""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from jobfinder import cost, send
from jobfinder.apply import find
from jobfinder.paths import APPLY_DIR, APPLY_TEST_JSON, ROOT

COMMAND = "/test-apply"


def choice(store: dict, path: Path = APPLY_TEST_JSON) -> dict:
    """The remembered posting, with `blockers` (empty: the agent may send it), or no job_id."""
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    job_id = saved.get("job_id") if isinstance(saved, dict) else None
    job = next((job for job in store["jobs"] if job["id"] == job_id), None) if job_id else None
    if job is None:
        return {"job_id": None}
    return {"job_id": job["id"], "title": job.get("title"), "company_name": job.get("company_name"),
            "status": job["status"], "blockers": send.blockers(job, test=True), "picked_at": saved.get("picked_at")}


def pick(store: dict, job_id: str, path: Path = APPLY_TEST_JSON, now: datetime | None = None) -> dict:
    job = find(store, job_id)
    picked = {"job_id": job["id"], "picked_at": (now or datetime.now(UTC)).isoformat()}
    send._write(path, picked)
    return picked


def stamp(settings: dict, root: Path = ROOT) -> dict:
    return {"commit": _git(root, "rev-parse", "--short", "HEAD"),
            "changed": bool(_git(root, "status", "--porcelain", "--untracked-files=no")),
            "sender_model": settings["sender_model"]}


def report(usage: dict, apply_dir: Path = APPLY_DIR) -> dict:
    tests = []
    for path in sorted((apply_dir / send.TESTS_FOLDER).glob(f"*/{send.TEST_FILE}")):
        try:
            test = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        tests.append(_row(test, path.parent, usage))
    tests.sort(key=lambda row: row["started_at"] or "")
    for index, row in enumerate(tests):
        row["vs_previous"] = _change(row, tests[index - 1]) if index else None
        row["vs_first"] = _change(row, tests[0]) if index else None
    return {"count": len(tests), "tests": tests, "estimate": True}


def _row(test: dict, folder: Path, usage: dict) -> dict:
    input_path = (folder / send.INPUT_FILE).resolve()
    agent = [run for run in usage["runs"].values()
             if run["agent_type"] == send.AGENT and _resolves_to(run, input_path)]
    started, ended = test.get("started_at"), test.get("ended_at")
    command = [run for run in usage["runs"].values()
               if run["agent_type"] == "main" and run.get("command") == COMMAND
               and _overlaps(run, started, ended)]
    row = {key: test.get(key) for key in ("id", "title", "company_name", "started_at", "ended_at",
                                           "commit", "changed", "sender_model", "result", "reason",
                                           "steps", "domain")}
    row["folder"] = str(folder)
    row["minutes"] = _minutes(started, ended)
    row["agent"] = _sum(agent)
    row["command"] = _sum(command)
    return row


def _sum(runs: list[dict]) -> dict:
    tokens = dict.fromkeys(cost.KINDS, 0)
    for run in runs:
        for kind in cost.KINDS:
            tokens[kind] += run["tokens"][kind]
    return {"runs": len(runs), "requests": sum(run["requests"] for run in runs), "tokens": tokens,
            "total_tokens": sum(tokens.values()),
            "cost_usd": round(sum(run["cost_usd"] or 0 for run in runs), 4),
            "models": sorted({model for run in runs for model in run.get("by_model", {})})}


def _change(row: dict, other: dict) -> dict:
    def percent(now, before):
        return round((now - before) * 100 / before, 1) if before else None
    agent, before = row["agent"], other["agent"]
    return {"total_tokens": agent["total_tokens"] - before["total_tokens"],
            "total_tokens_percent": percent(agent["total_tokens"], before["total_tokens"]),
            "cost_usd": round(agent["cost_usd"] - before["cost_usd"], 4),
            "cost_percent": percent(agent["cost_usd"], before["cost_usd"]),
            "requests": agent["requests"] - before["requests"],
            "minutes": (round(row["minutes"] - other["minutes"], 1)
                        if row["minutes"] is not None and other["minutes"] is not None else None)}


def _resolves_to(run: dict, input_path: Path) -> bool:
    name = run.get("input")
    if not name:
        return False
    path = Path(name)
    path = path if path.is_absolute() else Path(run.get("root") or ROOT) / path
    return path.resolve() == input_path


def _overlaps(run: dict, started: str | None, ended: str | None) -> bool:
    """The command's responses span the test's run (it starts before the run and ends after it)."""
    if not (started and run.get("started_at") and run.get("ended_at")):
        return False
    start, end = _time(started), _time(ended) if ended else _time(started)
    return _time(run["started_at"]) <= end and _time(run["ended_at"]) >= start


def _minutes(started: str | None, ended: str | None) -> float | None:
    if not (started and ended):
        return None
    return round((_time(ended) - _time(started)).total_seconds() / 60, 1)


def _time(value: str) -> datetime:
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _git(root: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip()
