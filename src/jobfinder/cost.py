"""Token use and estimated cost of every LLM step (all stages), read from Claude Code's own records.

    hook_main()                the hook's entry point (`python -m jobfinder.cost`, set in .claude/settings.json):
                               SubagentStop records one agent run, Stop the main session's commands
    record_agent(event)        sum the agent's transcript into data/cost/usage.json, linked to its posting
    record_main(event)         sum the main session's transcript, one entry per command it ran
    refresh(usage)             read every transcript still on disk again (the last message can land late)
    report(usage, store, by)   totals by job, agent, command or day (job: only runs on one posting)

Claude Code writes the main session to ~/.claude/projects/<project>/<session_id>.jsonl and each agent to
<session_id>/subagents/agent-<agent_id>.jsonl. Every model response there carries `message.usage`, but one
response is split over several lines (one per content block) that repeat the same usage, so responses are
counted once by `message.id`. The agents of this repository always start with `Input file: <path>` or
`Batch file: <path>`; code reads the posting id from that file. Nothing here calls an LLM.

Tokens are what the API billed (or, on a Claude plan, what was used); dollars are an estimate from
config/pricing.json, since a plan is not billed per token.
"""

import json
import os
import re
import sys
import traceback
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from jobfinder.paths import COST_ERRORS_LOG, COST_USAGE_JSON, DATA_DIR, PRICING_JSON, ROOT

KINDS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
STAGES = {"cv-writer": "cv", "cv-reviewer": "cv", "job-sender": "apply", "job-extractor": "score", "job-matcher": "score",
          "mail-reader": "track", "profile-extractor": "profile"}
INPUT_RE = re.compile(r"(?:Input|Batch) file: (\S+)")
COMMAND_RE = re.compile(r"<command-name>/?([\w:.-]+)</command-name>")
BY = ("job", "agent", "command", "day")


class CostError(Exception):
    pass


def hook_main() -> int:
    """Always exits 0 with nothing on stdout: a SubagentStop hook that exits 2 or prints a decision
    keeps the agent running, and recording cost must never change what an agent does."""
    try:
        event = json.loads(sys.stdin.read() or "{}")
        if isinstance(event, dict):
            name = event.get("hook_event_name")
            if name == "SubagentStop" and event.get("agent_type") in STAGES:
                record_agent(event)
            elif name == "Stop":
                record_main(event)
    except Exception:  # noqa: BLE001 - logged, never raised into Claude Code
        _log_error()
    return 0


def record_agent(event: dict, usage_path: Path = COST_USAGE_JSON, data_dir: Path = DATA_DIR,
                 pricing: dict | None = None) -> dict:
    path = _path(event.get("agent_transcript_path"))
    if path is None:
        raise CostError("SubagentStop without agent_transcript_path")
    root = Path(event.get("cwd") or ROOT)
    run = agent_run(read_transcript(path), str(event.get("agent_type")), root, data_dir,
                    pricing or load_pricing())
    run.update(session_id=event.get("session_id"), agent_id=event.get("agent_id"), transcript=str(path))
    with _locked(usage_path) as usage:
        usage["runs"][f"{event.get('session_id')}/{event.get('agent_id')}"] = run
    return run


def record_main(event: dict, usage_path: Path = COST_USAGE_JSON, pricing: dict | None = None) -> list[dict]:
    path = _path(event.get("transcript_path"))
    if path is None:
        raise CostError("Stop without transcript_path")
    session = str(event.get("session_id") or path.stem)
    runs = main_runs(read_transcript(path), pricing or load_pricing())
    with _locked(usage_path) as usage:
        _replace_main(usage, session, path, runs)
    return runs


def read_transcript(path: Path) -> list[dict]:
    """The transcript's entries; a line that is not JSON (one being written) is skipped."""
    entries = []
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def responses(entries: list[dict], main: bool = False) -> list[dict]:
    """One item per model response, in order: {id, model, at, tokens}. In a main transcript, lines of
    an agent (`isSidechain`, written there by older Claude Code versions) are left out."""
    seen: dict[str, dict] = {}
    for index, entry in enumerate(entries):
        if entry.get("type") != "assistant" or (main and entry.get("isSidechain")):
            continue
        message = entry.get("message") or {}
        usage = message.get("usage")
        if not isinstance(usage, dict) or message.get("model") in (None, "<synthetic>"):
            continue
        key = message.get("id") or entry.get("requestId") or f"line-{index}"
        first = seen.get(key)
        seen[key] = {"id": key, "model": message["model"], "at": (first or entry).get("timestamp"),
                     "index": first["index"] if first else index, "tokens": _tokens(usage)}
    return sorted(seen.values(), key=lambda item: item["index"])


def summarize(items: list[dict], pricing: dict) -> dict:
    tokens = dict.fromkeys(KINDS, 0)
    by_model: dict[str, dict] = {}
    cost, unknown = 0.0, set()
    for item in items:
        model = by_model.setdefault(item["model"], {"requests": 0, "tokens": dict.fromkeys(KINDS, 0),
                                                    "cost_usd": 0.0})
        model["requests"] += 1
        for kind in KINDS:
            tokens[kind] += item["tokens"][kind]
            model["tokens"][kind] += item["tokens"][kind]
        price = price_of(item["model"], item["tokens"], pricing)
        if price is None:
            unknown.add(item["model"])
            model["cost_usd"] = None
        else:
            cost += price
            if model["cost_usd"] is not None:
                model["cost_usd"] += price
    for model in by_model.values():
        if model["cost_usd"] is not None:
            model["cost_usd"] = round(model["cost_usd"], 6)
    times = [item["at"] for item in items if item.get("at")]
    return {"started_at": min(times, default=None), "ended_at": max(times, default=None),
            "requests": len(items), "tokens": tokens, "total_tokens": sum(tokens.values()),
            "by_model": by_model, "cost_usd": round(cost, 6), "unknown_models": sorted(unknown)}


def price_of(model: str, tokens: dict, pricing: dict) -> float | None:
    """The estimated dollars of one response; None for a model config/pricing.json does not list."""
    rates = _rates(model, pricing)
    if rates is None:
        return None
    long_prompt = rates.get("long_prompt")
    prompt = tokens["input"] + tokens["cache_write_5m"] + tokens["cache_write_1h"] + tokens["cache_read"]
    if long_prompt and prompt > long_prompt["above"]:
        rates = long_prompt
    return sum(tokens[kind] * rates[kind] for kind in KINDS) / 1_000_000


def agent_run(entries: list[dict], agent_type: str, root: Path = ROOT, data_dir: Path = DATA_DIR,
              pricing: dict | None = None) -> dict:
    run = {"agent_type": agent_type, "stage": STAGES.get(agent_type)}
    run.update(_subject(entries, root, data_dir))
    run.update(summarize(responses(entries), pricing or load_pricing()))
    return run


def main_runs(entries: list[dict], pricing: dict | None = None) -> list[dict]:
    """The main session cut where a slash command starts or the Skill tool runs a narrow command:
    each response belongs to the command started last before it (None before the first)."""
    pricing = pricing or load_pricing()
    commands = {}  # entry index -> command started there
    for index, entry in enumerate(entries):
        if entry.get("isSidechain"):
            continue
        message = entry.get("message") or {}
        if entry.get("type") == "user":
            for text in _texts(message.get("content")):
                match = COMMAND_RE.search(text)
                if match:
                    commands[index] = "/" + match.group(1)
        elif entry.get("type") == "assistant":
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Skill":
                    skill = (block.get("input") or {}).get("skill")
                    if isinstance(skill, str) and skill:
                        commands[index] = "/" + skill.lstrip("/")
    starts = sorted(commands)
    segments: dict[int, list[dict]] = {}  # start index (-1: before any command) -> its responses
    for item in responses(entries, main=True):
        start = max((start for start in starts if start < item["index"]), default=-1)
        segments.setdefault(start, []).append(item)
    return [{"agent_type": "main", "command": commands.get(start), **summarize(items, pricing)}
            for start, items in sorted(segments.items())]


def refresh(usage: dict, data_dir: Path = DATA_DIR, pricing: dict | None = None) -> int:
    """Read every transcript still on disk again (Claude Code writes it after the hook may have run);
    returns how many entries were read. A transcript Claude Code removed keeps its last entry."""
    pricing = pricing or load_pricing()
    done, count = set(), 0
    for key, run in list(usage["runs"].items()):
        path = Path(run.get("transcript") or "")
        if not run.get("transcript") or not path.is_file():
            continue
        if run["agent_type"] == "main":
            session = run["session_id"]
            if session in done:
                continue
            done.add(session)
            _replace_main(usage, session, path, main_runs(read_transcript(path), pricing))
        else:
            fresh = agent_run(read_transcript(path), run["agent_type"], Path(run.get("root") or ROOT),
                              data_dir, pricing)
            usage["runs"][key] = {**run, **fresh}
        count += 1
    return count


def report(usage: dict, store: dict | None = None, by: str = "job", since: date | None = None,
           job_ids: list[str] | None = None) -> dict:
    if by not in BY:
        raise CostError(f"by must be one of {', '.join(BY)}")
    runs = [run for run in usage["runs"].values() if _since(run, since)]
    if job_ids:
        runs = [run for run in runs if run.get("job_id") in job_ids]
    groups: dict = defaultdict(lambda: {"runs": 0, "requests": 0, "tokens": dict.fromkeys(KINDS, 0),
                                        "cost_usd": 0.0})
    for run in runs:
        key = _group(run, by)
        if key is None:
            continue
        group = groups[key if by != "job" else (key, run.get("stage"))]
        group["runs"] += 1
        group["requests"] += run["requests"]
        for kind in KINDS:
            group["tokens"][kind] += run["tokens"][kind]
        group["cost_usd"] += run["cost_usd"] or 0.0
    rows = _job_rows(groups, store) if by == "job" else [
        {by: key, **_finish(group)} for key, group in groups.items()]
    rows.sort(key=lambda row: -(row.get("cost_usd") or 0))
    total = dict.fromkeys(KINDS, 0)
    for run in runs:
        for kind in KINDS:
            total[kind] += run["tokens"][kind]
    unknown = sorted({model for run in runs for model in run.get("unknown_models", [])})
    return {"ok": True, "by": by, "since": since.isoformat() if since else None, "count": len(rows),
            "rows": rows, "total": {"runs": len(runs), "tokens": total, "total_tokens": sum(total.values()),
                                    "cost_usd": round(sum(run["cost_usd"] or 0 for run in runs), 4)},
            "unknown_models": unknown, "estimate": True}


def detail(usage: dict, store: dict | None = None) -> list[dict]:
    """Every recorded run as one flat row, newest first (for the web UI's cost page)."""
    titles = {job["id"]: job for job in (store or {}).get("jobs", [])}
    rows = []
    for key, run in usage["runs"].items():
        at = run.get("ended_at") or run.get("started_at")
        job = titles.get(run.get("job_id"), {})
        rows.append({"id": key, "at": at, "day": _local_day(at).isoformat() if at else None,
                     "agent": run["agent_type"], "command": run.get("command"), "stage": run.get("stage"),
                     "job": run.get("job_id"), "title": job.get("title"), "company": job.get("company_name"),
                     "models": sorted(run.get("by_model", {})), "requests": run["requests"],
                     "tokens": run["tokens"], "total_tokens": run["total_tokens"], "cost_usd": run["cost_usd"]})
    rows.sort(key=lambda row: row["at"] or "", reverse=True)
    return rows


def load_usage(path: Path = COST_USAGE_JSON) -> dict:
    if not path.exists():
        return {"runs": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("runs"), dict):
        raise CostError(f"{path} is not a usage file")
    return data


def save_usage(usage: dict, path: Path = COST_USAGE_JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def load_pricing(path: Path = PRICING_JSON) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tokens(usage: dict) -> dict:
    split = usage.get("cache_creation")
    if isinstance(split, dict):
        five, hour = split.get("ephemeral_5m_input_tokens") or 0, split.get("ephemeral_1h_input_tokens") or 0
    else:  # older records: one number, the 5-minute cache by default
        five, hour = usage.get("cache_creation_input_tokens") or 0, 0
    return {"input": usage.get("input_tokens") or 0, "cache_write_5m": five, "cache_write_1h": hour,
            "cache_read": usage.get("cache_read_input_tokens") or 0, "output": usage.get("output_tokens") or 0}


def _rates(model: str, pricing: dict) -> dict | None:
    models = pricing.get("models", {})
    if model in models:
        return models[model]
    # a dated or suffixed id (claude-haiku-4-5-20251001, ...[1m]): the longest listed id it starts with
    matches = [name for name in models if model.startswith(name + "-") or model.startswith(name + "[")]
    return models[max(matches, key=len)] if matches else None


def _subject(entries: list[dict], root: Path, data_dir: Path) -> dict:
    """What the agent worked on, from the file named in its first prompt."""
    for entry in entries:
        if entry.get("type") != "user":
            continue
        for text in _texts((entry.get("message") or {}).get("content")):
            match = INPUT_RE.search(text)
            if match:
                return _read_subject(match.group(1).rstrip(".,;:"), root, data_dir)
        break  # only the first prompt
    return {"input": None, "job_id": None}


def _read_subject(name: str, root: Path, data_dir: Path) -> dict:
    path = Path(name)
    path = (path if path.is_absolute() else root / path).resolve()
    result = {"input": name, "root": str(root), "job_id": None}
    try:
        relative = path.relative_to(data_dir.resolve())
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, json.JSONDecodeError):
        return result  # outside data/ or gone: the run is kept without a posting
    if not isinstance(data, dict):
        return result
    folder = relative.parts[0] if relative.parts else None
    if folder == "cv":
        result["job_id"] = (data.get("posting") or {}).get("id")
    elif folder == "apply":
        result["job_id"] = data.get("id")
    elif folder in ("score", "track"):  # a batch of postings: kept per batch, not split
        result["job_ids"] = [job.get("id") for job in data.get("jobs") or [] if isinstance(job, dict)]
    return result


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"]
    return []


def _replace_main(usage: dict, session: str, path: Path, runs: list[dict]) -> None:
    prefix = f"{session}/main/"
    for key in [key for key in usage["runs"] if key.startswith(prefix)]:
        del usage["runs"][key]
    for number, run in enumerate(runs):
        usage["runs"][f"{prefix}{number}"] = {**run, "session_id": session, "transcript": str(path)}


def _group(run: dict, by: str):
    if by == "job":
        return run.get("job_id")
    if by == "agent":
        return run["agent_type"]
    if by == "command":
        return (run.get("command") or "(no command)") if run["agent_type"] == "main" else None
    at = run.get("ended_at") or run.get("started_at")
    return _local_day(at).isoformat() if at else None


def _job_rows(groups: dict, store: dict | None) -> list[dict]:
    jobs = {job["id"]: job for job in (store or {}).get("jobs", [])}
    rows: dict[str, dict] = {}
    for (job_id, stage), group in groups.items():
        job = jobs.get(job_id, {})
        row = rows.setdefault(job_id, {"job": job_id, "title": job.get("title"),
                                       "company_name": job.get("company_name"), "stages": {},
                                       "total_tokens": 0, "cost_usd": 0.0})
        done = _finish(group)
        row["stages"][stage or "other"] = done
        row["total_tokens"] += done["total_tokens"]
        row["cost_usd"] = round(row["cost_usd"] + done["cost_usd"], 4)
    return list(rows.values())


def _finish(group: dict) -> dict:
    return {**group, "total_tokens": sum(group["tokens"].values()), "cost_usd": round(group["cost_usd"], 4)}


def _since(run: dict, since: date | None) -> bool:
    if since is None:
        return True
    at = run.get("ended_at") or run.get("started_at")
    return bool(at) and _local_day(at) >= since


def _local_day(at: str) -> date:
    return datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone().date()


def _path(value) -> Path | None:
    return Path(os.path.expanduser(value)) if isinstance(value, str) and value else None


@contextmanager
def _locked(path: Path):
    """Load, change and save usage.json under a lock: four cv-writer agents can stop at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / ".usage.lock").open("w") as lock:
        try:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        except ImportError:  # not POSIX: no lock
            pass
        usage = load_usage(path)
        yield usage
        save_usage(usage, path)


def _log_error(path: Path | None = None) -> None:
    path = path or COST_ERRORS_LOG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log:
            log.write(f"{datetime.now(UTC).isoformat()} {traceback.format_exc(limit=3)}\n")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(hook_main())
