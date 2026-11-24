# Commands and tools

Every slash command and every `jobfinder` tool. How they fit together is in [architecture.md](architecture.md); the files they read and write are in [data.md](data.md).

## Slash commands

Run them in Claude Code (`claude` in the repository). The candidate only needs the six marked **entry point**: one per stage, plus `/show-list`. The others are the steps they run (with the Skill tool) and can also be run alone, e.g. `/setup-job-scoring` to change only the threshold.

| Command | Stage | Does | Writes |
|---------|-------|------|--------|
| `/setup` **entry point** | 1 | Runs every setup step in order: `/create-profile`, `/setup-sources` (Apify token and a test fetch; skipped when the candidate has no token and picks only the free sources without a token: FREEHIRE, GREENHOUSE, LEVER, ASHBY), `/setup-job-search`, `/setup-job-scoring`, `/setup-cv`, then the `job-sender` settings (confirm up front only or every application, daily limit) and checks of the injection model, Gmail and the browser. When something is already set up it asks which parts to redo. | through the commands and tools it calls |
| `/search` **entry point** | 2 | Runs `/execute-job-search` (fetch, map and dedupe, safety check), then `/execute-job-scoring` (filter and score every posting not scored yet, shortlist), then `/write-cv` for the best ones. Asks nothing until `/write-cv` asks for how many postings (none stops the run). | through the commands it calls |
| `/write-cv [job id ... \| all \| N]` **entry point** | 3 | Asks for how many scored postings without a CV (the first 10 by default, all when there are 10 or fewer, the first 20, or custom; best score first), then a tailored CV and cover letter are written as PDF for each, with no review step: calls `/approve-jobs`, then `/execute-cv`. | through the commands it calls |
| `/apply [job id ... \| all \| N]` **entry point** | 4 | Lists the postings whose documents are ready, marks those the agent may send (safety `pass`, documents on disk, an https link), asks once up front how many to apply to (the first 10 by default, best score first, within the daily limit) and confirms the list; then, one at a time, the `job-sender` agent fills in each application in a Playwright browser window (LinkedIn Easy Apply or the company's apply page). Asks the required fields the profile does not answer (and saves the answers unless they are for that application only) and shows each summary; with `confirm_before_submit` on it also waits for "Gönder" on each. Needs Node and the Playwright MCP server of `.mcp.json` (a visible window, own profile; `npx -y @playwright/mcp@0.0.83 install-browser chromium` once, and a one-time sign-in in that profile for LinkedIn). A stopped posting stays `cv_ready`. | `data/apply/`, `data/jobs/jobs.json` (`status`, `application`) |
| `/test-apply` | 4 | A repeatable test of `/apply`: the `job-sender` agent applies to the one posting picked for tests (asked the first time, remembered in `data/apply/test.json`), fills in the whole form and submits it every time, without a summary or any question (except a form question no saved answer covers); the posting keeps its status and is not counted in `max_per_day`. Ends with `apply test-report`: this test's result, steps, minutes, tokens and ~$, and a table of every test with the change against the previous and the first. | `data/apply/test.json`, `data/apply/tests/` |
| `/track` **entry point** | 5 | Looks in Gmail for mail about applications (through `mail-reader`): marks a posting applied from a LinkedIn "application sent" or a confirmation, stores replies and updates the status; reports a status table of every application and, last, how many postings were updated and which moved from which status to which. Needs the claude.ai Gmail connector. Asks nothing, never sends or changes mail; no setup half. | `data/jobs/jobs.json` (`tracking`, `status`), `data/track/batches/`, `data/track/results/` |
| `/show-list [stage ... \| all \| N]` **entry point** | any | Counts of postings per stage (from unchecked and blocked to applied and replied), then a table of the postings in progress (or the stages given): stage, score, title, company, applied on, reply, link. Asks nothing, changes nothing. | nothing |
| `/create-profile [cv path]` | 1a | Extracts the profile from the CV and the optional documents in `data/profile/sources/` (LinkedIn data export, certificates, project write-ups; through the `profile-extractor` agent), confirms uncertain fields and disagreements between the documents, asks for missing ones, renders `profile.md`. Run again to update answers (A) or to add what new documents say (C: each addition and conflict is asked). | `data/profile/profile.json`, `profile.md` |
| `/setup-sources` | 1b | Apify only (the free sources FREEHIRE, GREENHOUSE, LEVER and ASHBY need no token and have no test run). Checks `APIFY_TOKEN` and the monthly credit, runs a 5-posting LinkedIn test built from the profile; shows only postings that pass the safety check. | `data/fetch/raw/APIFY_LINKEDIN_SCRAPER/` |
| `/setup-job-search` | 1 | Search settings: date filter, Apify actors, then calls `/setup-searches`. Does not search. | through the tools and commands it calls |
| `/setup-searches` | 1 | Only the queries: drafts up to 8 (role × location), the candidate picks which to keep and how many postings each returns (25 by default). | `data/searches/searches.json` |
| `/setup-job-scoring` | 1 | The scoring model (Sonnet, Opus or Haiku), the shortlist threshold and size, and postings per run. Does not score. | `data/score/settings.json` |
| `/setup-cv` | 1 | The model `cv-writer` runs on (Sonnet or Opus) and whether `cv-reviewer` revises each CV and letter (on by default). Writes no documents. | `data/cv/settings.json` |
| `/execute-job-search` | 2a, 2b, 2c | Runs the search with the saved settings, adds new postings and, as the last step, runs the safety check; asks nothing. | `data/jobs/jobs.json`, `data/fetch/raw/`, `data/fetch/runs/` |
| `/execute-job-scoring` | 2d | Rule filter (code), extraction (`job-extractor`, Haiku), scoring (`job-matcher`, Sonnet or Opus), then the shortlist with each posting's safety warnings; asks nothing. | `data/jobs/jobs.json` (`filter`, `extracted`, `match`), `data/score/batches/`, `data/score/results/` |
| `/approve-jobs [job id ... \| all \| N]` | 3a | Only the choice: shows the scored postings without a CV and asks for how many, best first (all, first 10, first 20, custom); marks them `approved`. Rejects nothing. | `data/jobs/jobs.json` (`status`) |
| `/execute-cv [job id]` | 3b | For every approved posting (or the one given, also when already `cv_ready`) the `cv-writer` agent tailors the CV and writes the cover letter; code checks them against the profile and renders both as PDF (Jake's Resume layout, fit to the page, ATS text check); with review on, `cv-reviewer` revises each saved posting and code checks and renders the revision again (the first version stays when it fails); asks nothing and asks the candidate for no review. | `data/cv/<DD-MM-YYYY>/<company>-<source_id>/`, `data/jobs/jobs.json` (`documents`, `status: cv_ready`) |
| `/execute-job-apply` | 4, by hand | A table of every posting whose documents are ready and that the candidate has not applied to yet (`cv_ready`), best score first: title, company, location, documents folder and a clickable apply link, with the safety warnings under it. Asks nothing and opens no link. | nothing |
| `/mark-applied [id]` | 4, by hand | Records an application the candidate sent through a link in the `/execute-job-apply` table: asks the day (today, yesterday, a date) and moves the posting from `cv_ready` to `applied`. | `data/jobs/jobs.json` (`status`, `application`) |
| `/close-stale [days]` | 5 | Lists the applications still `applied` with no mail for 60 days or more (or the days given), asks whether to close all, some or none, and marks the chosen ones `no_response`; `/track` stops looking for them. Reads no mail. | `data/jobs/jobs.json` (`status`, `application.closed_at`) |
| `/skill-gaps [--days N] [--top N]` | 2d | Lists the skills the scored postings of the last 90 days ask for that the profile does not have, weighted by score (code only, no agent), with example postings, and points to `/add-skills` for the ones the candidate has. Asks nothing and changes nothing. | nothing |
| `/add-skills [--days N]` | 2d, 1a | Shows the top 16 skills of `/skill-gaps`, asks which ones the candidate really has (multi-select) and at which level, and adds them to the profile by their key (`profile add-skill`), then renders `profile.md`. Adds nothing the candidate did not pick. | `data/profile/profile.json` (`skills.technical`), `profile.md` |
| `/cost-report [job\|agent\|command\|day]` **entry point** | any | Shows the tokens and estimated dollars of the LLM steps recorded by the cost hook: per posting (CV writing and application), agent, command or day. Asks nothing and changes nothing. | nothing (the hook writes `data/cost/usage.json`) |

Arguments passed by an orchestrating command (`$ARGUMENTS`, `key=value`):

- `/setup-searches data_sources=all|NAME,NAME`: used only for the cost estimate.

### Agents

| Agent | Tools | Used by | Does |
|-------|-------|---------|------|
| `profile-extractor` | Read, Glob | `/create-profile` | Reads the CV and the documents in `data/profile/sources/` and returns a draft profile as JSON, with uncertain fields and disagreements listed as pending confirmations; or, for an existing profile, the additions and conflicts the documents give. Cannot write. |
| `job-extractor` (Haiku) | Read | `/execute-job-scoring` | Reads one batch of postings and returns the facts each states (industry, seniority, years, required and preferred skills, required languages, salary, visa sponsorship) as JSON. Cannot write. |
| `job-matcher` (Sonnet; Opus or Haiku from the settings) | Read | `/execute-job-scoring` | Reads one batch of postings, with `profile.md` in it, and returns a 0-100 score from a fixed rubric, a summary, strengths and gaps as JSON. Cannot write. |
| `cv-reviewer` (Sonnet) | Read | `/execute-cv`, when review is on | Reads one posting folder (the writer's input and saved answer) and returns the whole answer revised as a hiring manager would want it: strongest true matches first, wording that stretches an achievement brought back to it, true skills the posting asks for named. Same shape and the same checks as `cv-writer`'s; cannot write. |
| `cv-writer` (Sonnet; Opus from the settings) | Read | `/execute-cv` | Reads one posting and the profile without contact details and returns, as JSON, a CV tailored to it (summary, skill groups, each chosen achievement rewritten in the posting's terms, by reference, and the projects that fit) and the cover letter's paragraphs. Cannot write. |
| `mail-reader` (Haiku) | Read; Gmail `search_threads`, `get_thread`, `get_message` | `/track` | Reads one batch of applications, runs its Gmail search and returns each matching reply (message id, day, kind, a short note) as JSON. Cannot write files or send, change or delete mail. |
| `job-sender` (Sonnet; `sender_model` in `data/apply/settings.json`) | Read (its input and documents only); the Playwright tools but JavaScript (`browser_evaluate`, `browser_run_code_unsafe`), `browser_drop` and the network and console readers; the `apply-safety` skill preloaded | `/apply` | Opens one posting in the Playwright browser, fills in the application from its input and stops with JSON when a required field needs the candidate, before the submit button, and on anything wrong (sign-in, CAPTCHA, sensitive fields, another domain, prompt injection); submits once when the command says the candidate approved. A hook checks each of its calls. Cannot run commands, write files or send email. |

## `jobfinder` CLI

Run as `uv run jobfinder <group> <command>`. Every command prints JSON to stdout and exits with 1 on failure (`"ok": false` with an `error`, or `"valid": false` with `errors`). Before any command, files from the old flat `data/` layout are moved into the per-stage folders (reported on stderr).

### `profile` (stage 1a)

| Command | Does | Output |
|---------|------|--------|
| `profile validate` | Checks `profile.json` against the schema. | `valid`, `errors` |
| `profile missing` | Lists fields still to ask (by `x-level`) and pending confirmations. | `pending_confirmations`, `missing`, `complete` |
| `profile set <pointer> <json>` | Sets a value at a JSON Pointer; `/-` appends to a list. The value is JSON (strings in double quotes, `null` to skip). | `ok`, `errors_at_pointer` |
| `profile render` | Renders `profile.md` from a valid `profile.json`. | `ok`, `path` |
| `profile add-skill <key> <expert\|advanced\|intermediate> [--days N]` | Appends a skill the postings ask for and the profile lacks to `skills.technical`, found by its `key` in `score gaps` (same `--days`), so code writes the name. Fails when no missing skill has that key. | `ok`, `added` (`name`, `level`), `errors_at_pointer` |

### `source` (stage 1b)

| Command | Does | Output |
|---------|------|--------|
| `source check` | Verifies `APIFY_TOKEN` and reads this month's usage. | `username`, `plan`, `monthly_used_usd`, `monthly_limit_usd`, `monthly_remaining_usd`, `cycle_ends` |
| `source test --keywords K --location L [--limit 5] [--date-posted pastWeek] [--data-source APIFY_LINKEDIN_SCRAPER]` | Runs one actor once (LinkedIn by default), keeps the raw output and runs the safety check on the postings; lists only those that are not blocked. | `count`, `cost_usd`, `raw_path`, `blocked`, `jobs` (`title`, `company_name`, `location`, `posted_at`, `url`, `safety`) |
| `source actors` | Lists `config/apify_actors.json`. | `actors` |

### `search` (stage 2a)

| Command | Does | Output |
|---------|------|--------|
| `search validate [--data-source NAME ...]` | Checks `searches.json` and gives the per-run totals for the chosen actors. | `valid`, `errors`, `searches`, `data_sources`, `max_postings_per_run`, `max_cost_per_run_usd` |

### `fetch` (stage 2a)

| Command | Does | Output |
|---------|------|--------|
| `fetch configure [--date-posted F] [--data-source NAME ...]` | Saves the actors (`all` or names) and the date filter in `settings.json`. | `ok`, `path`, the saved settings |
| `fetch settings` | Shows `settings.json` with defaults filled in. | `ok`, `path`, `exists`, `data_sources`, `date_posted` |
| `fetch run [--date-posted F] [--data-source NAME ...]` | Runs every enabled search once per chosen actor, adds new postings to `jobs.json` (skipping known ids and reposts) and, as its last step after every query, runs the safety check. Refuses to start if the worst case is more than the Apify credit left this month. | the run summary (see [data.md](data.md#datafetchrunsstampjson)) plus `run_path` |

### `jobs` (stages 2b and 2c)

| Command | Does | Output |
|---------|------|--------|
| `jobs import [RAW_FILE ...]` | Maps raw actor output into `jobs.json` with each actor's `output` mapping, skipping known ids and reposts. With no files, every file under `data/fetch/raw/`, oldest first. `fetch run` does this itself; run it after changing a mapping. | `ok`, `path`, `files` (counts per file), `total_count`, `total_new`, `total_already_saved`, `total_reposts`, `total_invalid` |
| `jobs check [--all]` | Safety check (stage 2c) of the postings in `jobs.json` that have no `safety` yet (or were checked before the model was downloaded), with the rules in `config/safety.json` and the local classifier; `--all` checks every posting again (after changing the rules). `fetch run` does this itself. | `ok`, `path`, `checked`, `pass`, `warn`, `block`, `reasons` (count per reason code), `model` (whether the classifier was used) |
| `jobs approve ID [ID ...]` | Marks postings `approved` for stage 3b (the choice of step 3a): only postings with verdict `pass` or `warn` and status `new` or `shortlist`. | `ok`, `path`, `approved` (ids), `skipped` (`id`, `reason`: `unknown`, `not_checked`, `blocked`, `status:<status>`) |
| `jobs list [--stage S ...] [--top N]` | Every posting with the stage it has reached (`unchecked`, `blocked`, `filtered_out`, `unscored`, `new` = scored, or its status `shortlist`, `approved`, `cv_ready`, `applied`, `response`, `rejected`), furthest stage first, then best score; `--stage` keeps only those stages. The table of `/show-list`. A `blocked` posting is its id and stage only. | `ok`, `path`, `total`, `counts` (per stage, over every posting), `count`, `jobs` (`id`, `title`, `company_name`, `location`, `url`, `apply_url`, `stage`, `status`, `score`, `warnings`, `applied_at`, `outcome`, `also_in`: the locations of its copies set aside as `same_posting`, `people_search`: from scoring on, LinkedIn people-search links `recruiter` and `team`, or null) |
| `jobs dedupe [--dry-run]` | Removes reposts already in `jobs.json` (same company, title and city as an earlier entry), only entries still `new`. New reposts are already skipped by `fetch run`. | `ok`, `path`, `dry_run`, `before`, `removed`, `removed_ids`, `after` |

### `safety` (stage 2c)

| Command | Does | Output |
|---------|------|--------|
| `safety model` | Downloads the prompt injection classifier once (about 81 MB, pinned revision) into `data/safety/model/` and checks its SHA-256; if it is already there, only checks it. Then scores a built-in ordinary posting and a built-in injection; `ok` is false unless the first is below the threshold and the second at or above it. Uses `HF_TOKEN` from `.env` if Hugging Face asks for a login. | `ok`, `downloaded`, `repo`, `revision`, `threshold`, `path`, `size_mb`, `self_test` (`benign`, `injection` scores) |

### `score` (stage 2d)

| Command | Does | Output |
|---------|------|--------|
| `score configure [--matcher-model sonnet\|opus\|haiku] [--min-score N] [--shortlist-size N] [--max-per-run N]` | Saves the scoring settings; a value left out keeps its saved value. | `ok`, `path`, the saved settings |
| `score settings` | Shows `data/score/settings.json` with defaults filled in. | `ok`, `path`, `exists`, `matcher_model`, `min_score`, `shortlist_size`, `max_per_run` |
| `score filter [--all]` | Rule filter, from the preferences in `profile.json` and the exclude words of `data/fetch/settings.json`, of the postings with verdict `pass` or `warn` and status `new` that have no `filter` yet; `--all` filters them all again (after changing the preferences). | `ok`, `path`, `checked`, `passed`, `rejected`, `reasons` (count per reason), `same_posting_changed` (filter entries the grouping of copies changed) |
| `score batch extract\|match [--size N]` | Writes the postings that still need the step (passed the filter, not extracted yet; or extracted, not scored yet), at most `max_per_run`, into batch files of 10 postings, after removing the step's old batches and results; a description goes in at most 6,000 characters long. A `match` batch also carries `profile.md` (`profile`), so it needs it. | `ok`, `step`, `count`, `remaining`, `batches` (`path`, `result_path`, `count`) |
| `score save BATCH RESULT` | Stores an agent's answer for one batch: only postings of that batch, each value checked against the job schema; after an extraction the rule filter runs again. | `ok`, `path`, `step`, `saved`, `invalid` (and `first_invalid`: an id and a field), `unknown`, `missing`; for `extract` also `rejected`, `reasons` |
| `score list [--min-score N] [--top N] [--all]` | The shortlist: scored postings that passed the filter and are not decided yet, at or above the threshold (default from the settings), best first; `--all` does not cut it to the shortlist size. | `ok`, `min_score`, `count`, `jobs` (`id`, `title`, `company_name`, `location`, `work_modes`, `posted_at`, `url`, `apply_url`, `score`, `summary`, `strengths`, `gaps`, `warnings`: the safety `warn` reasons, `also_in`: the locations of its copies, `people_search`: LinkedIn people-search links `recruiter` and `team`) |
| `score gaps [--days N] [--top N]` | The skill gap report, code only: the `required_skills` and `preferred_skills` of postings with verdict `pass` or `warn` that passed the rule filter, have `extracted` and were saved in the last N days (default 90), any status, keeping the skills the profile lacks as `cv save` decides it (aliases and generic skills of `config/cv_check.json`, the technologies of every job and project). One name per skill; a posting counts once per skill, 2 when required and 1 when preferred, times its match score / 100 (0.5 unscored). Heaviest first, the top N (default 15). | `ok`, `days`, `postings`, `count`, `skills` (`skill`, `key`: lower-case letters, digits and dashes, `weight`, `postings`, `required`, `preferred`, `avg_score`, `examples`: up to 3 `id`, `title`, `company_name`) |

### `cv` (stage 3b)

| Command | Does | Output |
|---------|------|--------|
| `cv configure [--writer-model sonnet\|opus] [--review true\|false]` | Saves the CV settings. | `ok`, `path`, `writer_model`, `review` |
| `cv settings` | Shows `data/cv/settings.json` with the default filled in. | `ok`, `path`, `exists`, `writer_model` |
| `cv batch [--id ID]` | Writes `input.json` for every approved posting (or only `ID`, which may also be `cv_ready`) into `data/cv/<DD-MM-YYYY>/<company>-<source_id>/` and removes an old answer there. Needs a valid profile. | `ok`, `count`, `jobs` (`id`, `title`, `company_name`, `input_path`, `answer_path`) |
| `cv save INPUT ANSWER [--review]` | `--review`: the answer is `cv-reviewer`'s revision (`review.json`) of a `cv_ready` posting; it is checked and rendered the same way and sets `documents.reviewed`, and when it fails no `rejection.json` is written and the documents saved before stay (`kept`). Otherwise: checks the agent's answer against the profile (see [data.md](data.md#datacvdd-mm-yyyycompany-source_id)); if it holds, renders the CV and cover letter as PDF (Typst), fits the CV to at most two pages, checks the text read back from both, writes the letter as text and the posting as `Job-Description.pdf`, sets `documents` and moves the posting to `cv_ready`. Exit code 1 when the answer or the documents are rejected. | `ok`, `id`, `documents`, `pages` (of the CV), `trimmed` (bullets or projects left out to fit), `dropped` (`skills`, `achievements`, `rewrites`, `projects`: counts); or `errors` (`<field>: <code>`) and `rejection_path` (`rejection.json` next to the input, with the values, for the agent) |

### `apply` (stage 4)

| Command | Does | Output |
|---------|------|--------|
| `apply configure [--confirm-before-submit true\|false] [--max-per-day N] [--sender-model sonnet\|opus]` | Saves the `job-sender` settings in `data/apply/settings.json` (written by `/setup`): `false` lets the up-front confirmation of `/apply` be the approval, `true` waits for "Gönder" on each application; `--sender-model` is the model the agent runs on. | `ok`, `path`, `confirm_before_submit`, `max_per_day`, `sender_model` |
| `apply settings` | Shows them, with defaults filled in (`true`, 10, `sonnet`). | `ok`, `path`, `exists`, `confirm_before_submit`, `max_per_day`, `sender_model` |
| `apply list` | Postings with status `cv_ready` (documents ready, not applied to yet) that passed the safety check, best score first; the table of `/execute-job-apply` and `/apply`. Short fields only, never the description. | `ok`, `count`, `agent` (`sent_today`, `max_per_day`), `jobs` (`agent_blockers`: why the agent may not send it, empty when it may: `status:<status>`, `safety:<verdict>`, `missing:cv`, `missing:cover_letter`, `no_https_link`; `id`, `title`, `company_name`, `location`, `work_modes`, `url`, `apply_url`, `status`, `link`: `apply_url`, else `url`; `score`, `warnings`: the safety `warn` reasons; `documents`: `cv`, `cover_letter`, `cover_letter_text` and `job_description`, those the posting has, with `path` and `exists`) |
| `apply mark ID [--date today\|yesterday\|YYYY-MM-DD]` | Records that the candidate sent the application: `cv_ready` → `applied` and `application.applied_at` (now, or 00:00 UTC of the given day). Refuses another status, a day in the future or before the posting was saved. | `ok`, `path`, `id`, `title`, `company_name`, `status`, `applied_at` |
| `apply batch ID` | Starts the `job-sender` agent's application for one posting: writes `input.json` into `data/apply/<DD-MM-YYYY>/<company>-<source_id>/` and the run, `data/apply/current.json` (an earlier run is dropped). Refuses a posting with `agent_blockers` and the day's `max_per_day`. Needs a valid profile. | `ok`, `id`, `title`, `company_name`, `input_path`, `answer_path`, `user_answers_path`, `domains`, `sent_today`, `max_per_day` |
| `apply step` | Checks the agent's latest answer (`answer.json`, see [data.md](data.md#dataapplydd-mm-yyyycompany-source_id)) and says what comes next: `ask` (`questions`: `key`, `label`, `type`, `options`, `required`, `saved`), `confirm` or `submit` (`page`: host and path of the form; `summary`: `label`, `value`, `source`; `uploaded`), `continue` (`trusted`: the domain code trusted after `new_domain`), `stop` (`reason`), `done` (`result`: `submitted`, with `applied_at` and `warnings`; or `stopped`, with `reason` and `note`). On `submitted`: `cv_ready` → `applied`, `application.via` = `job-sender`. | `ok`, `id`, `title`, `company_name`, `status`, `domain`, `next`, and the fields above |
| `apply answer` | Stores the candidate's answers from `user-answers.json` in `data/apply/answers.json` (but those in `once`) and removes the file. | `ok`, `saved`, `this_application_only`, `unknown`, `answers` (`key`, `question`, `answer`) |
| `apply confirm` | Records that the candidate approved the latest summary; refused before a summary or after the run stopped. | `ok`, `id`, `confirmed` |
| `apply batch ID --test` | The run of `/test-apply`: the same, in `data/apply/tests/<YYYY-MM-DD-HHMMSS>-<company>-<source_id>/` with `test.json` (the commit, whether files were changed, `sender_model`; completed with the result, steps and end when the run ends), the posting remembered in `data/apply/test.json`. Its status does not block it, `max_per_day` does not count, `step` answers `submit` instead of `confirm`, and a submitted test leaves the posting as it is. | the `apply batch` fields and `test` (`commit`, `changed`, `sender_model`) |
| `apply test-choice` | The posting `/test-apply` sends. | `ok`, `job_id` (null before the first test), `title`, `company_name`, `status`, `blockers` (as `agent_blockers`, never the status), `picked_at` |
| `apply test-report` | Every test run, oldest first, with the cost records read again: result, steps, minutes, the agent's runs on that folder's input and the `/test-apply` command's responses during the run (counted when its turn ends, so the current test has none yet). Changes compare the agent's figures. | `ok`, `count`, `tests` (`id`, `title`, `company_name`, `started_at`, `ended_at`, `commit`, `changed`, `sender_model`, `result`, `reason`, `steps`, `domain`, `minutes`, `agent` and `command`: `runs`, `requests`, `tokens`, `total_tokens`, `cost_usd`, `models`; `vs_previous`, `vs_first`: `total_tokens`, `total_tokens_percent`, `cost_usd`, `cost_percent`, `requests`, `minutes`) |
| `apply stop` | Ends the run without sending (the agent cannot be reached); the posting stays `cv_ready`. | `ok`, `id`, `result: stopped`, `reason: abandoned` |
| `apply guard` | The hook on the agent's Playwright calls and Reads: the hook input on stdin; exit 2 denies a call, JSON on stdout allows it or replaces its output. `.claude/settings.json` runs the same code as `python -m jobfinder.send`, which starts faster. | the hook's JSON, or nothing |

### `track` (stage 5)

| Command | Does | Output |
|---------|------|--------|
| `track batch` | Writes every posting to look up (status `shortlist`, `approved`, `cv_ready`, `applied` or `response`, or `new` with a score at or above `min_score`, these last ones only for 60 days after they were saved; not rejected, not closed, not `block`) into batch files of 15, each with its Gmail search query, after removing the old batches and results. | `ok`, `count`, `batches` (`path`, `result_path`, `count`) |
| `track save BATCH RESULT` | Stores the mail agent's answer for one batch: only replies to postings of that batch, a message once, each value checked against the job schema; marks a posting not applied yet as `applied` (with `application.applied_at`) when mail about its application is found; sets `tracking.outcome` and moves `applied` to `response` when a reply answers the application. | `ok`, `path`, `checked`, `saved`, `invalid` (and `first_invalid`: an id and a field), `unknown`, `duplicate`, `replies` (`id`, `title`, `company_name`, the reply, `status`, `outcome`), `changes` (`id`, `title`, `company_name`, `from_status`, `to_status`, `from_outcome`, `to_outcome`: each posting whose status or outcome changed) |
| `track list` | Every application (closed ones too), latest apply day first. | `ok`, `count`, `jobs` (`id`, `title`, `company_name`, `url`, `applied_at`, `status`, `outcome`, `checked_at`, `last_reply`) |
| `track stale [--days N]` | Applications still `applied` whose last mail (or apply day) is N days ago or more (60 by default), quietest first. | `ok`, `days`, `count`, `jobs` (`id`, `title`, `company_name`, `url`, `applied_at`, `last_activity`, `quiet_days`) |
| `track close ID ... \| --stale [--days N]` | Marks the given applications, or every stale one, `no_response` and writes `application.closed_at`; only `applied` ones. | `ok`, `path`, `closed`, `skipped` (`id`, `reason`: `unknown`, `status:<status>`) |
| `track interview ID ... [--back]` | Moves applications by hand to the next interview round: `applied` or `response` to status `interview` round 1, then round 2, 3 ... (up to 9); `--back` goes one round back, round 1 back to `applied`. Changes nothing else. | `ok`, `path`, `moved` (`id`, `status`, `round`), `skipped` (`id`, `reason`: `unknown`, `status:<status>`, `not_interview`, `last_round`) |

### `cost` (every stage)

| Command | Does | Output |
|---------|------|--------|
| `cost report [--by job\|agent\|command\|day] [--since YYYY-MM-DD] [--job ID ...]` | Reads every transcript in `data/cost/usage.json` that is still on disk again, then sums the runs (from the local day `--since` on, only the postings `--job` names). `job` (the default) keeps runs on one posting, with a part per stage (`cv`, `apply`); `command` only the main session. Dollars are an estimate at API prices. The hook that records runs is `python -m jobfinder.cost` (no CLI command). The `score save`, `cv save` and `track save` above also run inside the `python -m jobfinder.handoff` hook, which writes each agent's answer and saves it (see [architecture.md](architecture.md#agent-answers-the-handoff-hook)); all `jobfinder` tools print compact JSON. | `ok`, `by`, `since`, `count`, `rows` (`job`, `title`, `company_name`, `stages`, `total_tokens`, `cost_usd`; or the key, `runs`, `requests`, `tokens`, `total_tokens`, `cost_usd`), `total` (`runs`, `tokens`, `total_tokens`, `cost_usd`), `unknown_models`, `estimate`, `refreshed` |

`--date-posted` is one of `anyTime`, `past24Hours`, `pastWeek`, `pastMonth`. Which actors a command uses, first match wins: `--data-source`, then `JOBFINDER_DATA_SOURCES` (comma-separated), then `data_sources` in `settings.json`.

### `ui` (every stage)

| Command | Does | Output |
|---------|------|--------|
| `ui [--port N] [--no-open]` | Starts the local web UI and opens it in the browser: every posting in a table (tabs by stage, filters, sort, a side panel per posting), the settings of each stage in tabs, and a *Cost* page (tokens per day, breakdown and every run, from `cost report`'s data), and a terminal panel that runs the Claude Code commands. Listens on `127.0.0.1` only (port 8765, or the next free one, unless `--port` is given) until Ctrl+C. See [architecture.md](architecture.md#the-web-ui-jobfinder-ui). | the address, as text (not JSON) |

## Tests

```bash
uv run pytest
```

Tests never call Apify, Hugging Face, Gmail, a browser or a Claude agent and never load the downloaded model; to check the real model, run `uv run jobfinder safety model` (self-test).
