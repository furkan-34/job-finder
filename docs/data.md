# Data

Where job-finder keeps its files and what is in them. The paths are defined in `src/jobfinder/paths.py`; nothing else hard-codes them.

## Git policy

**No user data is committed.** Everything under `data/` (CV, profile, searches, settings, postings, raw actor output, run summaries, logs) is git-ignored. Only the folder structure is in the repository, as empty folders holding a `.gitkeep`:

```gitignore
data/**
!data/**/
!data/**/.gitkeep
```

The files are created by the commands and tools on first use. Secrets are kept apart, in `.env` (also git-ignored, see `.env.example`).

What is checked in and holds no personal data: `schemas/`, `config/apify_actors.json`, `config/safety.json`, `config/cv_check.json` and `examples/profile.example.json` (a fictional person).

If a data file was committed before this rule, untrack it once and keep the local copy: `git rm -r --cached data && git add data`.

## Layout

One folder per stage. A new stage opens its own `data/<stage>/`.

```
data/
  profile/                    stage 1a
    <cv>.pdf | <cv>.docx        the candidate's CV
    sources/                    optional other documents /create-profile reads: a LinkedIn data export
                                (CSV files), certificates, project write-ups
    profile.json                the profile (schemas/profile.schema.json)
    profile.md                  rendered from profile.json, no contact details
  searches/                   stage 2a
    searches.json               search queries and postings per query
  fetch/                      stage 2a
    settings.json               actors, date filter
    raw/<DATA_SOURCE>/          full actor output, one file per query and run
    runs/                       one summary per run
  jobs/                       stage 2a on (dedupe: stage 2b, safety check: stage 2c, filter and score: stage 2d, tracking: stage 5)
    jobs.json                   every posting once, in the standard shape (schemas/job.schema.json)
  safety/                     stage 2c
    model/                      the prompt injection classifier (jobfinder safety model): model.quant.onnx,
                                tokenizer.json, config.json; not user data, but large and downloaded, so ignored too
  score/                      stage 2d
    settings.json               scoring model, shortlist threshold and size, postings per run
    batches/                    postings for the agents of the last run, <step>-<n>.json; untrusted
    results/                    the agents' answers for those batches, <step>-<n>.json; untrusted
  cv/                         stage 3b
    settings.json               the model cv-writer runs on, and whether cv-reviewer revises
    <DD-MM-YYYY>/               the day the documents were written, e.g. 24-10-2026/
      <company>-<source_id>/      one folder per approved posting, e.g. acme-software-gmbh-4012345678/
        input.json                  what cv-writer reads: the posting + the profile without contact details; untrusted
        answer.json                 cv-writer's answer; untrusted
        review.json                 only with review on: cv-reviewer's revised answer; untrusted
        rejection.json              only after a rejected answer: why, with the values, for cv-writer's second try; untrusted
        <Name>-CV.pdf               the CV to send (Jake's Resume layout, one or two pages)
        <Name>-Cover-Letter.pdf     the cover letter to send
        <Name>-Cover-Letter.txt     the same letter as plain text, to paste into a form
        Job-Description.pdf         the posting as stored (fields, safety warnings, description), to read
  apply/                      stage 4 by the agent (/apply)
    settings.json               confirm before submit, applications a day; by hand, optional
    answers.json                the candidate's answers to form questions, asked once
    trusted_domains.json        domains the agent judged to be a company's site (new_domain), allowed from then on
    current.json                the application in progress, which the hook enforces; removed when it ends
    test.json                   the posting /test-apply sends every time: {"job_id", "picked_at"}
    tests/                      one folder per /test-apply run, <YYYY-MM-DD-HHMMSS>-<company>-<source_id>/:
                                  the files of a day folder below, and test.json (commit, changed,
                                  sender_model, started_at; when it ends: ended_at, result, reason, steps)
    <DD-MM-YYYY>/               the day the application started
      <company>-<source_id>/      one folder per posting, named as in cv/
        input.json                  what job-sender reads: start link, allowed domains, documents, the profile
                                    with contact details, saved answers
        answer.json                 job-sender's latest answer; untrusted
        step-<n>.json               its earlier answers, kept once checked; untrusted
        user-answers.json           the candidate's answers, written by the command, removed once stored
        log.json                    one line per step: time, status, domain, reason
        guard-seen                  touched by the hook when it checks the agent's calls
  track/                      stage 5
    batches/                    applications to look up in Gmail, track-<n>.json, with the search query
    results/                    the mail agent's answers for those batches, track-<n>.json; untrusted
  cost/                       every stage, written by the cost hook
    usage.json                  tokens and estimated dollars per agent run and per command
    errors.log                  what the hook could not record
```

Applying by hand (stage 4) has no folder: it only adds `application` to a posting in `jobs.json`. Applying by the agent writes `data/apply/`.

Files from the older flat layout (`data/profile.json`, `data/profile.md`, `data/*.pdf`, `data/raw/`) are moved into these folders the next time any `jobfinder` tool runs. A file is never moved over an existing one.

## Files

### `data/profile/profile.json`

The candidate profile, validated against `schemas/profile.schema.json`. Values are stored in English. Each field has an `x-level` (`required`, `recommended`, `optional`) that decides whether `/create-profile` asks for it; the field decisions are in [plan.md](plan.md#profile-schema-decisions). `examples/profile.example.json` shows the full shape.

`profile.md` is rendered from it by `jobfinder profile render`, preferences first, and leaves out the contact details, so it can go into scoring prompts.

### `data/searches/searches.json`

Written by `/setup-searches`, validated by `jobfinder search validate`.

```json
{
  "updated_at": "2026-10-23",
  "searches": [
    {"id": "senior-backend-istanbul", "keywords": "Senior Backend Engineer hybrid",
     "location": "Istanbul, Turkey", "limit": 25, "enabled": true}
  ]
}
```

| Field | Rule |
|-------|------|
| `id` | lowercase letters, digits and `-`; unique |
| `keywords` | non-empty; seniority and work mode go into the text (LinkedIn search is AI based) |
| `location` | non-empty, in a form LinkedIn resolves, e.g. `Istanbul, Turkey`, `Germany` |
| `limit` | postings per run for this query, 1 to 1000; 25 by default |
| `enabled` | `false` pauses the query; at least one must be enabled |

Each enabled query runs once per chosen actor, so the worst-case cost of a run is the sum of the limits × the summed price per result of the actors.

### `data/fetch/settings.json`

Written by `jobfinder fetch configure`; read by every run. Missing keys fall back to the defaults shown.

```json
{
  "data_sources": ["all"],
  "date_posted": "anyTime",
  "exclude_words": ["unpaid", "commission only"],
  "updated_at": "2026-10-23T08:00:00+00:00"
}
```

`exclude_words` (set in the web UI under Setup, Searches; a list of at most 100 words or phrases of at most 60 characters, each once, case ignored): the rule filter of stage 2d rejects a posting with one of them as a whole word in its title, company or description (`excluded_word:<word>`), next to the profile's dealbreaker keywords. They apply to postings filtered after the change; `jobfinder score filter --all` checks the others again.

| Field | Values |
|-------|--------|
| `data_sources` | `["all"]` or actor names from `config/apify_actors.json` |
| `date_posted` | `anyTime` (default, all postings), `past24Hours`, `pastWeek`, `pastMonth` |

`all` means every entry of `config/apify_actors.json`, FREEHIRE, GREENHOUSE, LEVER and ASHBY included. The actors can be overridden for one run with `--data-source NAME` or `JOBFINDER_DATA_SOURCES=NAME,NAME`.

### `config/apify_actors.json`

Not user data, but it decides `data_source`. Each entry:

```json
{"name": "APIFY_LINKEDIN_SCRAPER", "label": "LinkedIn Jobs Scraper",
 "actor_id": "curious_coder/linkedin-jobs-scraper", "site": "linkedin",
 "cost_per_1000_results_usd": 1.0, "notes": "...",
 "input": {"keywords": "{keywords}", "location": "{location}", "datePosted": "{date_posted}",
           "limitPerSource": "{limit}", "scrapeCompany": false},
 "output": {"source_id": "id", "url": "link", "title": "title", "company_name": "companyName",
            "work_modes": {"field": "workplaceTypes", "transform": "work_modes"},
            "salary": {"field": "salaryInfo", "transform": "join"}, "...": "..."}}
```

`name` is UPPER_SNAKE and unique, `site` is the prefix of the job id.

- **`input`** is the actor input. A value that is exactly `"{keywords}"`, `"{location}"`, `"{limit}"` or `"{date_posted}"` is replaced by the search's value (keeping its type, so `"{limit}"` becomes the number); everything else is sent as written. `"{city}"` is available with `location_countries` (below).
- **`output`** maps job fields (`schemas/job.schema.json`) to the actor's output fields: `"fieldName"`, with dots for nested fields (`"company.name"`), a list of them (`["external_url", "job_url"]`: the first one that is not empty), or `{"field": "...", "transform": "..."}`. `source_id` and `title` are required; a job field left out is not set (`work_modes` then defaults to `[]`). Transforms: `text` (the default: strings and numbers as they are, empty as `null`), `join` (a list joined with ` - `), `work_modes` (`"Hybrid"` or `["Remote", "On-site"]` → `["hybrid"]`, `["onsite", "remote"]`).

**Optional keys for an Apify actor** that needs the search's values translated (all read by `mapping.build_input`; APIFY_REED_SCRAPER, APIFY_INDEED_UK_SCRAPER and APIFY_TOTALJOBS_SCRAPER use them):

- `drop_words`: words removed from the keywords, case ignored (work modes, which a keyword search would require in the posting). Keywords that become empty fail that search.
- `date_posted_values`: `date_posted` → the actor's own value, e.g. `{"pastWeek": "lastweek"}`. A value not listed (Reed has no `pastMonth`) fails that search with an error; it is never widened to all dates. A value of `null` means no date filter: the input key is left out (Indeed UK and Totaljobs for `anyTime`). In general, an `input` key whose value ends up `null` is not sent.
- `location_countries`: the countries the actor covers. The part of the location after the last comma must be one of them (case ignored), otherwise that search fails; `"{city}"` is then the part before the first comma (`"London, United Kingdom"` → `"London"`), or `""` when the location is only a country (the whole country).

**Optional key.** `"kind": "freehire"` marks freehire.me's free API (no Apify actor, no token, `cost_per_1000_results_usd` 0): `input` then holds the request's query parameters (`"{keywords}"`, `"{country}"`, `"{limit}"`, `"{days}"`; a parameter whose value is `null`, such as `{days}` for `anyTime`, is left out), `drop_words` are removed from the keywords (work modes, which a full-text search would require in the posting), and `countries` maps the part of the search's location after the last comma (`"Istanbul, Turkey"` → `"turkey"`) to the ISO code freehire filters by; a location whose country is not listed fails that search with an error naming the file. The city is not used, so a search for one city returns the whole country. `apply_url` is freehire's `url`, which can be a job board's redirect to the company's form.

**Optional key.** `"kind": "greenhouse"`, `"lever"` or `"ashby"` marks the free company job boards of that applicant tracking system (no Apify actor, no token, cost 0). The services have no search: an answer is every open posting of one company, so `boards` (board name as in the careers URL → company name, e.g. `{"gitlab": "GitLab"}`) lists the companies, and `fetch run` reads each board once per run (the rest of the run's searches reuse the answer) and filters in code (`sources/ats.py`): every keyword (minus `drop_words`) must be a word of the title; the part of the search's location after the last comma must be a key of `countries` (country → words such as city names that a posting's location text may use; a posting is kept when one of its locations contains one of those words or the search's city), or one of `any_location` (`"Worldwide"`) to keep every place; `date_posted` keeps postings published within 1, 7 or 30 days (a posting without a date is left out then); the newest `limit` are kept. `input` documents the parameters (`"{boards}"`, `"{keywords}"`, `"{location_words}"`, `"{days}"`, `"{limit}"`) and is saved in the raw file; a location whose country is not listed, or a board that does not exist (HTTP 404), fails that search with an error naming the file. Items are saved as returned, plus `_board` (`{"name", "company"}`) because Lever and Ashby postings do not name the company; `output` reads it as `_board.company`. Descriptions are mapped with the `html_text` transform (HTML, also HTML-escaped as Greenhouse sends it, to plain text). The apply link is the ATS's own page, so it passes the safety check's domain rule.

**Output options** (all sources): `"transform": "html_text"` (HTML or a list/object of it to plain text), `"epoch_ms"` (milliseconds since 1970 to ISO 8601), and `{"fields": [...], "transform": ...}` (every non-empty field, in order, joined as one list before the transform; Lever's description is `descriptionPlain`, `lists`, `additionalPlain`).

The entries are checked every time the file is loaded (`jobfinder source actors`).

### `config/safety.json`

The rules of the stage 2c safety check. Not user data.

```json
{"block": {"fee_request": ["\\bapplication\\s+fees?\\b", "..."], "prompt_injection": ["..."]},
 "warn": {"ai_addressed": ["..."], "chat_contact": ["..."]},
 "ats_domains": ["greenhouse.io", "lever.co", "..."],
 "shorteners": ["bit.ly", "tinyurl.com", "..."],
 "model": {"repo": "gravitee-io/Llama-Prompt-Guard-2-22M-onnx", "revision": "<commit>",
           "model_file": "model.quant.onnx", "sha256": "...", "malicious_label": 1, "threshold": 0.9}}
```

- **`block`, `warn`:** reason code → case-insensitive, multi-line regular expressions, matched against every text field of a posting joined together (lowercased, zero-width characters removed). Any match gives that code.
- **`ats_domains`:** an apply link to one of these (or a subdomain) is fine.
- **`shorteners`:** a link to one of these anywhere in the posting gives `link_shortener`.
- **`model`:** the local classifier: Hugging Face repository and pinned `revision`, the model file and its `sha256` (checked after download and before every use), the output index meaning "injection", and the `threshold` (0 to 1) at or above which the posting is blocked as `prompt_injection_model`.

Built-in checks with fixed codes: `hidden_characters` (block: Unicode tag characters or bidirectional overrides), and for links `apply_domain_unknown:<domain>`, `apply_not_https`, `apply_url_invalid`, `ip_link`, `punycode_link:<host>`, `link_shortener:<domain>` (warn). An apply link is fine when it goes to the source site (the domain of `url`), a known ATS, the domain of `company_website`, or a domain named after the company (`acme.com` for "Acme GmbH"); no apply link means applying on the source site.

### `data/jobs/jobs.json`

The job store: every posting from every source, once.

```json
{"updated_at": "2026-10-23T08:03:12+00:00", "jobs": [ { ... }, { ... } ]}
```

Each entry follows `schemas/job.schema.json` (no other fields allowed):

| Field | Meaning |
|-------|---------|
| `id` | `<site>:<source_id>`, e.g. `linkedin:4012345678`; the dedupe key |
| `data_source` | the actor's `name`, e.g. `APIFY_LINKEDIN_SCRAPER` |
| `source_id` | the posting's id at the source |
| `url`, `apply_url` | the posting page and the apply link |
| `title`, `description` | posting text, **untrusted** |
| `company_name`, `company_url`, `company_website`, `company_size` | company details as the source gives them |
| `location`, `work_modes` | `work_modes` is a list of `remote`, `hybrid`, `onsite` |
| `employment_type`, `seniority`, `salary` | as the source writes them |
| `posted_at`, `applicants_count` | as the source gives them |
| `search_id` | the query that found it first |
| `raw_file` | the raw file it came from, relative to `data/` |
| `status` | see below; `new` when fetched |
| `created_at` | when it was added to the store (ISO 8601, UTC) |
| `safety` | stage 2c: `verdict` (`pass`, `warn`, `block`), `reasons` (codes such as `fee_request`, `prompt_injection`, `prompt_injection_model`, `apply_domain_unknown:example.xyz`; never posting text), `model_score` (injection probability from the local classifier, `null` if checked without it), `checked_at`; absent until checked |
| `filter` | stage 2d rule filter: `passed`, `reasons` (`dealbreaker_company:<name>`, `dealbreaker_keyword:<word>`, `excluded_word:<word>`, `dealbreaker_industry:<name>`, `employment_type:<type>`, `seniority:<level>`, `work_mode:<modes>`, `language:<name>`, `same_posting:<id>` for a copy of posting `<id>` in another city; the values are the candidate's own words or fixed codes), `checked_at` |
| `extracted` | stage 2d, read out of the text by `job-extractor` (Haiku): `industry`, `seniority` (`intern` … `principal` or `null`), `min_years_experience`, `required_skills`, `preferred_skills`, `required_languages`, `salary` (`min`, `max`, `currency`, `period` or `null`), `visa_sponsorship` (`yes`, `no`, `null`), `model`, `extracted_at`; every string and list capped |
| `match` | stage 2d, scored by `job-matcher` (Sonnet or Opus): `score` (0-100), `summary` (≤ 400 characters), `strengths` and `gaps` (≤ 3 each, ≤ 160 characters), `model`, `scored_at` |
| `documents` | stage 3b, written by `jobfinder cv save` with the move to `cv_ready`: `cv`, `cover_letter`, `cover_letter_text`, `job_description` (paths relative to `data/`, e.g. `cv/24-10-2026/acme-software-gmbh-4012/Ayse-Yilmaz-CV.pdf`; the last two are missing on older records), `language` (`en`), `model` (the writer model), `created_at`, `reviewed` (`true` when the documents are `cv-reviewer`'s revision; missing otherwise); stage 4 reads the paths from here |
| `application` | stage 4, written by `jobfinder apply mark` when the candidate says they sent it, or by `jobfinder apply step` when the `job-sender` agent sent it: `applied_at` (ISO 8601, UTC; 00:00 of the day when the candidate gave only the day), `via` (`job-sender` when the agent sent it), `confirmed` (agent only: whether the candidate approved its summary before it submitted), `closed_at` (when `jobfinder track close` marked it `no_response`) |
| `tracking` | stage 5, replies found in Gmail by `mail-reader` (Haiku): `checked_at` (when the last search that covered the posting started), `outcome` (`assessment`, `interview`, `offer`, `rejection`: the kind of the latest reply that answers the application, or `null`), `replies` (≤ 50: `message_id`, `received_at` as `YYYY-MM-DD`, `kind`, `note` ≤ 160 characters, `saved_at`); no mail text |

Required: `id`, `data_source`, `source_id`, `title`, `status`, `created_at`.

Rules:

- **Dedupe by id.** A posting whose id is already in the store is skipped, never overwritten, so a new fetch never undoes what later stages added to an entry.
- **Reposts are skipped too.** A posting with a new id but the same fingerprint as one in the store (a repost, or the same job from another source) is not added; the run summary counts it under `reposts`. The fingerprint is `company|title|city`, each normalized: lowercase, accents and punctuation removed (`İ`/`ı` → `i`), legal forms dropped from the end of the company (`Inc`, `Ltd`, `GmbH`, `A.Ş.`, `Ltd. Şti.`, ...), bracketed parts dropped from the title (`(m/w/d)`), and only the part of the location before the first comma. It is computed in `jobs.py`, not stored. A posting without a company or title has no fingerprint and is only deduped by id.
- **Copies in several cities.** The same posting saved for several cities has a different fingerprint per city, so each is stored; the rule filter of stage 2d keeps one of them for the agents and marks the others `same_posting:<id>` (see `filter`).
- **Cleaning old reposts.** `jobfinder jobs dedupe` removes reposts already in the store (from before stage 2b): the first entry with a fingerprint stays, a later one goes only while its status is `new`.
- **Mapped by code.** One mapper (`sources/mapping.py`) maps every actor's items with that actor's `output` entry; no LLM is involved. A mapped item that does not pass the job schema, or has no id, is left out and counted as `invalid`.
- **Raw first.** The fetch (stage 2a) only saves raw files; `jobs.import_raw` (stage 2b) maps a raw file into the store. `fetch run` calls it for each file it saves, and `jobfinder jobs import` runs it again over any raw files, which is safe because known postings are skipped.
- **Safety check.** Every posting without `safety`, or with `model_score: null` once the model is downloaded, is checked as the last step of each fetch (`jobfinder jobs check` does it alone; `--all` checks every posting again). Stage 2d never reads a posting whose verdict is `block`.
- **Untrusted.** No command opens this file; posting text is read only by read-only agents (the two of stage 2d, `cv-writer` in stage 3b; `mail-reader` in stage 5 gets only the company and title), through files written by code, and only for postings that are not `block`.
- **Written atomically** (temporary file + rename).
- **Stage 2d** adds `filter`, `extracted` and `match`, in that order, to postings with verdict `pass` or `warn` and status `new`; each step runs once per posting (`jobfinder score filter --all` filters again after the preferences change). The values written by an agent are checked against this schema before they are stored. `status` does not change.
- **Stage 4** lists the postings with status `cv_ready`, never a `block` one; once the candidate confirms they sent the application, `jobfinder apply mark` sets `status` to `applied` and adds `application`. When the `job-sender` agent sent it (only a posting with verdict `pass`), `jobfinder apply step` does the same, with `application.via`. Nothing else is written.
- **Stage 5** adds `tracking` to every posting the candidate may have applied to and that is not rejected: status `shortlist`, `approved`, `cv_ready`, `applied` or `response`, or `new` with a score at or above the shortlist threshold (`min_score`), these last ones only for 60 days after they were saved; never a `block` posting; until a rejection arrives or the application is closed. `jobfinder track close` (`/close-stale`) moves an `applied` posting with no mail for 60 days or more to `no_response` and adds `application.closed_at`; a closed posting is no longer looked up. A posting not marked applied that gets mail about an application becomes `applied`, with `application.applied_at` = 00:00 UTC of that mail's day. A reply of kind `assessment`, `interview`, `offer` or `rejection` sets `tracking.outcome` and moves `applied` to `response`; `received` and `other` change nothing else. A Gmail message is stored once in the whole store.
- **Interview rounds.** `jobfinder track interview` (and the web UI's *Interview* buttons) moves an `applied` or `response` posting by hand to status `interview` with `application.interview_round` 1, then 2, 3 ... up to 9 (`--back` goes one round back, round 1 back to `applied`). `/track` keeps looking up mail for an `interview` posting and never changes its status.
- **Deleted postings.** The web UI's delete sets the status `deleted` (`jobs.delete`). The posting stays in `jobs.json` only as a marker: `jobs.add` skips a posting with the same id or the same company, title and city, so a search does not bring it back, and no list, stage or run reads it. Nothing sets another status on it.

Status lifecycle:

```
new → rejected
    → shortlist → approved → cv_ready → applied → response (tracking.outcome: assessment / interview / offer / rejection)
```

`jobfinder jobs approve` (step 3a, for 3b) moves `new` or `shortlist` to `approved`, never for a posting without a `pass` or `warn` verdict; `jobfinder cv save` moves `approved` to `cv_ready` once the documents are written.

`cv_ready → applied` is set by stage 4: `jobfinder apply mark`, only after the candidate says they sent the application, or `jobfinder apply step`, when the `job-sender` agent returns that the site confirmed it.

### `data/fetch/raw/<DATA_SOURCE>/<stamp>[_<search_id>].json`

The full actor output of one run of one query, kept for reprocessing (for example when an `output` mapping improves: `jobfinder jobs import`). `<stamp>` is the UTC time, `YYYYMMDDTHHMMSSZ`; the test run of `/setup-sources` has no `search_id`.

```json
{"data_source": "APIFY_LINKEDIN_SCRAPER", "actor": "curious_coder/linkedin-jobs-scraper",
 "run_id": "...", "search_id": "senior-backend-istanbul", "fetched_at": "...",
 "input": { ... }, "items": [ ... ]}
```

Untrusted, like the job store.

### `data/fetch/runs/<stamp>.json`

One summary per `jobfinder fetch run`. Counts, costs and paths only, never posting text, so it is safe to read.

```json
{
  "data_sources": ["APIFY_LINKEDIN_SCRAPER"], "date_posted": "anyTime",
  "started_at": "...", "finished_at": "...", "ok": true,
  "searches": [
    {"data_source": "APIFY_LINKEDIN_SCRAPER", "search_id": "senior-backend-istanbul",
     "count": 25, "new": 16, "already_saved": 7, "reposts": 2, "invalid": 0, "cost_usd": 0.025, "raw_path": "..."}
  ],
  "safety": {"checked": 16, "pass": 12, "warn": 3, "block": 1,
             "reasons": {"apply_domain_unknown": 3, "fee_request": 1}, "model": true},
  "total_count": 25, "total_new": 16, "total_reposts": 2, "total_invalid": 0, "total_cost_usd": 0.025, "jobs_path": "..."
}
```

`safety` counts the postings checked in this run (stage 2c) by verdict and by reason code, and `model` says whether the local classifier was used; if `config/safety.json` cannot be loaded, or the model file fails its hash check, it holds an `error` and the postings stay unchecked until `jobfinder jobs check`. The safety check is the last step of the run, after every query.

A query that failed has an `error` instead of the counts. A run stopped before it started (for example not enough Apify credit) has a top-level `error` and no searches.

### `data/score/settings.json`

Written by `jobfinder score configure` (`/setup-job-scoring`); read by every scoring run. Missing keys fall back to the defaults shown.

```json
{"matcher_model": "sonnet", "min_score": 70, "shortlist_size": 10, "max_per_run": 50,
 "updated_at": "2026-10-23T08:00:00+00:00"}
```

| Field | Values |
|-------|--------|
| `matcher_model` | `sonnet` (default), `opus` or `haiku`: the model `job-matcher` runs on; `job-extractor` always runs on Haiku |
| `min_score` | 0 to 100: the shortlist threshold |
| `shortlist_size` | 1 to 50: postings `jobfinder score list` returns |
| `max_per_run` | 1 to 500: postings one run extracts, and postings one run scores; the rest wait for the next run |

### `data/score/batches/<step>-<n>.json` and `data/score/results/<step>-<n>.json`

`<step>` is `extract` or `match`. `jobfinder score batch <step>` writes the batches and removes the step's old batches and results first; each holds the postings one agent reads (10 a batch), with only the fields it needs (no links, status or safety entry) and the description closed up and cut at 6,000 characters (`jobs.for_agent`; the store keeps it whole). A `match` batch also carries `profile.md` as `profile`:

```json
{"step": "match", "profile": "# Candidate profile ...", "jobs": [{"id": "linkedin:4012", "title": "...", "company_name": "...", "location": "...",
  "work_modes": ["hybrid"], "employment_type": "...", "seniority": "...", "salary": "...",
  "description": "...", "extracted": {"industry": "...", "...": "..."}}]}
```

The result file is the agent's answer, written unchanged by the `jobfinder.handoff` hook (or by `/execute-job-scoring` when the hook did not run) (a ```` ```json ```` fence around it is fine): `{"jobs": [{"id": "...", ...fields...}]}`, with the fields of `extracted` or `match` above. `jobfinder score save <batch> <result>` stores it. Both files hold posting text or text written from it: untrusted, like the job store.

### `data/cv/settings.json`

Written by `jobfinder cv configure` (`/setup-cv`); read by every `/execute-cv` run. A missing file means the default.

```json
{"writer_model": "sonnet", "review": true, "updated_at": "2026-10-23T08:00:00+00:00"}
```

| Field | Values |
|-------|--------|
| `writer_model` | `sonnet` (default) or `opus`: the model `cv-writer` runs on |
| `review` | `true` (default) or `false`: `cv-reviewer` (Sonnet) revises each saved CV and letter, one more agent run per posting |

### `data/cv/<DD-MM-YYYY>/<company>-<source_id>/`

One folder per posting, holding all its documents, under the day `jobfinder cv batch` ran (`24-10-2026`), named after the company and the source id in lowercase ASCII words joined by `-` (the company cut to 60 characters), e.g. `data/cv/24-10-2026/acme-software-gmbh-4012345678/`. `jobfinder cv batch` writes `input.json` and removes an old `answer.json`; `/execute-cv` writes the agent's answer unchanged to `answer.json`; `jobfinder cv save` checks it and writes the documents next to them, in the same folder even after midnight. Writing a posting again on the same day replaces its files; on another day it gets a new folder, and `documents` points to the newest.

`input.json`, what the agent reads (no name, email, phone or profile links):

```json
{"language": "en",
 "posting": {"id": "linkedin:4012", "title": "...", "company_name": "...", "location": "...",
             "work_modes": ["hybrid"], "employment_type": "...", "seniority": "...", "description": "...",
             "extracted": {"required_skills": ["..."], "...": "..."}, "match": {"score": 82, "strengths": [], "gaps": []}},
 "profile": {"headline": "...", "total_years_experience": 6, "about": "...", "based_in": "Istanbul, TR",
             "target_roles": ["..."], "work_authorization": {}, "open_to_relocation": true, "availability": {},
             "experience": [{"ref": "exp-1", "title": "...", "company": "...", "start": "2023-03", "end": null,
                             "location": "...", "technologies": ["..."],
                             "achievements": [{"ref": "exp-1-a1", "text": "..."}]}],
             "education": [], "skills": [], "languages": [], "certifications": [],
             "projects": [{"ref": "proj-1", "name": "...", "description": "...", "technologies": []}],
             "publications": []}}
```

`answer.json`, the agent's answer (a ```` ```json ```` fence around it is fine):

```json
{"id": "linkedin:4012", "summary": "...",
 "skills": [{"category": "Languages", "items": ["Python", "Go"]}, {"category": "Data", "items": ["PostgreSQL"]}],
 "experience": [{"ref": "exp-1", "bullets": [{"ref": "exp-1-a2", "text": "..."}, {"ref": "exp-1-a1"}]}],
 "projects": ["proj-1"], "cover_letter": ["...", "...", "..."]}
```

| Field | Rule (`jobfinder cv save`) |
|-------|------|
| `id` | the posting's id |
| `summary` | 1 to 500 characters |
| `skills` | 1 to 6 groups: `category` (at most 30 characters; not plain words → "Skills") and up to 20 `items`; only names in the profile's skills or in the technologies of its experiences and projects, written as the profile writes them, each once; others are dropped. No group left → one "Skills" group of the profile's skills |
| `experience` | per experience its `bullets`, most relevant first: the `ref` of one of its achievements and `text`, the rewrite. A rewrite over 220 characters, with a number the achievement does not have, a skill (from the profile or the posting) that neither the achievement nor the experience's technologies name, a link, an email address or hidden characters is replaced by the achievement as written; no `text` keeps it as written. Refs of another experience, that do not exist or repeat are dropped; an experience left out or left empty gets all its achievements |
| `projects` | at most 10 project refs; others are dropped |
| `cover_letter` | 2 to 6 paragraphs of at most 1,000 characters, 3,000 together; no greeting or sign-off (code adds them) |

The summary and the cover letter are rejected if they hold a number that is not in the profile (or in the posting's title or company name), a skill the posting asks for that the profile does not have, a link, an email address or hidden characters. "24/7" and a version right after one of the profile's skills ("Python 3.12") are not counted as numbers. An asked-for skill is in the profile when the profile lists it or names it anywhere the agent sees (an achievement, the about text, a project, a certification), under any of its names in `config/cv_check.json` (`aliases`: Postgres = PostgreSQL); the `generic` skills there (communication, leadership, ...) are never missing. A one- or two-letter skill does not match before `&` or `-` ("R" in "R&D", "Go" in "Go-to-market"). The skill list and the rewrite check use the aliases too, and a skill list keeps the profile's spelling. The documents are rejected (`render: ...`) if the text read back from a PDF lacks the name, the email, a heading, the summary, a bullet or a letter paragraph. Errors are `<field>: <code>` and never quote the text; on a rejection `cv save` also writes `rejection.json` next to the input, `{"id", "errors", "problems": [{"field", "code", "values"}]}`, where `values` are the numbers as written or the skill names that broke the rule, for the agent's second try only (the command does not open it); a later batch or a saved answer removes it. These files hold posting text or text written from it: untrusted, like the job store.

### `config/cv_check.json`

Terms of the stage 3b check. Not user data. `aliases` is a list of groups, each the names of one skill (`["PostgreSQL", "Postgres"]`); `generic` lists skills a posting may ask for that are not technologies. Matching ignores case and extra spaces. Add a test in `tests/test_cv.py` for a new kind of entry.

### `data/apply/settings.json`

Written by `jobfinder apply configure` (`/setup`); missing means the defaults:

```json
{"confirm_before_submit": true, "max_per_day": 10, "sender_model": "sonnet"}
```

`confirm_before_submit`: the candidate approves the summary of every application before the agent submits it (`false`: the up-front confirmation of `/apply` is enough, and the agent submits after the summary without asking again). `max_per_day`: applications the agent may send a day (UTC), at least 1. `sender_model`: the model `/apply` starts the agent on, `sonnet` (default) or `opus`.

### `data/apply/answers.json`

The candidate's answers to form questions the profile does not answer, by a key made from the question (`notice-period-in-weeks`); `jobfinder apply answer` writes them, unless the candidate said they are for that application only:

```json
{"answers": {"notice-period-in-weeks": {"question": "Notice period in weeks", "answer": "4",
                                        "saved_at": "2026-10-24T09:00:00+00:00"}}}
```

They reach the agent as `saved_answers` in its input, and a question with a saved answer is not asked again. Answers are cleaned (no links, hidden characters or line breaks, at most 1,000 characters). Delete an entry to be asked again.

### `data/apply/current.json`

The application in progress, written by `jobfinder apply batch` and removed when it ends (sent, stopped, or `jobfinder apply stop`); a new `batch` replaces it. The hook reads it on every browser call of the agent:

```json
{"id": "linkedin:4012", "folder": "/.../data/apply/24-10-2026/acme-software-gmbh-4012",
 "started_at": "2026-10-24T09:00:00+00:00", "domains": ["linkedin.com"], "trusted": [], "company_name": "Acme Software GmbH",
 "uploads": ["/.../Ayse-Yilmaz-CV.pdf", "/.../Ayse-Yilmaz-Cover-Letter.pdf"],
 "reads": ["/.../input.json", "/.../Ayse-Yilmaz-CV.pdf", "..."],
 "questions": {"notice-period-in-weeks": "Notice period in weeks"},
"ready": true, "confirmed": false, "stopped": null, "off_domain": null, "steps": 2}
```

`domains` are the posting's own (its site, apply link and company website) and those of `trusted_domains.json`, plus any the agent trusts in this run (`trusted`); the ATS list of `config/safety.json` is allowed too, subdomains included. `uploads` and `reads` are the only files the agent may attach and read. `questions` are the ones the agent asked, the only keys `apply answer` takes. `ready` is set by a summary and `confirmed` by `jobfinder apply confirm`. `stopped` (`reason`, `codes`, `at`) is set by the hook on a finding or a hook error, or by `step` for a page on another domain or a step the hook never saw (`guard_not_running`); after it, every call but listing and closing tabs is denied. `off_domain` is the domain of the page the agent is on while it is outside `domains` (set by the hook, cleared when the page is allowed again or `step` trusts it); while it is set, clicking and typing are denied.

### `data/apply/trusted_domains.json`

Domains the agent judged to belong to a company (`new_domain`) and code accepted, written by `jobfinder apply step`; every later run allows them. Delete an entry to stop trusting it.

```json
{"domains": {"fresha.com": {"added_at": "2026-10-24T09:00:00+00:00", "job_id": "linkedin:4012",
                            "company_name": "Fresha", "note": "Apply button led to the careers site."}}}
```

### `data/apply/<DD-MM-YYYY>/<company>-<source_id>/`

`input.json`, what the agent reads, written by `jobfinder apply batch`:

```json
{"id": "linkedin:4012", "title": "...", "company_name": "...", "location": "...",
 "start_url": "https://www.linkedin.com/jobs/view/4012/",
 "allowed_domains": ["linkedin.com", "greenhouse.io", "..."],
 "documents": {"cv": "/.../Ayse-Yilmaz-CV.pdf", "cover_letter": "/.../Ayse-Yilmaz-Cover-Letter.pdf",
               "cover_letter_text": "/.../Ayse-Yilmaz-Cover-Letter.txt"},
 "applicant": {"full_name": "...", "first_name": "...", "last_name": "...", "email": "...", "phone": "...",
               "city": "...", "country": "...", "linkedin": "...", "github": "...", "portfolio": "...",
               "headline": "...", "total_years_experience": 6, "current_title": "...", "current_company": "...",
               "experience": [], "education": [], "skills": [], "languages": [], "certifications": [],
               "work_authorization": {}, "open_to_relocation": true, "office_days_per_week": 3,
               "availability": {}, "min_salary": {}},
 "saved_answers": [{"question": "Notice period in weeks", "answer": "4"}]}
```

`answer.json`, the agent's answer at each stop, written unchanged by the `jobfinder.handoff` hook when the agent stops (by `/apply` when the hook did not run) (a ```` ```json ```` fence around it is fine); `jobfinder apply step` checks it, renames it to `step-<n>.json` and logs it:

| Field | Rule (`jobfinder apply step`) |
|-------|------|
| `id` | the run's posting |
| `status` | `needs_input`, `ready_to_submit`, `submitted`, `new_domain` (a page outside the run's domains the agent judged to be the company's: code trusts its domain unless `trust_problem` refuses it: not https or not plain, punycode, a shortener, more than 3 in the run) or `stopped` |
| `url` | the page's address (needed but for `stopped`); its domain must be allowed, else the run stops (`domain_not_allowed`) |
| `questions` | `needs_input`: 1 to 15 fields, each `label` (≤ 500), `type` (`text`, `textarea`, `number`, `date`, `select`, `radio`, `checkbox`, `other`), `options` (≤ 50), `required` |
| `filled` | `ready_to_submit`: ≤ 80 fields, each `label`, `value` (≤ 5,000), `source` (`profile`, `answers`, `cover_letter`, `document`, `default`) |
| `uploaded` | `ready_to_submit`: `cv`, `cover_letter` |
| `reason` | `stopped`: `domain_not_allowed`, `login_required`, `account_required`, `captcha`, `sensitive_field`, `prompt_injection`, `no_apply_button`, `closed`, `page_error`, `candidate_cancelled`, `other`; a reason the run already has (from the hook) wins |
| `note` | ≤ 500 characters |

What `step` shows the candidate (labels, options, values, the note) is cleaned: no links, hidden characters or line breaks, labels at most 150 and values 300 characters. Errors are `answer.json: /<path>: <check>` and never quote the text. `user-answers.json`, written by the command for `jobfinder apply answer`: `{"answers": {"<key>": "<answer>"}, "once": ["<key>"]}`; keys the agent did not ask are ignored, keys in `once` are not saved. Every file but `log.json` and `guard-seen` holds page text or the candidate's data.

### `data/track/batches/track-<n>.json` and `data/track/results/track-<n>.json`

`jobfinder track batch` writes the batches (10 postings each) and removes the old batches and results first. Each holds the Gmail search query and only the short fields the agent needs to match a message:

```json
{"step": "track", "made_at": "2026-10-23T08:00:00+00:00", "since": "2026-10-13",
 "query": "after:2026/10/13 -from:me -in:chats {\"Acme Software\" \"Globex\"}",
 "jobs": [{"id": "linkedin:4012", "company_name": "Acme Software GmbH", "title": "...",
           "applied_on": "2026-10-15", "apply_domain": "boards.greenhouse.io"}]}
```

`since` is two days before the earliest last check (`tracking.checked_at`), apply day or, for a posting not applied to yet, day saved (`created_at`) of the batch; `applied_on` is `null` for a posting not applied to yet; company names in the query have no search operators and no legal form.

The result file is the `mail-reader` agent's answer, written unchanged by the `jobfinder.handoff` hook (by `/track` when the hook did not run) (a ```` ```json ```` fence around it is fine): `{"replies": [{"job_id": "...", "message_id": "...", "received_at": "YYYY-MM-DD", "kind": "...", "note": "..."}]}`. `jobfinder track save <batch> <result>` stores it: replies to postings outside the batch, messages already stored, invalid values and days outside the search window are left out, and every posting of the batch gets `checked_at` = `made_at`. The result is written from mail: untrusted.

### `data/cost/usage.json`

Written by the cost hook (`python -m jobfinder.cost`) at every `SubagentStop` of this repository's agents and every `Stop` of the main session, and refreshed by `jobfinder cost report` from the transcripts still on disk. One entry per agent run, keyed `<session_id>/<agent_id>`, and per command of the main session, keyed `<session_id>/main/<n>` (all of a session's `main` entries are replaced together):

```json
{"runs": {
  "<session_id>/<agent_id>": {
    "agent_type": "job-sender", "stage": "apply", "job_id": "linkedin:4012",
    "input": "data/apply/24-10-2026/acme-software-gmbh-4012/input.json", "root": "/path/to/job-finder",
    "session_id": "...", "agent_id": "...", "transcript": "~/.claude/projects/.../subagents/agent-<agent_id>.jsonl",
    "started_at": "2026-10-24T17:02:11Z", "ended_at": "2026-10-24T17:09:40Z", "requests": 38,
    "tokens": {"input": 410, "cache_write_5m": 0, "cache_write_1h": 61000, "cache_read": 1450000, "output": 9200},
    "total_tokens": 1520610,
    "by_model": {"claude-opus-5-5": {"requests": 38, "tokens": {"...": 0}, "cost_usd": 0.71}},
    "cost_usd": 0.71, "unknown_models": []},
  "<session_id>/main/2": {"agent_type": "main", "command": "/apply", "...": "..."}}}
```

`stage` comes from the agent (`cv-writer` and `cv-reviewer` → `cv`, `job-sender` → `apply`, `job-extractor` and `job-matcher` → `score`, `mail-reader` → `track`, `profile-extractor` → `profile`). `job_id` is read by code from the file named in the agent's first prompt (`Input file:` / `Batch file:`), only under `data/`: `posting.id` of a `data/cv/` input, `id` of a `data/apply/` input; a batch under `data/score/` or `data/track/` gives `job_ids` instead. `command` is the slash command or Skill-run command started last (`null` before any). Numbers in the example are made up. The file holds no posting or page text.

### `config/pricing.json`

Claude API prices in USD per million tokens per model id (`input`, `cache_write_5m`, `cache_write_1h`, `cache_read`, `output`), with `source` and `as_of`; `long_prompt` (`above` + the same keys) for a model priced higher when a request's prompt (input + cache writes + cache reads) is over `above` tokens (Haiku 5.5). A dated or suffixed id (`claude-haiku-4-5-20251001`) takes the listed id it starts with. Update it from the pricing page when prices change; it holds no personal data.
