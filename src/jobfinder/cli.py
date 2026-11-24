"""Deterministic tools used by the Claude Code commands.

    jobfinder profile validate     schema errors in data/profile/profile.json
    jobfinder profile missing      fields still to ask, plus pending confirmations
    jobfinder profile set PTR VAL  set a value (VAL is JSON) at a JSON Pointer
    jobfinder profile render       data/profile/profile.json -> data/profile/profile.md
    jobfinder profile add-skill K L  add the missing skill with key K (from score gaps) at level L
    jobfinder source check         verify APIFY_TOKEN, show this month's usage
    jobfinder source test          fetch a few postings with one actor (LinkedIn by default), save raw JSON;
                                   only postings that pass the safety check are listed
    jobfinder source actors        list the Apify actors in config/apify_actors.json
    jobfinder search validate      check data/searches/searches.json, show per-run totals
    jobfinder fetch configure      save the actors and date filter in data/fetch/settings.json
    jobfinder fetch settings       show data/fetch/settings.json
    jobfinder fetch run            run every enabled search with the saved settings; new postings -> data/jobs/jobs.json
    jobfinder jobs import [RAW ...] map raw actor output into data/jobs/jobs.json (all raw files by default)
    jobfinder jobs dedupe          remove reposts already in data/jobs/jobs.json (status new only)
    jobfinder jobs check [--all]   safety check (stage 2c) of postings not checked yet: pass / warn / block
    jobfinder jobs approve ID ...  mark postings approved for stage 3b (the choice of step 3a)
    jobfinder jobs list            every posting with its pipeline stage, counts per stage first
    jobfinder safety model         download (once) and verify the local prompt injection classifier
    jobfinder score configure      save the scoring settings in data/score/settings.json
    jobfinder score settings       show data/score/settings.json
    jobfinder score filter [--all] rule filter (stage 2d) of postings not filtered yet, from the profile
    jobfinder score batch STEP     write batch files of postings for the extract or match agents
    jobfinder score save B R       store an agent's answer R for batch B (checked against the job schema)
    jobfinder score list           the shortlist: scored postings, best first, with safety warnings
    jobfinder score gaps           skills the postings ask for that the profile lacks, weighted by score
    jobfinder cv configure         save the CV settings (writer model) in data/cv/settings.json
    jobfinder cv settings          show data/cv/settings.json
    jobfinder cv batch [--id ID]   write the cv-writer agent's input for each approved posting
    jobfinder cv save INPUT ANSWER check the agent's answer, render the CV and cover letter -> cv_ready
                                   (--review: the cv-reviewer's revision of saved documents)
    jobfinder apply configure      save the agent's settings in data/apply/settings.json
    jobfinder apply settings       show data/apply/settings.json
    jobfinder apply list           postings whose CV and cover letter are ready, not applied to yet, with links
    jobfinder apply mark ID        record that the candidate sent the application: cv_ready -> applied
    jobfinder apply batch ID       start the job-sender agent's run for one posting: its input and the run
    jobfinder apply step           check what the agent returned (answer.json) and say what comes next
    jobfinder apply answer         store the candidate's answers (user-answers.json) to the agent's questions
    jobfinder apply confirm        record that the candidate approved the summary: the agent may submit
    jobfinder apply stop           end the run without sending (the posting stays cv_ready)
    jobfinder apply batch ID --test  a /test-apply run: sent every time, the posting keeps its status
    jobfinder apply test-choice    the posting /test-apply sends (data/apply/test.json) and its blockers
    jobfinder apply test-report    every test run: result, steps, minutes, tokens and ~$, with changes
    jobfinder apply guard          hook on the Playwright tools (hook input on stdin)
    jobfinder track batch          write batch files of postings to look up in Gmail (stage 5)
    jobfinder track save B R       store the mail agent's answer R for batch B; may set applied or response
    jobfinder track list           every application with its status and latest reply
    jobfinder cost report [--by B] tokens and estimated dollars of the LLM steps, by job, agent, command or day
    jobfinder ui [--port N]        the local web UI: postings table, settings, terminal panel (prints an address, no JSON)

Output is JSON so the calling agent can parse it.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from jobfinder import apply, apply_test, cost, guard, handoff, jobs, overview, safety, searches, send, track
from jobfinder.fetch import settings as fetch_settings
from jobfinder.fetch.runner import run_searches
from jobfinder.cv import settings as cv_settings
from jobfinder.cv import writer
from jobfinder.fetch.settings import DATA_SOURCES_ENV
from jobfinder.paths import (APPLY_SETTINGS_JSON, APPLY_TEST_JSON, COST_USAGE_JSON, CV_DIR, CV_SETTINGS_JSON, FETCH_SETTINGS_JSON,
                             JOBS_JSON, PROFILE_JSON, PROFILE_MD, RAW_DIR, SCORE_BATCHES_DIR,
                             SCORE_RESULTS_DIR, SCORE_SETTINGS_JSON, SEARCHES_JSON,
                             TRACK_BATCHES_DIR, TRACK_RESULTS_DIR, migrate_legacy_layout)
from jobfinder.profile.gaps import missing, set_pointer
from jobfinder.profile.render import render_profile_md
from jobfinder.profile.schema import validate
from jobfinder.score import batches, rules, shortlist, skill_gaps
from jobfinder.score import settings as score_settings
from jobfinder.sources import actors as actor_registry
from jobfinder.sources.apify import DATE_POSTED, SourceError, check, make_client, try_actor

SKILL_LEVELS = ("expert", "advanced", "intermediate")  # skills.technical[].level in the profile schema


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobfinder")
    groups = parser.add_subparsers(dest="group", required=True)
    profile = groups.add_parser("profile", help="Stage 1a: candidate profile").add_subparsers(
        dest="command", required=True)
    profile.add_parser("validate", help="Print schema errors")
    profile.add_parser("missing", help="Print fields still to ask and pending confirmations")
    set_cmd = profile.add_parser("set", help="Set a JSON value at a JSON Pointer")
    set_cmd.add_argument("pointer")
    set_cmd.add_argument("value", help="JSON value, e.g. '\"Istanbul\"', 'null', '[\"TR\"]'")
    profile.add_parser("render", help="Render profile.md from profile.json")
    add_skill = profile.add_parser("add-skill", help="Add a skill from `score gaps` by its key")
    add_skill.add_argument("key", help="the skill's key in `jobfinder score gaps`")
    add_skill.add_argument("level", choices=SKILL_LEVELS)
    add_skill.add_argument("--days", type=int, default=skill_gaps.DAYS,
                           help=f"the days `score gaps` was run with (default {skill_gaps.DAYS})")

    source = groups.add_parser("source", help="Stage 1b: job sources").add_subparsers(
        dest="command", required=True)
    source.add_parser("check", help="Verify APIFY_TOKEN and show this month's usage")
    test_cmd = source.add_parser("test", help="Fetch a few postings and save the raw JSON")
    test_cmd.add_argument("--data-source", default="APIFY_LINKEDIN_SCRAPER",
                          help="actor name from config/apify_actors.json")
    test_cmd.add_argument("--keywords", required=True)
    test_cmd.add_argument("--location", required=True)
    test_cmd.add_argument("--limit", type=int, default=5)
    test_cmd.add_argument("--date-posted", default="pastWeek", choices=DATE_POSTED)
    source.add_parser("actors", help="List the Apify actors that can be used")

    search = groups.add_parser("search", help="Stage 2a: search queries").add_subparsers(
        dest="command", required=True)
    validate_cmd = search.add_parser("validate", help="Check searches.json and show per-run totals")
    _add_data_source(validate_cmd)

    fetch = groups.add_parser("fetch", help="Stage 2a: fetch postings").add_subparsers(
        dest="command", required=True)
    configure = fetch.add_parser("configure", help="Save the actors and the date filter")
    configure.add_argument("--date-posted", choices=DATE_POSTED,
                           help="anyTime (all postings) or a filter")
    configure.add_argument("--data-source", action="append",
                           help="actor name from config/apify_actors.json, repeatable, or 'all'")
    fetch.add_parser("settings", help="Show the saved run settings")
    run_cmd = fetch.add_parser("run", help="Run every enabled search once with the saved settings")
    run_cmd.add_argument("--date-posted", choices=DATE_POSTED, help="override the saved filter")
    _add_data_source(run_cmd)

    job_cmds = groups.add_parser("jobs", help="Stages 2b-2c: job store and safety check").add_subparsers(
        dest="command", required=True)
    import_cmd = job_cmds.add_parser("import", help="Map raw actor output into jobs.json")
    import_cmd.add_argument("raw", nargs="*", help="raw files (default: every file under "
                                                   "data/fetch/raw/, oldest first)")
    dedupe_cmd = job_cmds.add_parser("dedupe", help="Remove reposts already in jobs.json")
    dedupe_cmd.add_argument("--dry-run", action="store_true", help="only list what would go")
    check_cmd = job_cmds.add_parser("check", help="Safety check of postings not checked yet")
    check_cmd.add_argument("--all", action="store_true",
                           help="check every posting again (after changing config/safety.json)")
    approve_cmd = job_cmds.add_parser("approve", help="Mark postings approved for the CV stage")
    approve_cmd.add_argument("ids", nargs="+", help="job ids, e.g. linkedin:4012345678")

    list_jobs = job_cmds.add_parser("list", help="Every posting with its pipeline stage")
    list_jobs.add_argument("--stage", action="append", choices=overview.STAGES,
                           help="only postings at this stage, repeatable")
    list_jobs.add_argument("--top", type=int, help="at most this many postings")

    safety_cmds = groups.add_parser("safety", help="Stage 2c: safety check").add_subparsers(
        dest="command", required=True)
    safety_cmds.add_parser("model", help="Download (once) and verify the prompt injection model")

    score = groups.add_parser("score", help="Stage 2d: filter and score postings").add_subparsers(
        dest="command", required=True)
    score_configure = score.add_parser("configure", help="Save the scoring settings")
    score_configure.add_argument("--matcher-model", choices=score_settings.MATCHER_MODELS)
    score_configure.add_argument("--min-score", type=int, help="shortlist threshold, 0-100")
    score_configure.add_argument("--shortlist-size", type=int, help="postings on the shortlist")
    score_configure.add_argument("--max-per-run", type=int, help="postings scored per run")
    score.add_parser("settings", help="Show the saved scoring settings")
    filter_cmd = score.add_parser("filter", help="Rule filter of postings not filtered yet")
    filter_cmd.add_argument("--all", action="store_true",
                            help="filter every posting again (after changing the profile)")
    batch_cmd = score.add_parser("batch", help="Write batch files for the extract or match agents")
    batch_cmd.add_argument("step", choices=batches.STEPS)
    batch_cmd.add_argument("--size", type=int, help="postings per batch (default: "
                           + ", ".join(f"{k} {v}" for k, v in batches.BATCH_SIZE.items()) + ")")
    save_cmd = score.add_parser("save", help="Store an agent's answer for one batch")
    save_cmd.add_argument("batch", help="the batch file, data/score/batches/<step>-<n>.json")
    save_cmd.add_argument("result", help="the agent's JSON answer, data/score/results/<step>-<n>.json")
    list_cmd = score.add_parser("list", help="The shortlist, best first")
    list_cmd.add_argument("--min-score", type=int, help="default: min_score in the settings")
    list_cmd.add_argument("--top", type=int, help="default: shortlist_size in the settings")
    list_cmd.add_argument("--all", action="store_true",
                          help="every posting at or above the threshold, not only the shortlist size")
    gaps_cmd = score.add_parser("gaps", help="Skills the postings ask for that the profile lacks")
    gaps_cmd.add_argument("--days", type=int, default=skill_gaps.DAYS,
                          help=f"postings added in the last N days (default {skill_gaps.DAYS})")
    gaps_cmd.add_argument("--top", type=int, default=skill_gaps.TOP,
                          help=f"skills to list (default {skill_gaps.TOP})")

    cv = groups.add_parser("cv", help="Stage 3b: CV and cover letter").add_subparsers(
        dest="command", required=True)
    cv_configure = cv.add_parser("configure", help="Save the CV settings")
    cv_configure.add_argument("--writer-model", choices=cv_settings.WRITER_MODELS)
    cv_configure.add_argument("--review", choices=("true", "false"),
                              help="let the cv-reviewer agent revise each saved CV and letter")
    cv.add_parser("settings", help="Show the saved CV settings")
    cv_batch = cv.add_parser("batch", help="Write the writer agent's input for approved postings")
    cv_batch.add_argument("--id", help="only this posting (approved, or cv_ready to write it again)")
    cv_save = cv.add_parser("save", help="Check the agent's answer and render the documents")
    cv_save.add_argument("input", help="the input file, data/cv/<day>/<posting>/input.json")
    cv_save.add_argument("answer", help="the agent's JSON answer, answer.json next to the input")
    cv_save.add_argument("--review", action="store_true",
                         help="the answer is the cv-reviewer's revision (review.json): no second try")

    apply_cmds = groups.add_parser("apply", help="Stage 4: postings to apply to, record an application").add_subparsers(
        dest="command", required=True)
    apply_configure = apply_cmds.add_parser("configure", help="Save the job-sender agent's settings")
    apply_configure.add_argument("--confirm-before-submit", choices=("true", "false"),
                                 help="true: the candidate approves every application before it is sent")
    apply_configure.add_argument("--max-per-day", type=int, help="applications the agent sends a day")
    apply_configure.add_argument("--sender-model", choices=send.SENDER_MODELS,
                                 help="the model the job-sender agent runs on")
    apply_cmds.add_parser("settings", help="Show the job-sender agent's settings")
    apply_cmds.add_parser("list", help="Postings whose CV and cover letter are ready, with links")
    mark_cmd = apply_cmds.add_parser("mark", help="Record that the application was sent")
    mark_cmd.add_argument("id")
    mark_cmd.add_argument("--date", default="today", help="today (default), yesterday or YYYY-MM-DD")
    send_batch = apply_cmds.add_parser("batch", help="Start the job-sender agent's run for one posting")
    send_batch.add_argument("id")
    send_batch.add_argument("--test", action="store_true",
                            help="a /test-apply run: remembered, measured, sent without a summary to confirm")
    apply_cmds.add_parser("test-choice", help="The posting /test-apply sends every time")
    apply_cmds.add_parser("test-report", help="Every test run with its tokens, cost and time")
    apply_cmds.add_parser("step", help="Check what the agent returned and say what comes next")
    apply_cmds.add_parser("answer", help="Store the candidate's answers to the agent's questions")
    apply_cmds.add_parser("confirm", help="Record that the candidate approved the summary")
    apply_cmds.add_parser("stop", help="End the run without sending")
    apply_cmds.add_parser("guard", help="Hook on the Playwright tools (input on stdin)")

    track_cmds = groups.add_parser("track", help="Stage 5: replies to applications").add_subparsers(
        dest="command", required=True)
    track_cmds.add_parser("batch", help="Write batch files of applications for the mail agent")
    track_save = track_cmds.add_parser("save", help="Store the mail agent's answer for one batch")
    track_save.add_argument("batch", help="the batch file, data/track/batches/track-<n>.json")
    track_save.add_argument("result", help="the agent's JSON answer, data/track/results/track-<n>.json")
    track_cmds.add_parser("list", help="Every application with its status and latest reply")
    track_stale = track_cmds.add_parser("stale", help="Applications with no mail for N days")
    track_stale.add_argument("--days", type=int, default=track.STALE_DAYS,
                             help=f"days without mail (default {track.STALE_DAYS})")
    track_close = track_cmds.add_parser("close", help="Mark applications no_response")
    track_close.add_argument("ids", nargs="*", metavar="ID", help="postings to close")
    track_close.add_argument("--stale", action="store_true", help="close every stale application")
    track_close.add_argument("--days", type=int, default=track.STALE_DAYS,
                             help=f"with --stale: days without mail (default {track.STALE_DAYS})")

    track_interview = track_cmds.add_parser("interview", help="Move applications to the next interview round")
    track_interview.add_argument("ids", nargs="+", metavar="ID", help="postings to move")
    track_interview.add_argument("--back", action="store_true", help="one round back (round 1 -> applied)")

    ui_cmd = groups.add_parser("ui", help="Open the local web UI: postings table, settings, terminal panel")
    ui_cmd.add_argument("--port", type=int, help="default 8765, or the next free port after it")
    ui_cmd.add_argument("--no-open", action="store_true", help="do not open the browser")

    cost_cmds = groups.add_parser("cost", help="Every stage: tokens and estimated cost").add_subparsers(
        dest="command", required=True)
    cost_report = cost_cmds.add_parser("report", help="Tokens and estimated dollars of the LLM steps")
    cost_report.add_argument("--by", choices=cost.BY, default="job", help="group by (default: job)")
    cost_report.add_argument("--since", help="only runs from this day on, YYYY-MM-DD")
    cost_report.add_argument("--job", action="append", dest="jobs", metavar="ID",
                             help="only these postings (repeatable)")

    args = parser.parse_args(argv)
    if args.group == "apply" and args.command == "guard":
        return _apply_guard(args)  # a hook: runs on every browser call, so nothing else first
    for move in migrate_legacy_layout():
        print(f"moved data/{move}", file=sys.stderr)
    if args.group == "ui":
        from jobfinder.ui import server  # only this command needs the server

        return server.serve(args.port or 8765, explicit=args.port is not None, open_browser=not args.no_open)
    if args.group == "source":
        return _source(args)
    if args.group == "search":
        return _search_validate(args)
    if args.group == "fetch":
        return {"configure": _fetch_configure, "settings": _fetch_settings, "run": _fetch_run}[
            args.command](args)
    if args.group == "safety":
        return _safety_model(args)
    if args.group == "jobs":
        return {"import": _jobs_import, "dedupe": _jobs_dedupe, "check": _jobs_check,
                "approve": _jobs_approve, "list": _jobs_list}[args.command](args)
    if args.group == "score":
        return {"configure": _score_configure, "settings": _score_settings,
                "filter": _score_filter, "batch": _score_batch, "save": _score_save,
                "list": _score_list, "gaps": _score_gaps}[args.command](args)
    if args.group == "cv":
        return {"configure": _cv_configure, "settings": _cv_settings, "batch": _cv_batch,
                "save": _cv_save}[args.command](args)
    if args.group == "apply":
        return {"configure": _apply_configure, "settings": _apply_settings,
                "list": _apply_list, "mark": _apply_mark, "batch": _apply_batch, "step": _apply_step,
                "answer": _apply_answer, "confirm": _apply_confirm, "stop": _apply_stop,
                "test-choice": _apply_test_choice, "test-report": _apply_test_report,
                "guard": _apply_guard}[args.command](args)
    if args.group == "track":
        return {"batch": _track_batch, "save": _track_save, "list": _track_list,
                "stale": _track_stale, "close": _track_close,
                "interview": _track_interview}[args.command](args)
    if args.group == "cost":
        return _cost_report(args)
    if not PROFILE_JSON.exists():
        return _fail(f"{PROFILE_JSON} not found; run /create-profile first.")
    profile_data = json.loads(PROFILE_JSON.read_text(encoding="utf-8"))
    return {"validate": _validate, "missing": _missing, "set": _set, "render": _render,
            "add-skill": _add_skill}[
        args.command](profile_data, args)


def _validate(profile: dict, args) -> int:
    errors = validate(profile)
    _print({"valid": not errors, "errors": errors})
    return 0 if not errors else 1


def _missing(profile: dict, args) -> int:
    gaps = missing(profile)
    pending = profile.get("meta", {}).get("pending_confirmations", [])
    _print({"pending_confirmations": pending, "missing": gaps,
            "complete": not gaps and not pending and not validate(profile)})
    return 0


def _set(profile: dict, args) -> int:
    try:
        value = json.loads(args.value)
    except json.JSONDecodeError as exc:
        return _fail(f"VALUE must be JSON (strings in double quotes): {exc}")
    try:
        set_pointer(profile, args.pointer, value)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        return _fail(f"Cannot set {args.pointer}: {exc!r}")
    profile.setdefault("meta", {})["updated_at"] = date.today().isoformat()
    PROFILE_JSON.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    errors = [e for e in validate(profile) if e.startswith(args.pointer)]
    _print({"ok": True, "errors_at_pointer": errors})
    return 0


def _add_skill(profile: dict, args) -> int:
    """Append a skill the postings ask for to /skills/technical, found by its key in the gap
    report, so its name (posting text) is written by code and never passed through a shell."""
    entry = skill_gaps.find(jobs.load(JOBS_JSON), profile, args.key, args.days)
    if entry is None:
        _print({"ok": False, "error": f"no missing skill with key {args.key!r} in `score gaps --days {args.days}`"})
        return 1
    skill = {"name": entry["skill"], "level": args.level}
    set_pointer(profile, "/skills/technical/-", skill)
    profile.setdefault("meta", {})["updated_at"] = date.today().isoformat()
    PROFILE_JSON.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    errors = [e for e in validate(profile) if e.startswith("/skills")]
    _print({"ok": True, "added": skill, "errors_at_pointer": errors})
    return 0


def _render(profile: dict, args) -> int:
    if errors := validate(profile):
        _print({"ok": False, "errors": errors})
        return 1
    PROFILE_MD.write_text(render_profile_md(profile), encoding="utf-8")
    _print({"ok": True, "path": str(PROFILE_MD)})
    return 0


def _source(args) -> int:
    try:
        if args.command == "actors":
            _print({"ok": True, "actors": actor_registry.load()})
            return 0
        if args.command == "check":
            result = check(make_client())
        else:
            actor = actor_registry.select(actor_registry.load(), [args.data_source])[0]
            if actor.get("kind", "apify") != "apify":
                raise SourceError(f"{actor['name']} needs no token and has no test run: use /search.")
            result = try_actor(make_client(), actor, args.keywords, args.location, args.limit,
                               args.date_posted)
    except (SourceError, safety.SafetyError, guard.GuardError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print(result)
    return 0


def _load_searches() -> tuple[dict | None, list[str]]:
    try:
        data = searches.load(SEARCHES_JSON)
    except FileNotFoundError as exc:
        return None, [str(exc)]
    except json.JSONDecodeError as exc:
        return None, [f"{SEARCHES_JSON} is not valid JSON: {exc}"]
    return data, searches.validate(data)


def _search_validate(args) -> int:
    data, errors = _load_searches()
    if errors:
        _print({"valid": False, "errors": errors})
        return 1
    try:
        actors = actor_registry.select(actor_registry.load(), _data_sources(args))
    except SourceError as exc:
        _print({"valid": False, "errors": [str(exc)]})
        return 1
    _print({"valid": True, "errors": [], **searches.summary(data, actors)})
    return 0


def _fetch_run(args) -> int:
    data, errors = _load_searches()
    if errors:
        _print({"ok": False, "error": "; ".join(errors)})
        return 1
    try:
        actors = actor_registry.select(actor_registry.load(), _data_sources(args))
        date_posted = args.date_posted or fetch_settings.load(FETCH_SETTINGS_JSON)["date_posted"]
        client = make_client() if actor_registry.uses_apify(actors) else None
        result = run_searches(client, data, actors, date_posted)
    except SourceError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print(result)
    return 0 if result["ok"] else 1


def _add_data_source(cmd) -> None:
    cmd.add_argument("--data-source", action="append",
                     help="override: actor name, repeatable, or 'all' "
                          f"(default: ${DATA_SOURCES_ENV}, else data/fetch/settings.json)")


def _data_sources(args) -> list[str]:
    """--data-source, else $JOBFINDER_DATA_SOURCES, else the saved settings."""
    if args.data_source:
        return args.data_source
    env = [name.strip() for name in os.environ.get(DATA_SOURCES_ENV, "").split(",") if name.strip()]
    return env or fetch_settings.load(FETCH_SETTINGS_JSON)["data_sources"]


def _fetch_configure(args) -> int:
    current = fetch_settings.load(FETCH_SETTINGS_JSON)
    try:
        if args.data_source:
            chosen = actor_registry.select(actor_registry.load(), args.data_source)
            all_chosen = "all" in args.data_source
            current["data_sources"] = ["all"] if all_chosen else [a["name"] for a in chosen]
        if args.date_posted:
            current["date_posted"] = args.date_posted
        saved = fetch_settings.save(current, FETCH_SETTINGS_JSON)
    except SourceError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, "path": str(FETCH_SETTINGS_JSON), **saved})
    return 0


def _fetch_settings(args) -> int:
    _print({"ok": True, "path": str(FETCH_SETTINGS_JSON), "exists": FETCH_SETTINGS_JSON.exists(),
            **fetch_settings.load(FETCH_SETTINGS_JSON)})
    return 0


def _jobs_import(args) -> int:
    paths = [Path(p) for p in args.raw] or sorted(RAW_DIR.glob("*/*.json"), key=lambda p: p.name)
    try:
        actors = actor_registry.load()
        store = jobs.load(JOBS_JSON)
        files = [jobs.import_raw(store, path, actors) for path in paths]
    except (SourceError, OSError, json.JSONDecodeError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    jobs.save(store, JOBS_JSON)
    done = [f for f in files if "error" not in f]
    _print({"ok": len(done) == len(files), "path": str(JOBS_JSON), "files": files,
            **{f"total_{key}": sum(f[key] for f in done)
               for key in ("count", "new", "already_saved", "reposts", "invalid")}})
    return 0 if len(done) == len(files) else 1


def _jobs_dedupe(args) -> int:
    store = jobs.load(JOBS_JSON)
    before = len(store["jobs"])
    removed = jobs.dedupe(store)
    if removed and not args.dry_run:
        jobs.save(store, JOBS_JSON)
    _print({"ok": True, "path": str(JOBS_JSON), "dry_run": args.dry_run, "before": before,
            "removed": len(removed), "removed_ids": removed, "after": len(store["jobs"])})
    return 0


def _jobs_check(args) -> int:
    try:
        rules = safety.load_rules()
        scorer = safety.load_scorer(rules)
    except (safety.SafetyError, guard.GuardError, OSError, json.JSONDecodeError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    store = jobs.load(JOBS_JSON)
    counts = safety.check_store(store, rules, scorer, recheck=args.all)
    if counts["checked"]:
        jobs.save(store, JOBS_JSON)
    _print({"ok": True, "path": str(JOBS_JSON), **counts})
    return 0


def _jobs_approve(args) -> int:
    store = jobs.load(JOBS_JSON)
    result = jobs.approve(store, args.ids)
    if result["approved"]:
        jobs.save(store, JOBS_JSON)
    _print({"ok": True, "path": str(JOBS_JSON), **result})
    return 0


def _safety_model(args) -> int:
    spec = safety.load_rules()["model"]
    try:
        try:
            result = guard.verify(spec)
            downloaded = False
        except guard.GuardError:
            result = guard.download(spec)
            downloaded = True
    except Exception as exc:  # network, Hugging Face access, hash mismatch
        _print({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    # Self-test: an ordinary posting should score low, an injection high.
    score = guard.load_scorer(spec)
    self_test = {"benign": round(score(guard.SAMPLE_BENIGN), 4),
                 "injection": round(score(guard.SAMPLE_INJECTION), 4)}
    ok = self_test["benign"] < spec["threshold"] <= self_test["injection"]
    _print({"ok": ok, "downloaded": downloaded, "repo": spec["repo"],
            "revision": spec["revision"], "threshold": spec["threshold"], **result,
            "self_test": self_test})
    return 0 if ok else 1


def _score_configure(args) -> int:
    current = score_settings.load(SCORE_SETTINGS_JSON)
    for key in score_settings.DEFAULTS:
        if (value := getattr(args, key)) is not None:
            current[key] = value
    try:
        saved = score_settings.save(current, SCORE_SETTINGS_JSON)
    except score_settings.SettingsError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, "path": str(SCORE_SETTINGS_JSON), **saved})
    return 0


def _score_settings(args) -> int:
    _print({"ok": True, "path": str(SCORE_SETTINGS_JSON), "exists": SCORE_SETTINGS_JSON.exists(),
            **score_settings.load(SCORE_SETTINGS_JSON)})
    return 0


def _score_filter(args) -> int:
    profile = _scoring_profile()
    if profile is None:
        return 1
    store = jobs.load(JOBS_JSON)
    counts = rules.check_store(store, profile, recheck=args.all,
                               exclude=fetch_settings.load(FETCH_SETTINGS_JSON)["exclude_words"])
    if counts["checked"] or counts["same_posting_changed"]:
        jobs.save(store, JOBS_JSON)
    _print({"ok": True, "path": str(JOBS_JSON), **counts})
    return 0


def _score_batch(args) -> int:
    if args.size is not None and args.size < 1:
        return _fail("--size must be 1 or more")
    if args.step == "match" and not PROFILE_MD.exists():
        _print({"ok": False, "error": f"{PROFILE_MD} not found; run /create-profile first."})
        return 1
    limit = score_settings.load(SCORE_SETTINGS_JSON)["max_per_run"]
    profile_md = PROFILE_MD.read_text(encoding="utf-8") if args.step == "match" else None
    made = batches.make(jobs.load(JOBS_JSON), args.step, limit, SCORE_BATCHES_DIR,
                        SCORE_RESULTS_DIR, args.size, profile_md)
    _print({"ok": True, **made})
    return 0


def _score_save(args) -> int:
    result = handoff.save_score(Path(args.batch), Path(args.result), jobs_path=JOBS_JSON,
                                 profile_path=PROFILE_JSON, settings_path=SCORE_SETTINGS_JSON)
    _print(result)
    return 0 if result["ok"] else 1


def _scoring_profile() -> dict | None:
    """The profile the rule filter needs, or None after printing why it cannot be used."""
    profile, error = handoff.load_profile(PROFILE_JSON)
    if error:
        _print(error)
    return profile


def _score_list(args) -> int:
    saved = score_settings.load(SCORE_SETTINGS_JSON)
    min_score = saved["min_score"] if args.min_score is None else args.min_score
    top = None if args.all else saved["shortlist_size"] if args.top is None else args.top
    entries = shortlist.ranked(jobs.load(JOBS_JSON), min_score, top)
    _print({"ok": True, "min_score": min_score, "count": len(entries), "jobs": entries})
    return 0


def _score_gaps(args) -> int:
    if args.days < 1 or args.top < 1:
        _print({"ok": False, "error": "--days and --top must be at least 1"})
        return 1
    profile = _scoring_profile()
    if profile is None:
        return 1
    _print({"ok": True, **skill_gaps.report(jobs.load(JOBS_JSON), profile, args.days, args.top)})
    return 0


def _cv_configure(args) -> int:
    current = cv_settings.load(CV_SETTINGS_JSON)
    if args.writer_model:
        current["writer_model"] = args.writer_model
    if args.review is not None:
        current["review"] = args.review == "true"
    try:
        saved = cv_settings.save(current, CV_SETTINGS_JSON)
    except cv_settings.SettingsError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, "path": str(CV_SETTINGS_JSON), **saved})
    return 0


def _cv_settings(args) -> int:
    _print({"ok": True, "path": str(CV_SETTINGS_JSON), "exists": CV_SETTINGS_JSON.exists(),
            **cv_settings.load(CV_SETTINGS_JSON)})
    return 0


def _cv_batch(args) -> int:
    profile = _scoring_profile()
    if profile is None:
        return 1
    try:
        made = writer.make(jobs.load(JOBS_JSON), profile, CV_DIR, args.id)
    except writer.WriterError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, **made})
    return 0


def _cv_save(args) -> int:
    result = handoff.save_cv(Path(args.input), Path(args.answer), jobs_path=JOBS_JSON,
                              profile_path=PROFILE_JSON, settings_path=CV_SETTINGS_JSON, cv_dir=CV_DIR,
                              reviewed=args.review)
    _print(result)
    return 0 if result["ok"] else 1


def _jobs_list(args) -> int:
    _print({"ok": True, "path": str(JOBS_JSON),
            **overview.listing(jobs.load(JOBS_JSON), args.stage, args.top)})
    return 0


def _apply_configure(args) -> int:
    changes = {}
    if args.confirm_before_submit is not None:
        changes["confirm_before_submit"] = args.confirm_before_submit == "true"
    if args.max_per_day is not None:
        changes["max_per_day"] = args.max_per_day
    if args.sender_model is not None:
        changes["sender_model"] = args.sender_model
    try:
        saved = send.save_settings(changes, APPLY_SETTINGS_JSON)
    except send.SendError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, "path": str(APPLY_SETTINGS_JSON), **saved})
    return 0


def _apply_settings(args) -> int:
    try:
        saved = send.load_settings(APPLY_SETTINGS_JSON)
    except send.SendError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, "path": str(APPLY_SETTINGS_JSON), "exists": APPLY_SETTINGS_JSON.exists(),
            **saved})
    return 0


def _apply_list(args) -> int:
    store = jobs.load(JOBS_JSON)
    entries = apply.ready(store)
    by_id = {job["id"]: job for job in store["jobs"]}
    for entry in entries:  # why /apply cannot send it (empty: it can)
        entry["agent_blockers"] = send.blockers(by_id[entry["id"]])
    try:
        limit = send.load_settings()["max_per_day"]
    except send.SendError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, "count": len(entries), "jobs": entries,
            "agent": {"sent_today": send.sent_on(store), "max_per_day": limit}})
    return 0


def _apply_mark(args) -> int:
    store = jobs.load(JOBS_JSON)
    try:
        day = apply.parse_day(args.date)
        marked = apply.mark_applied(apply.find(store, args.id), None if args.date == "today" else day)
    except apply.ApplyError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    jobs.save(store, JOBS_JSON)
    _print({"ok": True, "path": str(JOBS_JSON), **marked})
    return 0


def _apply_batch(args) -> int:
    profile = _scoring_profile()
    if profile is None:
        return 1
    store = jobs.load(JOBS_JSON)
    try:
        settings = send.load_settings(APPLY_SETTINGS_JSON)
        test = apply_test.stamp(settings) if args.test else None
        made = send.make(store, profile, args.id, safety.load_rules(), settings, test=test)
        if test:
            apply_test.pick(store, args.id, APPLY_TEST_JSON)
    except (apply.ApplyError, send.SendError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, **made, **({"test": test} if test else {})})
    return 0


def _apply_test_choice(args) -> int:
    _print({"ok": True, **apply_test.choice(jobs.load(JOBS_JSON), APPLY_TEST_JSON)})
    return 0


def _apply_test_report(args) -> int:
    try:
        with cost._locked(COST_USAGE_JSON) as usage:
            cost.refresh(usage)
        result = apply_test.report(usage)
    except (cost.CostError, OSError, json.JSONDecodeError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, **result})
    return 0


def _apply_step(args) -> int:
    store = jobs.load(JOBS_JSON)
    try:
        result = send.step(store, safety.load_rules(), send.load_settings(APPLY_SETTINGS_JSON))
    except (apply.ApplyError, send.SendError, OSError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    if result.get("result") == "submitted":
        jobs.save(store, JOBS_JSON)
    _print({"ok": True, **result})
    return 0


def _apply_answer(args) -> int:
    try:
        result = send.answer()
    except (send.SendError, OSError, json.JSONDecodeError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, **result})
    return 0


def _apply_confirm(args) -> int:
    try:
        result = send.confirm()
    except send.SendError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, **result})
    return 0


def _apply_stop(args) -> int:
    try:
        result = send.stop()
    except send.SendError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, **result})
    return 0


def _apply_guard(args) -> int:
    return send.hook_main()


def _track_batch(args) -> int:
    min_score = score_settings.load(SCORE_SETTINGS_JSON)["min_score"]
    made = track.make(jobs.load(JOBS_JSON), TRACK_BATCHES_DIR, TRACK_RESULTS_DIR,
                      min_score=min_score)
    _print({"ok": True, **made})
    return 0


def _track_save(args) -> int:
    result = handoff.save_track(Path(args.batch), Path(args.result), jobs_path=JOBS_JSON,
                                 settings_path=SCORE_SETTINGS_JSON)
    _print(result)
    return 0 if result["ok"] else 1


def _track_list(args) -> int:
    entries = track.applications(jobs.load(JOBS_JSON))
    _print({"ok": True, "count": len(entries), "jobs": entries})
    return 0


def _track_stale(args) -> int:
    if args.days < 1:
        _print({"ok": False, "error": "--days must be 1 or more"})
        return 1
    found = track.stale(jobs.load(JOBS_JSON), args.days)
    _print({"ok": True, "days": args.days, "count": len(found), "jobs": found})
    return 0


def _track_close(args) -> int:
    if args.days < 1 or bool(args.ids) == args.stale:
        _print({"ok": False, "error": "give posting ids or --stale (not both), and --days of 1 or more"})
        return 1
    with handoff.jobs_lock(JOBS_JSON):
        store = jobs.load(JOBS_JSON)
        ids = [job["id"] for job in track.stale(store, args.days)] if args.stale else args.ids
        result = track.close(store, ids)
        if result["closed"]:
            jobs.save(store, JOBS_JSON)
    _print({"ok": True, "path": str(JOBS_JSON), **result})
    return 0


def _track_interview(args) -> int:
    with handoff.jobs_lock(JOBS_JSON):
        store = jobs.load(JOBS_JSON)
        result = track.interview(store, args.ids, args.back)
        if result["moved"]:
            jobs.save(store, JOBS_JSON)
    _print({"ok": True, "path": str(JOBS_JSON), **result})
    return 0


def _cost_report(args) -> int:
    try:
        since = date.fromisoformat(args.since) if args.since else None
    except ValueError:
        return _fail("--since must be a day, YYYY-MM-DD")
    try:
        with cost._locked(COST_USAGE_JSON) as usage:
            refreshed = cost.refresh(usage)
        store = jobs.load(JOBS_JSON) if JOBS_JSON.exists() else None
        result = cost.report(usage, store, args.by, since, args.jobs)
    except (cost.CostError, OSError, json.JSONDecodeError) as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({**result, "refreshed": refreshed})
    return 0


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))  # compact: read by a model


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
