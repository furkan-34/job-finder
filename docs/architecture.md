# Architecture

How job-finder is built. Goals, rules and decisions are in [plan.md](plan.md), file formats in [data.md](data.md), every command and tool in [commands.md](commands.md).

## Overview

Claude Code is the runtime. There is no server and no web UI: the candidate opens `claude` in the repository and runs slash commands. Claude does the parts that need judgement (reading a CV, drafting queries, reading and scoring postings, tailoring a CV and writing a cover letter, talking to the candidate); small Python tools do everything deterministic and print JSON for Claude to read.

```
candidate ──► claude (main session)
                 │
                 ├─ slash command (.claude/commands/*.md)   asks questions, decides, reports
                 │     ├─ Skill tool ──► narrower slash command
                 │     ├─ Agent tool ──► subagent (.claude/agents/*.md)   isolated, few tools, no questions
                 │     └─ Bash ──► uv run jobfinder ...   deterministic, prints JSON
                 │                     └─ Apify API, local files under data/, local classifier model
                 │     (stage 5: the mail-reader agent alone reads Gmail through the claude.ai connector)
                 │     (stage 4: the job-sender agent alone works in a Playwright browser window through the
                 │      Playwright MCP tools; a hook, python -m jobfinder.send, checks each of its calls)
```

Everything runs from a Claude session, by hand; there is no scheduled run.

The candidate uses one command per stage, plus one to look:

```
/setup ─► /search ─► /write-cv ─► /apply ─► /track          /show-list at any time
  1         2           3           4         5
```

Each of them is an orchestrating command over the narrow ones of its steps:

| Stage | Command | Runs |
|-------|---------|------|
| 1 Setup | `/setup` | `/create-profile`, `/setup-sources`, `/setup-job-search` (→ `/setup-searches`), `/setup-job-scoring`, `/setup-cv`, `jobfinder apply configure`, tool checks |
| 2 Search | `/search` | `/execute-job-search` (2a fetch, 2b map + dedupe, 2c safety check), `/execute-job-scoring` (2d filter + score), then `/write-cv` (stage 3: it asks for how many postings, none stops) |
| 3 CV + cover letter | `/write-cv` | `/approve-jobs` (3a), `/execute-cv` (3b) |
| 4 Application | `/apply` | `job-sender` per posting; by hand: `/execute-job-apply`, `/mark-applied` |
| 5 Tracking | `/track` | `mail-reader` per batch |
| any | `/show-list` | `jobfinder jobs list` |

## Building blocks

| Block | Where | What it does |
|-------|-------|--------------|
| Narrow command | `.claude/commands/<name>.md` | One concrete job, e.g. `/setup-searches` only builds and saves the queries. |
| Orchestrating command | `.claude/commands/<name>.md` | Runs a multi-step workflow by calling narrow commands in order through the Skill tool, e.g. `/setup-job-search`. |
| Agent | `.claude/agents/<name>.md` | Isolated work that needs no questions, with the fewest tools possible, e.g. `profile-extractor` (Read and Glob only), `job-extractor`, `job-matcher`, `cv-writer` and `cv-reviewer` (Read only), `mail-reader` (Read and three read-only Gmail tools), `job-sender` (Read and the Playwright tools, no JavaScript). |
| Skill | `.claude/skills/<name>/SKILL.md` | Rules preloaded into an agent through its `skills:` field, e.g. `apply-safety` into `job-sender` (not run by hand); or a developer procedure Claude Code loads when the work matches it, e.g. `add-data-source` (also `/add-data-source`). |
| Hook | `hooks` in `.claude/settings.json` | Code around tool calls and stops: `python -m jobfinder.send` checks every Playwright call and every Read of the `job-sender` agent, before and after it runs; `python -m jobfinder.cost` records the tokens of every agent run and of the main session (`SubagentStop`, `Stop`). |
| Python tool | `src/jobfinder/`, run as `uv run jobfinder <group> <command>` | Validation, rendering, API calls, file writes. Prints JSON; exit code 1 on failure. |
| Schema | `schemas/*.schema.json` | JSON Schema (Draft 2020-12) of the profile and of a job posting. |
| Config | `config/apify_actors.json`, `config/safety.json`, `config/pricing.json`, `config/cv_check.json` | The Apify actors that can be used; the safety check rules; Claude API prices for the cost estimate; skill aliases and generic skills for the CV check. Checked in; hold no personal data. |
| Permissions | `.claude/settings.json` | Allow-lists the tools, asks before risky steps, denies reading secrets. |

### Commands and agents

- **Commands are narrow.** Each one does a single thing and says what it does not do. A workflow made of several steps is an orchestrating command that calls the narrow ones; the narrow ones stay usable on their own.
- **Commands call commands.** Slash commands are skills, so a command can run another with the Skill tool. The caller passes the values it already decided as `key=value` pairs in `args` (they arrive as `$ARGUMENTS`), so the called command does not ask again.
- **Orchestration is a command, not an agent.** Subagents cannot ask the candidate questions (no `AskUserQuestion`), so anything that asks runs in the main session.
- **Agents are for isolation.** An agent gets a fresh context and only the tools it needs. That makes it the place for work on untrusted input: the CV reader (`profile-extractor`) and the agents that read posting text: `job-extractor` and `job-matcher` in stage 2d, `cv-writer` and `cv-reviewer` in stage 3b; and `mail-reader`, which reads mail in stage 5 with Read and Gmail's search and read tools only. An agent that reads posting text has the Read tool only: no write, commands, email or apply. It returns JSON as text, and a Python tool checks it before anything is stored. The one exception is `job-sender` (stage 4), which applies in a Playwright browser window with Read and the Playwright tools, on the posting's own domains, after the candidate approves, with a hook checking each of its calls.
- **An agent that stops for the candidate.** `job-sender` returns JSON each time it needs something (answers, approval) and waits; the command asks the candidate and continues the same agent with SendMessage, so the browser tab and the form stay as they are.
- **Models.** An agent's model is set in its file (`model: haiku`); the orchestrating command can override it per call with the Agent tool's `model`, which is how `job-matcher` runs on the model chosen in the scoring settings and `cv-writer` on the one in the CV settings. Agents run on the candidate's Claude login, so there is no API key. Every agent sets `omitClaudeMd: true`: it starts without CLAUDE.md, since its file and input hold all it needs. Each also sets its `effort` (thinking is output, the most expensive tokens): `low` for the agents that fill a fixed shape (`job-extractor`, `job-matcher` with its rubric, `mail-reader`), `medium` for those that write or act (`cv-writer`, `cv-reviewer`, `job-sender`, `profile-extractor`); without it an agent takes the session's level. The entry-point commands `/search`, `/write-cv`, `/apply` and `/track` set `model: sonnet` in their frontmatter, so the orchestration of a run (and the narrow commands it calls with the Skill tool) uses Sonnet for that turn; the session's model comes back with the next message.
- **Questions** follow the rule in `CLAUDE.md`: every choice goes through `AskUserQuestion`; plain chat only for free-form answers.

### Setup and execute

Each stage is split where it can be into:

- **setup**: asks the candidate and saves the answers under `data/<stage>/` (e.g. `/setup-job-search`);
- **execute**: runs with the saved settings and asks nothing (e.g. `/execute-job-search`).

`/setup` runs every setup half in order; `/search` runs the execute halves of stage 2. Settings are read at every run.

### Python package

```
src/jobfinder/
  cli.py              argparse entry point (`jobfinder`); one function per command
  paths.py            every file location; legacy layout migration
  jobs.py             job store: load, validate against schemas/job.schema.json, fingerprint,
                      add (skip known ids and reposts), dedupe, save
  safety.py           stage 2c: pass / warn / block per posting from config/safety.json
  guard.py            stage 2c: local prompt injection classifier (Prompt Guard 2 22M, ONNX):
                      download + SHA-256 check, scoring in 512-token windows
  searches.py         searches.json: load, validate, cost summary
  apply.py            stage 4: postings ready to apply with their links, cv_ready -> applied
  profile/            stage 1a: schema validation, missing fields, profile.md rendering
  sources/
    apify.py          Apify client, token check, actor runs, raw output
    actors.py         config/apify_actors.json registry, checked on load
    freehire.py       freehire.me's free JSON API for entries of kind "freehire": no token, one
                      request per page of 25, country from the search's location
    ats.py            Greenhouse, Lever and Ashby company job boards (kinds "greenhouse", "lever", "ashby"):
                      free, no token; reads the entry's `boards`, filters by title, country and date in code
    errors.py         SourceError, the one error every source raises
    mapping.py        one mapper for every actor (Apify, freehire or ATS board): build_input from the actor's "input"
                      template (with the optional drop_words, date_posted_values and location_countries),
                      to_job from its "output" mapping (stage 2b)
  fetch/
    settings.py       data/fetch/settings.json
    runner.py         one fetch run: budget check, actor × search, map, store, safety check (last), run summary
  score/
    rules.py          stage 2d rule filter from the profile's preferences, again after the extraction;
                      keeps one posting of each group of copies in several cities
    batches.py        batch files for the agents that read posting text; checks and stores their answers
    settings.py       data/score/settings.json
    shortlist.py      scored postings, best first, with the safety warnings
  cv/
    writer.py         stage 3b: the cv-writer agent's input (profile without contact details, items
                      numbered); checks its answer against the profile; stores documents, cv_ready
    render.py         the CV and cover letter as PDF (Typst): data from the profile and the checked
                      answer, fit to the page, the ATS check (text read back with pypdf)
    template.typ      the Typst layout of both, after Jake's Resume, and of the posting PDF
    settings.py       data/cv/settings.json
  apply.py            stage 4: postings ready to apply to, what a form needs, applied + applied_at
  send.py             stage 4 by the agent: the job-sender agent's input and run, checks of each of its
                      answers, saved form answers, and the hook on its browser calls (`python -m
                      jobfinder.send`, faster to start than the CLI)
  track.py            stage 5: batch files and Gmail query for the mail agent; checks and stores its
                      answer as `tracking`, sets the outcome and the status
  cost.py             every stage: tokens and estimated cost of each agent run and command, read from
                      Claude Code's transcripts by the hook (`python -m jobfinder.cost`); the report
  ui/                 the local web UI (`jobfinder ui`): server.py (standard-library HTTP server and the
                      actions), terminal.py (one Claude Code session in a pseudo-terminal), static/ (the page
                      and the xterm.js files it uses)
```

Rules for the tools:

- Output is JSON on stdout (`"ok"` or `"valid"`, plus an `error` or `errors`). Messages for humans go to stderr.
- The job store is written atomically (temporary file + `os.replace`), so a crash never leaves half a `jobs.json`.
- All paths come from `paths.py`; nothing else hard-codes a location under `data/`.
- Only the Python tools read `.env` (through `python-dotenv`). Claude Code is denied read access to it.

## Stage flows

### Stage 1a: `/create-profile`

```
CV in data/profile/ (+ optional documents in data/profile/sources/: LinkedIn export CSVs, certificates, write-ups)
  ─► profile-extractor agent (Read, Glob) ─► draft JSON (disagreements between documents as pending confirmations)
   ─► command writes profile.json ─► confirm uncertain fields ─► ask missing fields
   (jobfinder profile missing / set) ─► jobfinder profile render ─► profile.md
```

`schemas/profile.schema.json` marks every field with `x-level` (required, recommended, optional); `jobfinder profile missing` uses it to list what still has to be asked. `profile.md` is rendered by code and has no contact details.

### Stage 1b: `/setup-sources`

`jobfinder source check` verifies `APIFY_TOKEN` and shows the monthly credit; `jobfinder source test` runs an actor (LinkedIn by default) for 5 postings, keeps the raw output, runs the stage 2c safety check on them and lists only the short fields of postings that are not blocked.

### Stages 2a, 2b and 2c: `/setup-job-search` and `/execute-job-search`

```
/setup-job-search
  profile ok? ─► token ok? ─► date filter ─► which actors?
     ─► jobfinder fetch configure          data/fetch/settings.json
     ─► /setup-searches                    data/searches/searches.json

/execute-job-search  (run by /search)
  jobfinder fetch run
     ─► budget check: sum(limits) × price per result ≤ credit left this month
     ─► for each actor × enabled search:
          mapping.build_input ─► Apify run ─► raw file in data/fetch/raw/<DATA_SOURCE>/     stage 2a
          jobs.import_raw: mapping.to_job (code, no LLM) ─► schema check
             ─► jobs.add (skip known ids and reposts) ─► data/jobs/jobs.json                 stage 2b
     ─► last step, after every query: safety.check_store                                  stage 2c
          every posting not checked yet gets pass / warn / block
          (regular expressions + guard model score, if data/safety/model/ is there)
     ─► run summary in data/fetch/runs/
```

### Stage 2d: `/setup-job-scoring` and `/execute-job-scoring`

```
/setup-job-scoring
  profile ok? ─► matcher model, shortlist threshold and size, postings per run
     ─► jobfinder score configure          data/score/settings.json

/execute-job-scoring  (asks nothing; reads only postings with verdict pass or warn, status new)
  jobfinder score filter                   rule filter (code) from profile.json ─► job.filter
  jobfinder score batch extract            data/score/batches/extract-<n>.json (10 postings each)
     ─► job-extractor agent (Haiku, Read only) per batch, up to 4 at once ─► JSON answer
     ─► command copies it to data/score/results/extract-<n>.json
     ─► jobfinder score save               schema check ─► job.extracted ─► rule filter again
  jobfinder score batch match              data/score/batches/match-<n>.json (10 postings each, and profile.md)
     ─► job-matcher agent (Sonnet, Opus or Haiku, Read only; reads the batch file only) ─► JSON answer
     ─► jobfinder score save               schema check ─► job.match (score, summary, strengths, gaps)
  jobfinder score list                     shortlist: score ≥ threshold, best first, with warn reasons
```

Each step only takes postings that still need it, at most `max_per_run` per run, so an interrupted run continues where it stopped. Status does not change. `/search` runs `/execute-job-search`, then this command.

### Skill gaps: `/skill-gaps`, `/add-skills`

```
/skill-gaps  (asks nothing, changes nothing, no agent)
  jobfinder score gaps [--days 90] [--top 15]
     postings: verdict pass or warn, filter passed, extracted, saved in the last N days (any status)
     ─► required_skills + preferred_skills the profile lacks (writer.missing_skills, as cv save checks)
     ─► one name per skill (alias groups of config/cv_check.json), each posting once per skill
     ─► weight: 2 required / 1 preferred × match score / 100 (0.5 unscored) ─► heaviest first
```

The names come from `job-extractor`'s answers, already capped by the job schema; the report only shows them and never changes the profile. Each skill has a `key` (lower-case letters, digits, dashes).

```
/add-skills  (asks; changes only the profile)
  jobfinder score gaps --top 16 ─► AskUserQuestion multiSelect: which do you have? ─► level of each
  jobfinder profile add-skill <key> <level>   code finds the skill by key in the same report and
                                              appends {name, level} to /skills/technical
  jobfinder profile render
```

The command passes only the key: the name is posting text, so code writes it and it never goes into a shell command. A skill typed by the candidate goes through `/create-profile`.

### Stage 3b: `/write-cv`, `/setup-cv`

```
/write-cv [job id ... | all | N]  (no review step; runs the two commands below with the Skill tool)
  /approve-jobs  (step 3a: the choice)
    jobfinder score list --all ─► how many: first 10 | all | first 20 | custom ─► jobfinder jobs approve <id> ...   new | shortlist ─► approved
  /execute-cv [job id]  (asks nothing; postings with status approved, never a block one)
    jobfinder cv batch                     data/cv/<DD-MM-YYYY>/<company>-<source_id>/input.json: the posting (with extracted
                                           and match) + the profile without contact details, items numbered
                                           (exp-1, exp-1-a2, proj-1)
       ─► cv-writer agent (Sonnet or Opus, Read only) per posting, up to 4 at once ─► JSON answer:
            summary, skill groups, per job bullets (achievement ref + rewrite), project refs,
            cover letter paragraphs
       ─► command copies it to answer.json next to the input
       ─► jobfinder cv save                checks against profile.json: a rewrite that adds a number or a
                                           skill falls back to the achievement; unknown refs and skills
                                           dropped; a summary or letter with a number or an asked-for skill
                                           not in the profile, a link or an email rejects
                                           (rejected once ─► the agent again with the error codes and
                                            rejection.json, which names the values)
            ─► render (Typst): <Name>-CV.pdf (one page, at most two), <Name>-Cover-Letter.pdf
               ─► ATS check: pypdf reads the text back; name, email, headings, every bullet and paragraph; no year next to a non-ASCII dash
               ─► <Name>-Cover-Letter.txt, Job-Description.pdf (the posting as stored, no LLM)
               ─► job.documents, status cv_ready
    review on (data/cv/settings.json)? for each saved posting:
       ─► cv-reviewer agent (Sonnet, Read only): input.json + answer.json, as a hiring manager
            (strongest match first, stretched wording brought back to the achievement, true keywords)
            ─► the whole revised answer, same shape ─► hook writes review.json
       ─► jobfinder cv save --review       the same checks and render; passes ─► documents replaced,
                                           documents.reviewed: true; fails ─► no second try, the
                                           first documents stay

/setup-cv
  profile ok? ─► writer model (Sonnet or Opus), review (off | on) ─► jobfinder cv configure      data/cv/settings.json
```

A failed posting stays `approved`, so the next run tries again. Stage 4 starts from `cv_ready` and reads the paths in `documents`.

### Stage 4: `/execute-job-apply` and `/mark-applied`

```
/execute-job-apply   (asks nothing; the candidate applies, nothing here does)
  jobfinder apply list                     postings with status cv_ready, best score first: a table with
                                           title, company, documents folder, apply link (apply_url, else the
                                           posting page) and the warn reasons ─► the candidate clicks and applies

/mark-applied [id]
  when? today / yesterday / a date ─► jobfinder apply mark <id> [--date ...]
     ─► status cv_ready → applied, application.applied_at          (read by stage 5)
```

### Stage 4 by the agent: `/apply`

```
/apply [job id ... | all | N]   (Playwright MCP of .mcp.json: visible window, own profile)
  Playwright tools loaded? (the command only checks; it never calls them)
  jobfinder apply list                     cv_ready postings; agent_blockers empty = the agent may send it
                                           (safety pass, documents on disk, https link); sent today / max_per_day
  how many? first 10 | first 5 | all | custom, best score first, at most what is left today
  confirm the list once, up front: Başla / İptal   (jobfinder apply settings: confirm_before_submit)
  for each, one at a time:
    jobfinder apply batch <id>             data/apply/<DD-MM-YYYY>/<company>-<source_id>/input.json: start link,
                                           allowed domains, documents, profile with contact details, saved
                                           answers; data/apply/current.json: the run the hook enforces
    ─► job-sender agent (sender_model, Sonnet by default; Read + Playwright tools, no JavaScript; rules in the apply-safety skill)
         opens the posting, Easy Apply or the company's apply page, fills in each step ─► stops with JSON
         every browser call and Read ─► hook: before (https, allowed domain, the two documents, its input;
                                       else denied), after (stage 2c patterns + classifier on the text;
                                       prompt injection replaces the text and stops the run)
    ─► command copies the JSON to answer.json ─► jobfinder apply step   schema, page domain, next step:
         ask      AskUserQuestion / plain text ─► user-answers.json ─► jobfinder apply answer
                  (data/apply/answers.json unless "this application only") ─► SendMessage the answers
         confirm  summary table ─► Gönder / Düzelt / İptal ─► jobfinder apply confirm ─► SendMessage "submit"
                  (confirm_before_submit true)
         submit   summary table ─► SendMessage "submit"   (confirm_before_submit false: the up-front
                  confirmation is the approval)
         continue the agent judged a page on a new domain to be the company's (new_domain) and code
                  trusted it (data/apply/trusted_domains.json) ─► SendMessage "go on"
         stop     SendMessage "stop" (the hook or a domain stopped the run)
         done     submitted: status cv_ready → applied, application.via = job-sender; or stopped
  report: sent or stopped (reason) per posting, warnings
```

A stopped posting stays `cv_ready`, so `/execute-job-apply` lists it to apply to by hand.

### Testing `/apply`: `/test-apply`

```
/test-apply
  jobfinder apply test-choice              the posting picked for tests (asked once, from apply list)
  jobfinder apply batch <id> --test        data/apply/tests/<time>-<posting>/ with test.json: commit, model
    ─► job-sender, the same hook and steps as /apply; ask only for a question no saved answer covers
         (saved without asking), submit without a summary: the candidate chose to send every test
         done     submitted: the posting keeps its status, not counted in max_per_day; test.json completed
  jobfinder apply test-report              every test: result, steps, minutes, tokens and ~$ of the agent
                                           (data/cost/usage.json) and of the command; vs previous, vs first
```

The form is really sent every time, on the same posting. A change to `job-sender.md`, the apply-safety skill, the hook or `sender_model` is measured by running `/test-apply` before and after it: each row names the commit (and "changed" when files were not committed).

### Stage 5: `/track`

```
/track  (asks nothing; no setup half)
  Gmail connector loaded? (the command only checks; it never calls Gmail)
  jobfinder track batch                    data/track/batches/track-<n>.json (10 postings each): shortlist,
                                           approved, cv_ready, applied, response, or new on the shortlist (the
                                           ones not applied to only for 60 days after they were saved); not
                                           rejected, not closed;
                                           id, company, title, apply day and domain + the Gmail query
     ─► mail-reader agent (Haiku; Read + Gmail search_threads, get_thread, get_message) ─► JSON answer
     ─► command copies it to data/track/results/track-<n>.json
     ─► jobfinder track save                only this batch, a message once, schema + search window check
                                           ─► job.tracking.replies, tracking.outcome; status → applied (mail about
                                              an application, application.applied_at) → response (an answer)
  report: totals; each posting that changed (from status → to status, outcome); a status table of
          every application (jobfinder track list); how many are stale (jobfinder track stale)

/close-stale (asks)
  jobfinder track stale                    applied, no mail for 60 days or more (--days N)
     ─► AskUserQuestion: all / some / none
     ─► jobfinder track close <id ...> | --stale   status → no_response, application.closed_at
```

The Gmail query is built by code: `after:<since> -from:me -in:chats {"<company>" ...}`, where `since` is two days before the last check (or the apply day) and the company names have no search operators or legal forms.

### Agent answers: the handoff hook

```
Agent call of job-extractor | job-matcher | cv-writer | cv-reviewer | mail-reader returns (PostToolUse on Agent)
  python -m jobfinder.handoff
    prompt `Batch file: <path>` / `Input file: <path>` ─► the answer's ```json block, unchanged
    ─► data/score/results/<name> | data/cv/.../answer.json (review.json) | data/track/results/<name>
    ─► score save | cv save | track save (jobs.json under a lock: four answers can land at once)
    ─► the Agent result the command sees = that save's JSON ("saved_by": "jobfinder.handoff")
job-sender stops (SubagentStop; its later stops come after SendMessage, not as an Agent result)
  python -m jobfinder.handoff ─► data/apply/<day>/<posting>/answer.json of the open run ─► the command runs apply step
```

- **Why.** Copying an answer through the command cost three model requests per answer and the whole answer typed again as output tokens of the main session's model, and it put text written from postings, mail or pages into the main session. Now code copies it, and the command sees counts, codes and paths only.
- **Fallback.** When the hook did not run, the Agent result is the agent's own text and the command writes and saves it as before; `apply step` says when `answer.json` is missing. A save that fails is reported in the replaced result as `"ok": false`, never raised; errors go to `data/cost/handoff-errors.log`.
- **Checked paths.** A batch path must be in its stage's `batches/` folder and a CV input must be an `input.json` under `data/cv/`, so a prompt cannot make the hook write elsewhere.

### Cost tracking: the hook and `/cost-report`

```
any agent of this repository stops (SubagentStop)       the main session stops (Stop)
  python -m jobfinder.cost                                python -m jobfinder.cost
    agent_transcript_path (…/<session>/subagents/          transcript_path (…/<session>.jsonl)
    agent-<id>.jsonl): every response once, by             cut where a slash command starts or the Skill
    message.id; its first prompt `Input file: <path>`      tool runs one: one entry per command
    → posting id (cv: posting.id, apply: id;
    a score or track batch: its job ids)
  ─► data/cost/usage.json: one entry per agent run (<session>/<agent id>) and per command
     (<session>/main/<n>): requests, tokens by kind, by model, estimated dollars (config/pricing.json)
/cost-report ─► jobfinder cost report [--by job|agent|command|day]: reads every transcript still on disk
                again first, then sums; /execute-cv (so /write-cv) and /apply add a token column per posting
```

- **No LLM, no extra tokens.** Claude Code already writes every response's `usage` (input, 5-minute and 1-hour cache writes, cache reads, output) to its transcripts; code only sums them. A response is split over one line per content block, each repeating the usage, so it is counted once by `message.id`.
- **Linked to the posting.** `cv-writer` and `job-sender` always start with `Input file: <path>`, so each CV and each application is counted for its posting, retries and SendMessage continuations included (the hook runs at every stop and sums the whole transcript again, so a continued agent stays one entry). The scoring and tracking agents read batches; they are kept per batch, not split. The main session (the commands themselves) is counted per command, not per posting: `/execute-cv` runs four writers at once.
- **Never in the way.** The hook always exits 0 and prints nothing (a `SubagentStop` hook that exits 2 keeps the agent running); errors go to `data/cost/errors.log`. Its matcher names this repository's six agents, so Claude Code's own internal agents (empty `agent_type`) are left out. A file lock keeps four writers stopping at once from losing an entry.
- **Late lines.** Claude Code may write a transcript's last message after `Stop` runs, so the report reads every transcript still on disk again; Claude Code removes old transcripts after a while (`cleanupPeriodDays`), and their last entry stays.
- **Dollars are an estimate.** On a Claude plan nothing is billed per token: `cost_usd` is the same tokens at Claude API prices (`config/pricing.json`, with Haiku 5.5's higher price above 100,000 prompt tokens). A model it does not list is counted but not priced (`unknown_models`). Small calls Claude Code does not write to a transcript are not counted, so `/cost` can differ a little.

## The web UI: `jobfinder ui`

A local page for what the commands do: a table of every posting (`jobfinder jobs list`), the settings of each stage in tabs, and a terminal panel. It adds no rules: every action calls the function the matching CLI command calls, so a setting is checked by the same code and a posting changes status the same way.

- **Server:** `ui/server.py`, Python's `http.server` on `127.0.0.1` (no new dependency). `GET /api/state` returns the postings (as `jobs list`, with source and dates), the settings, the searches, the profile status and the terminal status. `POST /api/approve` (`jobs approve`), `/api/mark-applied` (`apply mark`), `/api/settings/{fetch,score,cv,apply}` (the `configure` commands; the fetch settings also hold the exclude words, written only here), `/api/searches` (`data/searches/searches.json`, validated by `searches.validate`) and `/api/source-check` (`source check`), `POST /api/interview` (`track interview`: the interview rounds by hand, as buttons in the table, the side panel and the selection bar), `POST /api/delete` (`jobs.delete`: sets the status `deleted`, under the same lock the agents' saves use; the posting stays in `jobs.json` as a marker, so a later search that finds the same id, or the same company, title and city, does not add it again, while every list, the UI and every stage leave it out; the documents written for it stay on disk) and `GET /api/cost` (every run the cost hook recorded, read again from the transcripts like `cost report`, one row each with its day, command or agent, posting, models, tokens and estimated dollars: the *Cost* page draws tokens per day, a breakdown by agent, command, stage or posting, and the list of runs). A posting the safety check blocked leaves the server as an id and a stage, and only `http(s)` links of a posting are passed on.
- **Terminal panel:** `ui/terminal.py` starts `claude "/<command>"` in a pseudo-terminal (macOS and Linux) for one command of a fixed list: `setup`, `create-profile`, `search`, `write-cv`, `score` (`/execute-job-scoring`), `cv` (`/execute-cv`), `apply` and `track`. The output goes to the page as server-sent events and drawn by xterm.js (`static/vendor/`, MIT licensed, copied from npm: nothing is loaded from the internet), the keystrokes come back as `POST /api/term/input`. So the agent's questions, menus and confirmations are answered in the browser exactly as in a terminal, and the commands, agents and hooks are the ones `claude` runs anywhere else. Every button opens its own session as a tab (up to 6 running, 12 open); a tab's output (512 KB) is kept until the tab is closed, so a reloaded page shows it again. Sessions run side by side, so two that write postings at the same moment (for example scoring and CV writing) can overwrite each other's change to `data/jobs/jobs.json`: start those one after the other. The Jobs page has the batch buttons *Start scoring*, *Generate CVs* (the approved postings; with a selection, *Approve and generate CVs* approves it first) and *Agent apply* (`/apply` asks in its session for how many). *Open batch* and *Batch apply* ask for a data source, a part and a maximum per part (up to 100) and work on the postings of that source that are not applied yet (also the unscored ones that passed the filter), highest score first, unscored last (ties by id): part 2 with a maximum of 40 is the postings 41 to 80. *Open batch* opens them in new browser tabs, one each; *Batch apply* also marks them applied right away (`/api/mark-applied`, only the ones whose tab really opened), so its part 1 is always the next batch. The page opens the links itself (`window.open`), no tool opens or reads the pages. The Jobs table's first column is the position in the list as shown (not an id).
- **Starting from zero in the page:** the *Get started* tab of Setup lists the steps in order with their state: Claude Code found on the PATH, CV upload, profile, searches and Apify token, safety model, then the first search. `POST /api/cv-upload` takes a PDF as base64 (at most 8 MB, must start with `%PDF-`) and always saves it as `data/profile/cv.pdf`, never under the name the page sent. `POST /api/apify-token` writes `APIFY_TOKEN` into `.env` (mode 0600, other lines kept, a value of letters, digits, `-` and `_` only) and never returns it; `GET /api/state` only says whether it is set. `POST /api/safety-model` runs what `jobfinder safety model` runs. The README gives the few commands to copy into a terminal (install `uv`, `uv sync`, `uv run jobfinder ui`); there is no launcher script.
- **Who may call it:** a request needs a `Host` of `127.0.0.1` or `localhost` on the server's port (against DNS rebinding), a `POST` also an `Origin` of the page, and every `/api` call a random token made at start-up that only the served page carries. This matters most for the terminal, which starts Claude Code. The page also has a content security policy that allows no other origin.
- **What stays in Claude Code:** the UI cannot run the agents itself. Profile creation, search and scoring, CV writing, applying by the agent and `/track` need Claude, so their buttons open the terminal panel. Approving a posting for the CV stage, marking it applied (also one that has no CV yet, because the candidate may have applied by hand: `apply.mark_applied(early=True)`; the table's tick button does it) and changing settings are plain CLI functions and run without it. The side panel of a posting opens its documents (only files under `data/`, never for a blocked posting): *Open* asks the server, which runs on the candidate's own computer, to open the file in the system's app (`POST /api/open-doc`: `open` on macOS, `xdg-open` on Linux), *Browser* shows it in a tab (`GET /api/doc`).

## Security model

- **Untrusted text.** Posting text (titles, descriptions, links) can carry prompt injection. Until the safety check of stage 2c no LLM reads it: commands never open `data/jobs/jobs.json` or `data/fetch/raw/`, mapping is done by code, run summaries hold counts, costs and paths only, and `source test` lists only the short fields of postings that pass the check. After it, only read-only agents read posting text (the two of stage 2d and `cv-writer` in stage 3b), from files written by code, and never a `block` posting; the shortlist shows the short fields only.
- **Mail (stage 5).** Anyone can email the candidate, so mail is untrusted like posting text. Only `mail-reader` reads it, with Read and three read-only Gmail tools (search threads, get a thread, get a message); every Gmail tool that writes (send, reply, forward, draft, labels, filters, spam, trash) is denied in `.claude/settings.json`, so no session in the repository can call it, and the orchestrating command never calls Gmail. The answer is checked like the stage 2d answers (`jobfinder track save`: only that batch's postings, enums, lengths, the day inside the search window, a message once), and code writes the status. No mail text is stored: a message id, a day, a kind and a 160-character note.
- **Agent answers (stage 2d).** An answer is written from posting text, so it is untrusted too. The `jobfinder.handoff` hook writes it unchanged into a result file and runs the save, so the command sees only the save's counts and codes; `jobfinder score save` keeps only postings of that batch and checks every value against the job schema (enums, numbers, list and string lengths) before storing it. Errors name an id and a field, never a value. An injection that passed stage 2c can at most bend its own posting's score or summary, which the rubric tells the matcher to flag.
- **Agent answers (stage 3b).** `cv-writer` returns references to profile items with a rewrite of each achievement, skill names and two texts; `jobfinder cv save` replaces a rewrite that adds a number, a skill, a link or hidden characters with the achievement word for word, drops what the profile does not have, and rejects a summary or letter with a number or an asked-for skill that is not in the profile, a link, an email address or hidden characters. A rejection's values (the numbers or skill names) go only to `rejection.json`, which only the agent reads on its second try; the command sees codes. Names, contact details, titles, companies, dates and education are rendered by code from `profile.json`, and all text goes into the Typst template as data, never as markup. The contact details are not in the agent's input at all, so an injection cannot reach them. A claim made in other words cannot be checked by code; the agent's rules forbid it.
- **Safety check (stage 2c).** `safety.py` checks every text field of a posting in code, and `guard.py` scores it with a small local classifier (not an LLM, nothing leaves the machine); it stores `safety.verdict`: `block` for fee requests, prompt injection (a pattern match or a model score at or above the threshold) and hidden characters, `warn` for suspicious links, chat contact, text addressed to an AI and apply links to an unknown domain, otherwise `pass`. Reasons are codes (plus a domain), never posting text. Stage 2d never reads a `block` posting, and an agent that reads posting text has no permission to write files, send email or apply.
- **Applying by hand (stage 4).** `/execute-job-apply` opens no link, fills in no form and sends no email. The apply link is shown with its `warn` reasons, a `block` posting is never listed, and the command never reads posting text or the stage 3b files. The status moves to `applied` only after the candidate confirms.
- **Applying by the agent (stage 4).** Web pages are untrusted like postings, and `job-sender` acts in a Playwright browser window whose profile may be signed in to the candidate's accounts, so it is fenced in on every side:
  - *What it may apply to:* only a posting the candidate picked, `cv_ready`, with safety `pass` (a `warn` posting is applied to by hand), at most `max_per_day` a day, one at a time.
  - *Its tools:* Read and the Playwright tools; `browser_evaluate`, `browser_run_code_unsafe`, `browser_drop`, the network and console readers and a few more are not given. It cannot run commands, write files or send email.
  - *The hook* (`send.guard`, for `job-sender` only): before a call, only the Playwright tools the workflow uses are allowed; a URL, under any key but the text it types, must be https on the posting's domains (its site, apply link and company website) or a known ATS, and plain enough that the browser reads the same host as code (no backslash, user info, percent sign, port or non-ASCII character); a file must be one of the two documents, a Read its own input or a document; JavaScript is denied, and so is every call once a run stopped, apart from listing and closing tabs. While the page is outside the allowed domains, clicking and typing are denied too. A call that passes needs no permission prompt. After a call, the page address every Playwright answer names ("Page URL") outside the allowed domains (a redirect, an apply button to another site) replaces the answer with a notice that the agent must judge the page (`new_domain` or stop), and the text it returned is checked with the stage 2c patterns (and the page tools' text with the classifier); prompt injection, hidden tag characters or text addressed to an AI replaces the output with a notice and stops the run. Any error in the hook denies or stops, and a hook that cannot run or times out before a browser call blocks it (`onFailure: "block"`). A step the hook never saw stops the run (`guard_not_running`).
  - *Its answers:* checked by `jobfinder apply step` against a schema; the page's domain is checked again; labels and values shown to the candidate are cleaned (no links, hidden characters or line breaks); errors and reasons are codes. The command never acts on what an answer says.
  - *The candidate:* answers the required fields the profile does not, and approves the list of postings up front, and with `confirm_before_submit` also the whole summary of each before the agent clicks submit once. Sign-in, account creation, CAPTCHA and fields like an ID, passport, bank account or date of birth stop the run (`apply-safety` skill).
  - *Contact details:* `job-sender` is the one agent that sees them, to type them into the form on those domains.
  - *What code cannot see:* a click that loads another domain is caught by the agent's own check and by the page address at the next step, not before the page loads (`navigate` is checked before and after); screenshots are not checked by code; the submit click itself is the agent's (code records `not_confirmed` when it submitted without approval); a hook that fails or times out after a call lets that output through (before a call it blocks it: `onFailure: "block"`, Claude Code v2.1.295 or later).
- **Secrets.** `APIFY_TOKEN` (and `HF_TOKEN`, only if Hugging Face asks for a login when downloading the model) lives in `.env`, which is git-ignored and denied to Claude Code (`Read(.env)`, `Read(.env.*)` in `.claude/settings.json`).
- **Personal data.** Nothing under `data/` is committed; see [data.md](data.md#git-policy).
- **Permissions.** The `jobfinder` tools and `pytest` are allowed without a prompt, and so are writing the agents' answers into `data/score/results/`, `data/cv/*/*/answer.json`, `data/apply/*/*/answer.json` (with the candidate's answers in `user-answers.json` next to it) and `data/track/results/` and the three read-only Gmail tools; the `job-sender` agent's browser calls are allowed by the hook after its checks; the Gmail tools that write are denied. Removing old reposts (`jobs dedupe` without `--dry-run`), pushing to git and editing `.claude/` ask first. `~/.ssh` and `~/.aws` are denied. `tests/test_permissions.py` pins all of this: every agent's `tools:`, the denied Gmail tools and `.env`, the allowed writes, the browser and handoff hooks and the git-ignored `data/`; a change made on purpose updates that test in the same PR.
- **LinkedIn.** The actor is never given a personal LinkedIn cookie, and the app does not depend on one source.

## Extending

Adding, changing or removing a data source follows the `add-data-source` skill (`.claude/skills/add-data-source/SKILL.md`): the registry entry, the input from the saved searches, the raw path, the mapping to the job schema, setup and credentials, the shared pipeline, tests and docs, with a verification checklist for the PR. The two sections below are its short form.

### Add an Apify actor

1. Read the actor's input and output schema on its Apify page.
2. Add an entry to `config/apify_actors.json`: `name` (UPPER_SNAKE, becomes the postings' `data_source`), `label`, `actor_id`, `site` (prefix of the job id), `cost_per_1000_results_usd`, `notes`, the `input` template and the `output` mapping (format in [data.md](data.md#configapify_actorsjson)). No code is needed.
3. Only if a field needs a conversion the existing transforms cannot do, add one to `TRANSFORMS` in `sources/mapping.py`. If the actor wants other keywords, date values or a city instead of `City, Country`, use the optional `drop_words`, `date_posted_values` and `location_countries` keys (as APIFY_REED_SCRAPER does) or add another generic, config-driven option to `mapping.build_input`.
4. Add a test with a sample item; every mapped job must pass `jobs.validate`. `jobfinder jobs import` reports items that do not (`invalid`).

The setup lists the new actor automatically.

### Add a source that is not an Apify actor

freehire.me is one: its entry has `"kind": "freehire"` and no actor run. `fetch run` calls `sources/freehire.py` instead of Apify (so it needs no `APIFY_TOKEN`, and a run with only such entries does no budget check); the raw file, the mapping from `output`, the job schema, dedupe and the safety check are the same. The ATS company boards are the second: entries of kind `greenhouse`, `lever` and `ashby` go through `sources/ats.py` (a service without a search, so the module reads whole boards and filters in code; a board is read once per run). Another API of this kind needs its own module beside `freehire.py` and a branch in `fetch/runner.py::_run_one`.

### Change the scoring

- **Rule filter:** the mapping tables (source employment types and seniority levels to the profile's values) are at the top of `src/jobfinder/score/rules.py`; the rules themselves read the profile, so most changes are a profile change followed by `jobfinder score filter --all`.
- **Rubric:** the part weights and bands are in `.claude/agents/job-matcher.md`. A changed rubric applies to postings scored afterwards.
- **A new extracted field:** add it to `extracted` in `schemas/job.schema.json` (with a length cap), to the table in `.claude/agents/job-extractor.md` and to `EXTRACT_FIELDS` in `src/jobfinder/score/batches.py`, with a test in `tests/test_score.py`.

### Change the CV

- **Layout and headings:** `src/jobfinder/cv/template.typ` (Typst, after Jake's Resume) lays out the CV, the cover letter and the posting PDF from the JSON that `src/jobfinder/cv/render.py` builds (`cv_data`, `letter_data`, `job_data`); the plain-text letter is `letter_text`. Keep it ATS-friendly: one column, no tables, columns, images or icons, the contact details in the body, standard headings; a new heading goes into `_expected_cv` too, so the ATS check looks for it. Place strings as text, never through `eval`. The trimming order is `_trim`.
- **What the agent stresses and how it writes:** `.claude/agents/cv-writer.md`.
- **The checks:** `ANSWER_SCHEMA` (lengths), `check` and `_rewrite_ok` in `src/jobfinder/cv/writer.py`, with a test in `tests/test_cv.py`. A new choice for the agent refers to a profile item; free text about a fact needs a check in code that falls back to the profile's own words.

### Change how the agent applies

- **Settings:** `data/apply/settings.json`, saved by `/setup` (`jobfinder apply configure`): `confirm_before_submit` (`false` lets the agent submit after the summary without asking again; the up-front confirmation is the approval), `max_per_day` and `sender_model` (the model `/apply` starts the agent on; Sonnet by default).
- **Allowed domains:** the posting's own (`domains` in `src/jobfinder/send.py`), `ats_domains` of `config/safety.json` and the domains the agent trusted (`data/apply/trusted_domains.json`), subdomains included. A LinkedIn posting rarely has an apply link or company website, so the agent judges a new domain its Apply button leads to (rules in `.claude/agents/job-sender.md`) and returns `new_domain`; `trust_problem` in `send.py` is what code still refuses (not https or not plain, punycode, a shortener, more than `MAX_NEW_DOMAINS` a run). Delete a wrong entry from the file.
- **How it fills in a form:** `.claude/agents/job-sender.md`; what it must never do: `.claude/skills/apply-safety/SKILL.md`.
- **The checks:** `ANSWER_SCHEMA`, `step` and `guard` in `src/jobfinder/send.py`, with a test in `tests/test_send.py`. The hook allows only the Playwright tools in `TOOLS` (names of `@playwright/mcp`, pinned in `.mcp.json`); if a newer version renames one, the call is denied and the agent stops with `page_error` until the name is added there. `tests/test_permissions.py` pins the browser as a visible window (no `--headless`).

### Change the safety rules

Edit `config/safety.json`: `model.threshold` sets how sure the classifier must be to block (0 to 1); `block` and `warn` map a reason code to case-insensitive regular expressions (matched against every text field, zero-width characters removed); `ats_domains` and `shorteners` are domain lists (subdomains match too). A pattern that does not compile fails on load. Add a test in `tests/test_safety.py`, then run `jobfinder jobs check --all` to check the stored postings again.

### Add a stage

1. Give it a folder `data/<stage>/` (with a `.gitkeep`) and its paths in `paths.py`.
2. Put the deterministic work in a `jobfinder <group>` command that prints JSON, and allow it in `.claude/settings.json`.
3. Split it into a setup command and an execute command where it has both halves; keep each command narrow.
4. Document it here, in [data.md](data.md) and in [commands.md](commands.md), and update the stage table in [plan.md](plan.md).

## Testing

`uv run pytest`. Tests never call Apify, Hugging Face, Gmail, a browser or a Claude agent (the web UI's tests start the server on a free port and use a fake `claude` script for the terminal; the agents' answers, the hook's input and the transcripts the cost hook reads are written by hand in the tests), and never load the downloaded classifier: the Apify client and the classifier (ONNX session, tokenizer, scorer) are faked, and tests that write files use a temporary directory instead of `data/`. A schema change also updates `examples/profile.example.json` and the tests.
