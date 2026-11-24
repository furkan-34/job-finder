"""Agents' answers saved by code, not copied by the orchestrating command.

The read-only agents return one JSON block as text. Without this module the command took it from the
Agent result, wrote it unchanged to the result file with the Write tool and ran the save tool: three
model requests per answer, the whole answer typed again as output, and text written from postings or
mail in the main session. Two hooks in .claude/settings.json do it instead (`python -m jobfinder.handoff`):

    PostToolUse on Agent   job-extractor, job-matcher, cv-writer, cv-reviewer, mail-reader: the prompt names the
                           batch or input file; the answer is written to its result file, the save
                           runs (score save, cv save, track save) and the Agent result the command
                           sees is replaced by the save's JSON: counts, codes and paths, never a value
                           from the answer
    SubagentStop           job-sender, which /apply continues with SendMessage (its later answers
                           arrive as notifications, not as an Agent result): its last message is
                           written to the run's answer.json, so the command only runs `apply step`

Both are best effort: a hook that cannot save says so in the replaced result (`"ok": false`), and a
command that sees the agent's JSON instead (the hook did not run) falls back to Write and save.
jobs.json is changed under a lock, because four agents can finish at once.
"""

import json
import re
import sys
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from jobfinder import jobs, send, track
from jobfinder.cv import settings as cv_settings
from jobfinder.cv import writer
from jobfinder.fetch import settings as fetch_settings
from jobfinder.paths import (
    APPLY_DIR,
    COST_ERRORS_LOG,
    CV_DIR,
    CV_SETTINGS_JSON,
    FETCH_SETTINGS_JSON,
    JOBS_JSON,
    PROFILE_JSON,
    ROOT,
    SCORE_BATCHES_DIR,
    SCORE_RESULTS_DIR,
    SCORE_SETTINGS_JSON,
    TRACK_BATCHES_DIR,
    TRACK_RESULTS_DIR,
)
from jobfinder.profile.schema import validate
from jobfinder.score import batches
from jobfinder.score import settings as score_settings

SAVED_AGENTS = ("job-extractor", "job-matcher", "cv-writer", "cv-reviewer", "mail-reader")  # saved after the Agent call
SENDER = send.AGENT  # its answers are written at SubagentStop
FILE_RE = re.compile(r"(?:Input|Batch) file: (\S+?\.json)")
BLOCK_RE = re.compile(r"```json[ \t]*\n(.*?)\n[ \t]*```", re.DOTALL)
MARK = "jobfinder.handoff"  # in every replaced result, so the command knows the hook saved the answer


def hook_main() -> int:
    """Never blocks: errors are logged, and for an Agent call reported in the replaced result."""
    try:
        event = json.loads(sys.stdin.read() or "{}")
        output = handle(event) if isinstance(event, dict) else None
    except Exception:  # noqa: BLE001 - logged, never raised into Claude Code
        _log_error()
        output = None
    if output is not None:
        print(json.dumps(output, ensure_ascii=False))
    return 0


def handle(event: dict, now: datetime | None = None) -> dict | None:
    name = event.get("hook_event_name")
    if name == "PostToolUse" and event.get("tool_name") in ("Agent", "Task"):
        tool_input = event.get("tool_input") or {}
        if tool_input.get("subagent_type") not in SAVED_AGENTS:
            return None
        response = event.get("tool_response")
        if not isinstance(response, dict) or response.get("status") != "completed":
            return None  # a background launch has no answer yet
        try:
            result = save_answer(tool_input["subagent_type"], str(tool_input.get("prompt") or ""),
                                 answer_text(response), now)
        except Exception as exc:  # noqa: BLE001 - the command sees the error and skips this batch
            _log_error()
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        replaced = {**response, "content": [{"type": "text", "text": json.dumps(
            {"saved_by": MARK, **result}, ensure_ascii=False)}]}
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": replaced}}
    if name == "SubagentStop" and event.get("agent_type") == SENDER:
        write_sender_answer(str(event.get("last_assistant_message") or ""))
    return None  # nothing printed: a SubagentStop decision would keep the agent running


def answer_text(response: dict) -> str:
    return "\n".join(block.get("text", "") for block in response.get("content") or []
                     if isinstance(block, dict) and block.get("type") == "text")


def json_block(text: str) -> str:
    """The answer's first fenced `json` block, as the command used to copy it; the whole text
    when there is none (the save then says it is not JSON)."""
    match = BLOCK_RE.search(text)
    return (match.group(1) if match else text).strip() + "\n"


def save_answer(agent: str, prompt: str, text: str, now: datetime | None = None) -> dict:
    """Write the agent's answer next to its batch or input and run the save; returns the save's JSON."""
    match = FILE_RE.search(prompt)
    if match is None:
        return {"ok": False, "error": "no 'Batch file:' or 'Input file:' in the prompt"}
    given = Path(match.group(1))
    given = (given if given.is_absolute() else ROOT / given).resolve()
    if agent in ("cv-writer", "cv-reviewer"):
        if given.name != "input.json" or CV_DIR.resolve() not in given.parents:
            return {"ok": False, "error": f"{given} is not a {agent} input"}
        reviewed = agent == "cv-reviewer"
        answer = given.parent / (writer.REVIEW if reviewed else "answer.json")
        answer.write_text(json_block(text), encoding="utf-8")
        return {"input_path": str(given), "answer_path": str(answer),
                **save_cv(given, answer, now, jobs_path=JOBS_JSON, profile_path=PROFILE_JSON,
                          settings_path=CV_SETTINGS_JSON, cv_dir=CV_DIR, reviewed=reviewed)}
    batches_dir, results_dir = ((TRACK_BATCHES_DIR, TRACK_RESULTS_DIR) if agent == "mail-reader"
                                else (SCORE_BATCHES_DIR, SCORE_RESULTS_DIR))
    if given.parent != batches_dir.resolve():
        return {"ok": False, "error": f"{given} is not a batch file of {batches_dir}"}
    result_path = results_dir / given.name
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json_block(text), encoding="utf-8")
    if agent == "mail-reader":
        saved = save_track(given, result_path, now, jobs_path=JOBS_JSON,
                           settings_path=SCORE_SETTINGS_JSON)
    else:
        saved = save_score(given, result_path, now, jobs_path=JOBS_JSON, profile_path=PROFILE_JSON,
                           settings_path=SCORE_SETTINGS_JSON)
    return {"batch": str(given), "result_path": str(result_path), **saved}


def write_sender_answer(text: str, apply_dir: Path = APPLY_DIR) -> Path | None:
    """The job-sender's last message, as the run's answer.json; nothing when no run is open
    (`apply step` removes the run when the application is over)."""
    try:
        run = send.load_run(apply_dir)
    except (send.SendError, OSError, json.JSONDecodeError):
        return None
    if not text.strip():
        return None
    path = Path(run["folder"]) / send.ANSWER_FILE
    path.write_text(json_block(text), encoding="utf-8")
    return path


def load_profile(path: Path = PROFILE_JSON) -> tuple[dict | None, dict | None]:
    """(profile, None), or (None, the error to print) when it is missing or not valid."""
    if not path.exists():
        return None, {"ok": False, "error": f"{path} not found; run /create-profile first."}
    profile = json.loads(path.read_text(encoding="utf-8"))
    if errors := validate(profile):
        return None, {"ok": False, "error": "profile.json is not valid; run /create-profile.",
                      "errors": errors}
    return profile, None


def save_score(batch_path: Path, result_path: Path, now: datetime | None = None, *,
               jobs_path: Path = JOBS_JSON, profile_path: Path = PROFILE_JSON,
               settings_path: Path = SCORE_SETTINGS_JSON, fetch_path: Path = FETCH_SETTINGS_JSON) -> dict:
    profile, error = load_profile(profile_path)
    if error:
        return error
    matcher_model = score_settings.load(settings_path)["matcher_model"]
    with jobs_lock(jobs_path):
        store = jobs.load(jobs_path)
        try:
            result = batches.save(store, batch_path, result_path, matcher_model, profile, now,
                                  fetch_settings.load(fetch_path)["exclude_words"])
        except (batches.BatchError, OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": str(exc)}
        if result["saved"]:
            jobs.save(store, jobs_path)
    return {"ok": True, "path": str(jobs_path), **result}


def save_cv(input_path: Path, answer_path: Path, now: datetime | None = None, *,
            jobs_path: Path = JOBS_JSON, profile_path: Path = PROFILE_JSON,
            settings_path: Path = CV_SETTINGS_JSON, cv_dir: Path = CV_DIR, reviewed: bool = False) -> dict:
    profile, error = load_profile(profile_path)
    if error:
        return error
    writer_model = cv_settings.load(settings_path)["writer_model"]
    with jobs_lock(jobs_path):
        store = jobs.load(jobs_path)
        try:
            result = writer.save(store, profile, input_path, answer_path, cv_dir, writer_model, now, reviewed)
        except (writer.WriterError, OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": str(exc)}
        if result["ok"]:
            jobs.save(store, jobs_path)
    return {"path": str(jobs_path), **result}


def save_track(batch_path: Path, result_path: Path, now: datetime | None = None, *,
               jobs_path: Path = JOBS_JSON, settings_path: Path = SCORE_SETTINGS_JSON) -> dict:
    min_score = score_settings.load(settings_path)["min_score"]
    with jobs_lock(jobs_path):
        store = jobs.load(jobs_path)
        try:
            result = track.save(store, batch_path, result_path, now, min_score=min_score)
        except (batches.BatchError, OSError, json.JSONDecodeError, KeyError) as exc:
            return {"ok": False, "error": str(exc)}
        if result["checked"]:
            jobs.save(store, jobs_path)
    return {"ok": True, "path": str(jobs_path), **result}


@contextmanager
def jobs_lock(path: Path = JOBS_JSON):
    """Hold a lock while jobs.json is loaded, changed and saved: four agents' answers can be
    saved at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / ".jobs.lock").open("w") as lock:
        try:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        except ImportError:  # not POSIX: no lock
            pass
        yield


def _log_error() -> None:
    try:
        COST_ERRORS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with (COST_ERRORS_LOG.parent / "handoff-errors.log").open("a", encoding="utf-8") as log:
            log.write(f"{datetime.now(UTC).isoformat()} {traceback.format_exc(limit=3)}\n")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(hook_main())
