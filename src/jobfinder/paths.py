"""Well-known file locations, relative to the repository root.

Personal data lives under data/, one folder per stage. Nothing under data/ is committed:
only the folders (each with a .gitkeep) are in the repository. Formats: docs/data.md.

    data/profile/    stage 1a: CV, profile.json, profile.md
    data/searches/   stage 2a: searches.json (search queries and per-query limits)
    data/fetch/      stage 2a: settings.json (actors, date filter), raw/<DATA_SOURCE>/ (raw items),
                     runs/ (one summary per run)
    data/jobs/       stage 2a+ (dedupe: stage 2b, safety: stage 2c): jobs.json, every posting in the standard shape (schemas/job.schema.json)
    data/safety/     stage 2c: model/ (the local prompt injection classifier, downloaded once)
    data/score/      stage 2d: settings.json (matcher model, shortlist threshold and size, postings per run),
                     batches/ (postings for the reading agents), results/ (what the agents returned)
    data/cv/         stage 3b: settings.json (writer model), <DD-MM-YYYY>/<company>-<source_id>/ per approved posting
                     (the writer agent's input and answer, the CV and cover letter)
    data/apply/      stage 4, sent by the job-sender agent: current.json (the run in progress, read by the
                     hook), answers.json (the candidate's answers to form questions), settings.json (by hand),
                     <DD-MM-YYYY>/<company>-<source_id>/ per posting (the agent's input, its answers, a log),
                     test.json (the posting of /test-apply) and tests/<YYYY-MM-DD-HHMMSS>-<posting>/ per test
    data/track/      stage 5: batches/ (applications to look up in Gmail), results/ (what the mail agent returned)
    data/cost/       every stage: usage.json (tokens and estimated dollars per agent run and command, written
                     by the hook), errors.log
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas" / "profile.schema.json"
JOB_SCHEMA_PATH = ROOT / "schemas" / "job.schema.json"
ACTORS_JSON = ROOT / "config" / "apify_actors.json"
SAFETY_JSON = ROOT / "config" / "safety.json"
PRICING_JSON = ROOT / "config" / "pricing.json"
CV_CHECK_JSON = ROOT / "config" / "cv_check.json"
ENV_FILE = ROOT / ".env"
DATA_DIR = ROOT / "data"

PROFILE_DIR = DATA_DIR / "profile"
PROFILE_JSON = PROFILE_DIR / "profile.json"
PROFILE_MD = PROFILE_DIR / "profile.md"

SEARCHES_DIR = DATA_DIR / "searches"
SEARCHES_JSON = SEARCHES_DIR / "searches.json"

FETCH_DIR = DATA_DIR / "fetch"
RAW_DIR = FETCH_DIR / "raw"
RUNS_DIR = FETCH_DIR / "runs"
FETCH_SETTINGS_JSON = FETCH_DIR / "settings.json"

JOBS_DIR = DATA_DIR / "jobs"
JOBS_JSON = JOBS_DIR / "jobs.json"

SAFETY_DIR = DATA_DIR / "safety"
SAFETY_MODEL_DIR = SAFETY_DIR / "model"

SCORE_DIR = DATA_DIR / "score"
SCORE_SETTINGS_JSON = SCORE_DIR / "settings.json"
SCORE_BATCHES_DIR = SCORE_DIR / "batches"
SCORE_RESULTS_DIR = SCORE_DIR / "results"

CV_DIR = DATA_DIR / "cv"
CV_SETTINGS_JSON = CV_DIR / "settings.json"

APPLY_DIR = DATA_DIR / "apply"
APPLY_RUN_JSON = APPLY_DIR / "current.json"
APPLY_ANSWERS_JSON = APPLY_DIR / "answers.json"
APPLY_SETTINGS_JSON = APPLY_DIR / "settings.json"
APPLY_TRUSTED_JSON = APPLY_DIR / "trusted_domains.json"
APPLY_TEST_JSON = APPLY_DIR / "test.json"  # the posting /test-apply sends every time

TRACK_DIR = DATA_DIR / "track"
TRACK_BATCHES_DIR = TRACK_DIR / "batches"
TRACK_RESULTS_DIR = TRACK_DIR / "results"

COST_DIR = DATA_DIR / "cost"
COST_USAGE_JSON = COST_DIR / "usage.json"
COST_ERRORS_LOG = COST_DIR / "errors.log"


def migrate_legacy_layout(data_dir: Path = DATA_DIR) -> list[str]:
    """Move files from the flat stage 1a-1b layout into the per-stage folders.

    Never overwrites: a file whose new location already exists is left where it is.
    Returns one "old -> new" line per moved path.
    """
    moves = [(data_dir / name, data_dir / "profile" / name) for name in ("profile.json", "profile.md")]
    moves += [(cv, data_dir / "profile" / cv.name)
              for pattern in ("*.pdf", "*.docx") for cv in sorted(data_dir.glob(pattern))]
    moves.append((data_dir / "raw", data_dir / "fetch" / "raw"))
    moved = []
    for old, new in moves:
        if old.exists() and not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(old, new)
            moved.append(f"{old.relative_to(data_dir)} -> {new.relative_to(data_dir)}")
    return moved
