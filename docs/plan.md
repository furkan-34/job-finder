# Project plan: personal AI job finder

A Claude-based, agentic job search application built for my own needs.
Way of working: the simplest thing that works, step by step. Before any large piece of code in a stage, a proposal is made and approved.

How the parts are built is in [architecture.md](architecture.md); file formats are in [data.md](data.md); commands and tools are in [commands.md](commands.md). This file holds the goals, the rules and the decisions.

## Contents

1. [Stages](#stages)
2. [MVP goal](#mvp-goal)
3. [Rules](#rules)
4. [Profile schema decisions](#profile-schema-decisions)
5. [Data source: Apify](#data-source-apify)
6. [Stage 2a decisions: fetching](#stage-2a-decisions-fetching)
7. [Stage 2b decisions: dedupe](#stage-2b-decisions-dedupe)
8. [Stage 2c decisions: safety check](#stage-2c-decisions-safety-check)
9. [Stage 2d decisions: filter and score](#stage-2d-decisions-filter-and-score)
10. [Stage 3b decisions: CV and cover letter](#stage-3b-decisions-cv-and-cover-letter)
11. [Stage 4 decisions: application](#stage-4-decisions-application)
12. [Stage 5 decisions: tracking](#stage-5-decisions-tracking)
13. [Open questions](#open-questions)

---

## Stages

5 stages, one command each. Each command runs the narrow commands of its steps in order (with the Skill tool), and each narrow command still works alone, e.g. `/setup-job-scoring` to change only the threshold.

| # | Stage | Command | Steps (narrow commands and tools) |
|---|-------|---------|-----------------------------------|
| 1 | Setup | `/setup` | 1a candidate profile (`/create-profile`), 1b sources and tool check (`/setup-sources`: Apify token, a 5-posting test), then the settings of every later stage: search (`/setup-job-search`), scoring (`/setup-job-scoring`), CV writer (`/setup-cv`), the `job-sender` agent (`jobfinder apply configure`), and checks of the safety model, Gmail and the browser |
| 2 | Search | `/search` | 2a fetch, 2b normalize + dedupe, 2c safety check (all three in `/execute-job-search`, i.e. `jobfinder fetch run`), 2d filter + score (`/execute-job-scoring`); then goes on to `/write-cv` (stage 3), whose question for how many postings is the place to stop |
| 3 | CV + cover letter | `/write-cv` | 3a choose how many postings (`/approve-jobs`), 3b write the documents (`/execute-cv`) |
| 4 | Application | `/apply` | Confirm up front how many postings (e.g. the first 10), then the `job-sender` agent applies to each in a Playwright browser window, one at a time. By hand: `/execute-job-apply`, `/mark-applied` |
| 5 | Tracking | `/track` | Claude reads mail about applications in Gmail (LinkedIn "application sent", confirmations, replies), classifies it and updates each posting's status |

`/show-list` shows every posting and the stage it has reached, at any time.

What each step does:

| Step | Description |
|------|-------------|
| 1a Candidate profile | `profile.json` is extracted from the CV. `profile.md` is rendered from that JSON by code. Missing required fields are filled by asking (search preferences included). |
| 1b Sources and tool check | Check the API keys, run a 5-posting test per source. |
| 2a Fetch | Runs the actors, keeps the raw output. |
| 2b Normalize + dedupe | One Apify mapping script turns raw output into the job schema, from each actor's output mapping in config; the job store skips postings seen before and reposts of them. |
| 2c Safety check | Runs before any LLM call: code and a small local classifier mark each posting `pass`, `warn` or `block` (fees, prompt injection, suspicious links, unknown apply domain). |
| 2d Filter + score | 1) Rule filter in code, 2) structured extraction with Haiku, 3) deep matching with Sonnet/Opus (score + reasoning). |
| 3a Approval | How many of the scored postings get documents, best score first. |
| 3b CV + cover letter | ATS-friendly. Only real facts from `profile.json`; experience is never invented. |
| 4 Application | By the `job-sender` agent in a Playwright browser window, which asks me what the profile does not answer; by hand through the links in a table as the fallback. |
| 5 Tracking | Mail is classified (applied, rejection, interview, ...) and the posting's status in `jobs.json` is updated automatically. |

Everything runs by hand from Claude Code; there is no scheduled run.

### Shared components

- **Gmail:** where replies to applications arrive; read by Claude in stage 5, never sent from.
- **Posting status:**
  ```
  new → rejected
      → shortlist → approved → cv_ready → applied → response (rejection / interview)
  ```
- **Logging and cost tracking.** The cost hook records the tokens of every agent run and command in `data/cost/usage.json`; `/cost-report` shows them per posting, agent, command or day (see "Cost tracking decisions").

## MVP goal

- Stages 1-2 work **with a single source** and give a shortlist.
- Stages 3-5 are added after the loop has been used for a few days. (All five are done.)

## Rules

### Data model
- One source-independent `Job` schema.
- One mapping script for all Apify actors; each actor's input template and output mapping live in `config/apify_actors.json`.
- Raw JSON is always kept (for reprocessing).

### Safety
- Posting text is **untrusted data** (prompt injection risk). An agent that reads posting text has no permission to send email or apply; the stage 2d and stage 3b agents have the Read tool only. The one exception is the stage 4 `job-sender` agent: it applies only to a posting the candidate picked, on the posting's own domains, after the candidate approves; it cannot send email, and a hook checks each of its calls. Web pages are untrusted like postings.
- Fake postings are detected (postings asking for fees, suspicious links).
- Prompt injection is detected in every field of a posting, not only the description.
- Apply links are checked to go to a real company domain or a known ATS.

### Privacy
- `profile.md` contains no contact details. Contact details are used when generating the CV, by the code that renders it, and by the stage 4 `job-sender` agent, which types them into the application form on the posting's own domains; no other agent sees them.
- No user data is committed: everything under `data/` (CV, profile, searches, settings, postings, raw output, run summaries, logs) is git-ignored; only the folder structure is in the repository.

### Cost
- **Scoring on the Claude plan.** The LLM steps run as Claude Code agents on the user's own Claude login: no API key and no per-token cost, but they count toward the plan's usage limits. Claude Code caches the fixed scoring rubric (the matcher agent's prompt) itself.
- **Cheapest step first.** The rule filter in code, then Haiku; Sonnet or Opus only reads postings that are still in, and one run reads at most `max_per_run` postings (50 by default).
- **Lean prompts.** The agents start without CLAUDE.md (`omitClaudeMd: true`): everything they need is in their own file and input, and CLAUDE.md is larger than a scoring batch. The entry-point commands `/search`, `/write-cv`, `/apply` and `/track` run on Sonnet (`model: sonnet` in their frontmatter, for the command's turn): they follow a fixed procedure and code makes every decision that matters.
- **Tokens are recorded.** Every LLM step's tokens are read from Claude Code's own transcripts by a hook, without an LLM; dollars are an estimate at API prices.
- **API as the fallback.** The Anthropic API with prompt caching and the Batch API (50% cheaper overnight) was weighed and set aside in stage 2d; it is the way back if the plan's limits get in the way.

### Secrets
- Secrets (`APIFY_TOKEN`, ...) live in `.env` and are **never committed**.

## Profile schema decisions

### Field levels

| Level | Behaviour |
|-------|-----------|
| Required | Asked if missing; cannot be left empty. |
| Recommended | Asked, but can be skipped. |
| Optional | Taken from the CV if present; not asked separately. |

### A. Who I am

- **Identity and contact**
  - Required: full name, email, city/country
  - Recommended: phone, LinkedIn
  - Optional: GitHub, portfolio
- **Summary**
  - Required: headline, total years of experience
  - Optional: short summary paragraph
- **Experience list** (required): company, title, start/end (`YYYY-MM`, empty if ongoing), location, work mode, technologies. Achievements as bullets, with numbers where possible; a weak one gets a follow-up question.
- **Education list** (required): school, degree, field, years. GPA optional.
- **Skills** (required)
  - Technical skills: name + level (expert / advanced / intermediate)
  - Languages (separate field): language + level (A1–C2 or native)
- **Work authorization** (required): countries where I can work without sponsorship, whether sponsorship is needed.
- **Extras** (optional): certificates, projects, publications.

### B. What I want

- Required: target roles (several allowed), seniority, locations + work mode (remote / hybrid / onsite), employment type.
- Required: dealbreakers (industry, company, keyword). The lists may be empty, but must be explicitly confirmed as "none".
- Recommended: minimum salary + currency (not a hard filter; affects the score), openness to relocation, office days a week (0-5; at `/apply` a question asking for that many days or fewer is answered yes), notice period / start date.
- Optional: must-haves, nice-to-haves, preferred company size / industry.

### C. Meta

- Schema version, last update date, source CV file.
- "Pending confirmations" list: fields taken from the CV but uncertain are confirmed with the user.

### Other decisions

- A single profile. Per-role emphasis is done when generating the CV.
- The profile is stored in English (may change if the target market turns out to be mainly Turkey).
- `profile.md` starts with the preferences.
- **More than the CV.** `/create-profile` also reads the optional documents in `data/profile/sources/`: a LinkedIn data export (its profile, positions, education, skills, certifications, projects and languages CSV files; never messages or connections), certificates, project write-ups. The CV is the base; a value only another document gives, and every disagreement (dates, titles), becomes a pending confirmation. On an existing profile, option C asks about each addition and conflict the documents give, and writes only what the candidate accepts. The more true facts the profile has, the fewer the stage 3b check drops. Taken from ai-job-search's setup "Path A" and `/expand`. GitHub is not read: the agent has no web access, so a project goes in as a write-up.

## Data source: Apify

### Basic concepts

| Concept | Meaning |
|---------|---------|
| Actor | A ready-made scraper |
| Run | One execution of an actor |
| Dataset | The output of a run |
| Task | A saved input |
| Webhook | Notification when a run ends (the data is read through `resource.defaultDatasetId` in the payload) |

### Pricing
- Free plan: $5 credit per month (does not roll over).
- Starter plan: $29.

### Integration
- **Pipeline:** Python `apify-client`, run from this project by `/search` (no Apify Schedule or webhook).
- **Later:** Apify MCP (`https://mcp.apify.com?tools=...`) for a "let the agent search live" feature. Not added yet.

### LinkedIn actor: `curious_coder/linkedin-jobs-scraper`

- Cost: about $1 per 1,000 results.
- **Input:** `urls` or filters (`keywords`, `location`, `geoId`, `distance`, `datePosted`: `anyTime` / `past24Hours` / `pastWeek` / `pastMonth`, `companyIds`, `under10Applicants`), `limitPerSource`, `scrapeCompany` (default `true`, slow), `splitByLocation` + `splitCountry` (to get past the 1,000-posting limit), `autoConvertToAiSearch`.
- Since August 2026 LinkedIn search is AI based. Filters such as seniority, employment type and remote now go into the keywords.
- **Output fields:** `id`, `link`, `title`, `companyName`, `location`, `salaryInfo`, `postedAt`, `descriptionText`, `applicantsCount`, `seniorityLevel`, `employmentType`, `workplaceTypes`, `companyEmployeesCount` and others.
- **Risks:**
  - LinkedIn's terms of service forbid automated collection.
  - The actor is never given my own LinkedIn cookies.
  - The application does not depend on a single source.

### Other sources

| Source | Actor(s) | Note |
|--------|----------|------|
| Indeed | `valig/indeed-jobs-scraper`, `borderline/indeed-scraper` | UK added as APIFY_INDEED_UK_SCRAPER (valig; `memo23/apify-indeed-cheerio` is the pricier alternative with 60+ countries). Other countries are not added yet. |
| Totaljobs (UK) | `valig/totaljobs-jobs-scraper` | Added as APIFY_TOTALJOBS_SCRAPER. Other Totaljobs actors (`bovi`, `piotrv1001`, `clearpath`) are alternatives. |
| Reed (UK) | `scrapersdelight/reed-jobs-scraper` | Added as APIFY_REED_SCRAPER. Reed's free official API (`/api/1.0/search`, `/jobs/{id}`, API key) is the alternative. |
| ATS (Greenhouse / Lever / Ashby) | none: the companies' free public job board APIs | Added as GREENHOUSE, LEVER and ASHBY (`sources/ats.py`), from a company list (`boards`). The Apify actors `webdata_labs/greenhouse-lever-ashby-jobs-scraper` and `fetch_cat/ats-jobs-scraper` are the paid alternative. |
| Company research | `apify/rag-web-browser` | |

## Stage 2a decisions: fetching

- **Setup and execute are separate.** `/setup-job-search` asks and saves everything; `/execute-job-search` runs the search with the saved settings and asks nothing. This setup/execute split is the pattern for later stages too; `/setup` runs every setup half in order and `/search` the execute halves of stage 2.
- **Orchestration is a command, not an agent.** It has to ask the user questions, and subagents in Claude Code cannot. Agents are kept for isolated work without questions (the CV reader, later the agents that read posting text).
- **Narrow commands.** `/setup-searches` only builds and saves the queries. The orchestrating command calls it through the Skill tool.
- **Queries:** one per target role × location from the profile (at most 8). Seniority and work mode go into the keywords (LinkedIn AI search). The user picks which queries to keep and how many postings each returns (25 by default).
- **No scheduled run.** A recurring run through launchd / cron was built first and removed later, unused: the search runs when the candidate runs `/search`. Every run fetches all postings; the date filter (last 24 hours / week / month) is an optional question and only lowers cost, because postings already saved are skipped anyway.
- **Budget:** a run does not start if its worst-case cost (sum of the limits × the actors' price per result) is more than the Apify credit left this month.
- **Actor list:** the usable Apify actors are listed in `config/apify_actors.json`. The setup shows the list and asks which to use (with an "All" option); the choice is saved in `data/fetch/settings.json`. A posting's `data_source` is the actor's name in that list.
- **freehire.me as a second source** (part of "all"): its free JSON API (about 50 ATS and job boards, tech roles; 96 Python postings for Turkey when this was written) is read by `sources/freehire.py` and mapped by the same `output` config as an Apify actor, so the job schema, dedupe, safety check and scoring are unchanged; no token, no cost. It searches by country (a posting's city is not a filter), drops work-mode words from the keywords, and its apply links can be a job board's redirect (`warn` from the safety check, and `job-sender` asks before trusting a new domain). A personal, best-effort service with no SLA: when it is unreachable that search fails and the others go on. Taken from ai-job-search's `freehire-search` skill.
- **ATS company boards as free sources** (part of "all"; idea from career-ops' portal scanner): Greenhouse, Lever and Ashby publish every open posting of a company as JSON without a token (`boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true`, `api.lever.co/v0/postings/<board>?mode=json`, `api.ashbyhq.com/posting-api/job-board/<board>`), with the full description and the ATS's own apply link, so the safety check's domain rule passes. There is no search, so each entry lists its companies in `boards` and `sources/ats.py` filters in code (all keywords in the title, country by `countries`, date, newest `limit`); a board is read once per run and reused by the other searches. Cost 0; fewer postings than an aggregator, but only the companies the candidate chose, and no LinkedIn dependency. A board that does not exist fails that search (404) and the others go on. Risk: the endpoints are public but undocumented for scraping at volume; requests are bounded (one per board per run, retries only on 429/503).
- **reed.co.uk as a third source** (part of "all"): Apify `scrapersdelight/reed-jobs-scraper` (about $0.50 per 1,000 postings with the detail page, so the full description and the external apply link when there is one; maintained, pay per event). Config only plus three generic `build_input` options: work-mode words are dropped from the keywords, `pastMonth` has no Reed value and fails that search (Reed's filters stop at two weeks), and the location must be in the UK (`location_countries`): the city before the first comma is searched within 10 miles, a country-only location searches the whole UK, and a search elsewhere fails alone, so with "all" every non-UK search reports an error for Reed. Easy-apply postings have only the Reed page as apply link. Chosen over the other Reed actors (higher price, no detail page, not maintained or deprecated); Reed's free official API was the alternative and is still an option if scraping becomes a problem.
- **Indeed UK and Totaljobs as fourth and fifth sources** (part of "all"; chosen for a London search): Apify `valig/indeed-jobs-scraper` (about $0.10 per 1,000 postings, 37k users, country fixed to `uk`) and `valig/totaljobs-jobs-scraper` (about $0.28-0.40 per 1,000, small and new). Config only, like Reed (`drop_words`, `location_countries` with `{city}`, `date_posted_values`), plus one generic `build_input` rule: an input key whose value is `null` is not sent, so `anyTime` sends no date filter (both actors list only 1, 3, 7 and 14 days: `past24Hours` is `1`, `pastWeek` is `7`, `pastMonth` fails that search). Like Reed they fail every non-UK search, so with "all" a search outside the UK reports errors for all three UK sources. Input and output field names are from the actors' Apify pages and not yet compared with a live run; Indeed's `jobTypes` and `baseSalary` (objects) are not mapped yet.
- **Job store:** all postings in one file, `data/jobs/jobs.json`, in the shape of `schemas/job.schema.json`. The id is `<site>:<id>`; a posting already in the file is skipped, never overwritten.
- **Mapping is code, not an LLM** (one script for all actors since stage 2b). The actor output is JSON with fixed field names, and posting text is not given to an LLM before the safety check of stage 2c. Reading meaning out of the text (requirements, technologies, ...) is done in stage 2d with Haiku and added to the same record.
- **Effect on stage 2b:** one schema and id-based dedupe are done. Left for stage 2b (now done): catching the same posting reposted under a new id (company + title + location fingerprint), and a database if one JSON file stops being enough.

## Stage 2b decisions: dedupe

- **Built on stage 2a.** One schema and id-based dedupe already came with the fetch. No new slash command: stage 2b runs inside `jobfinder fetch run` after each raw file is saved, and alone as `jobfinder jobs import`.
- **One Apify mapping script, not one adapter per actor.** `sources/mapping.py` reads each actor's `output` entry in `config/apify_actors.json` (job field → actor output field, plus a few named transforms) and its `input` template. Adding an actor is a config entry, not code. Deterministic, no LLM.
- **Raw is the source of truth.** Mapping reads the saved raw files, so a better mapping can be applied to old fetches with `jobfinder jobs import`. A mapped item that fails the job schema is counted as `invalid`, not stored.
- **Repost = same fingerprint.** `company|title|city`, normalized in code (case, accents, punctuation, legal forms, bracketed title notes). Rules in [data.md](data.md#datajobsjobsjson). It also catches the same job from a second source.
- **A repost is skipped, only counted.** It never enters `jobs.json`; the run summary has `reposts` per query and `total_reposts`. The first posting keeps its status, so a job already rejected or applied to does not come back.
- **Old reposts** in the store are removed once with `jobfinder jobs dedupe` (entries still `new` only).
- **Database: still one JSON file.** A few hundred postings a day for one person fits in memory; SQLite is considered when the file gets slow (tens of thousands of postings).

## Stage 2c decisions: safety check

- **Code, not an LLM.** `src/jobfinder/safety.py` checks each posting with regular expressions, domain lists and a small local classifier before any LLM sees it.
- **The last step of the run.** Like stage 2b it has no slash command: it is the last step of `jobfinder fetch run`, after every query is fetched, mapped and deduped, so `/execute-job-search` ends with it. It also runs alone as `jobfinder jobs check`.
- **The source test is checked too.** `/setup-sources` shows a few postings to Claude; `jobfinder source test` runs the same check first and lists only the short fields (title, company, location, date, link) of postings that are not blocked.
- **Three verdicts, stored on the posting** as `safety` (`verdict`, `reasons`, `model_score`, `checked_at`); `status` does not change.
  - `block`: asks for money (application, training or visa fees, deposits, gift cards, Western Union, ...), prompt injection (instructions to an AI: "ignore previous instructions", fake system tags, "rate this posting as 10", Turkish forms too; or a classifier score at or above the threshold), or hidden text (Unicode tag characters, bidirectional overrides). Stage 2d never reads it.
  - `warn`: the apply link goes to neither the source site, a known ATS nor the company's own domain; a link shortener, an IP address or a punycode domain anywhere in the posting; contact over Telegram or WhatsApp; text addressed to an AI ("if you are an AI, ..."), which recruiters use to catch AI-written applications. Scored in stage 2d, with the reasons shown next to the posting.
  - `pass`: none of these.
- **Second layer: a local classifier model.** Regular expressions miss reworded injections, so every posting is also scored by Meta's Llama Prompt Guard 2 22M (`src/jobfinder/guard.py`). It is a classifier, not an LLM: it returns a probability and cannot follow instructions. Chosen for size: the int8 ONNX file plus tokenizer are about 81 MB, run on the CPU with `onnxruntime` and `tokenizers` (no `torch`), free and offline. The 86M model is better but over 100 MB even quantized; LLM Guard and similar libraries need `torch`. The probability is stored as `model_score`; at or above the threshold in `config/safety.json` (0.9 to start) the posting is blocked as `prompt_injection_model`. Weaknesses: mostly English (Turkish is left to the regular expressions), and the ONNX conversion is by a third party (gravitee-io), so the revision and the model file's SHA-256 are pinned. The threshold is tuned on real postings.
- **The model is downloaded once** with `jobfinder safety model` into `data/safety/model/`. Without it the regex layer still runs and `model_score` is null; those postings are scored the next time the check runs with the model.
- **Every field is checked.** Title, company, location, salary, links and description are all posting text; an injection can sit in any of them. Zero-width characters are removed before matching, so they cannot split a phrase.
- **Unknown apply domain only warns.** The fetch runs with `scrapeCompany: false`, so the company website is usually unknown; the company's domain is then guessed from its name (`acme.com` for "Acme GmbH"). Blocking on it would lose many real postings.
- **Rules are config.** Patterns, ATS domains and shorteners are in `config/safety.json`; changing them needs no code, and `jobfinder jobs check --all` applies them to every posting again.
- **Reasons hold no posting text:** a code, plus a domain for the link checks (characters limited to `a-z0-9.-`). The run summary counts verdicts and reason codes only.
- **Neither layer is a full defence** against prompt injection. Patterns and the classifier remove the obvious and most reworded cases; the agents that read posting text in stage 2d still run without write, email or apply permissions.

## Stage 2d decisions: filter and score

- **Claude Code agents, not the API.** Extraction and scoring run as Claude Code subagents on the user's Claude login, like every other step: no `ANTHROPIC_API_KEY`, no per-token cost, and the Python tools stay deterministic. The API (Haiku 5.5 + Sonnet 5.5 with the Batch API) was estimated at about $9 a month for 50 new postings a day; it would have given the reading model no tools at all. Kept as the fallback.
- **Setup and execute.** `/setup-job-scoring` asks for the scoring model (Sonnet or Opus), the shortlist threshold (70) and size (10) and the postings read per run (50), saved in `data/score/settings.json`; `/execute-job-scoring` asks nothing. `/search` runs it right after the fetch, so a search ends with a shortlist.
- **Three steps, cheapest first.**
  1. **Rule filter, code** (`score/rules.py`), on postings with verdict `pass` or `warn` and status `new` only: dealbreaker companies (legal forms ignored) and keywords (whole words, in the title, company and description), the employment type and seniority as the source writes them (LinkedIn's levels map to the profile's: "Mid-Senior level" covers mid, senior and lead), and the work mode. A value the tables do not know never rejects. Then one posting is kept of each group of copies, the same company, title and description (its own city aside) saved for several cities: the one already worked on, else the one in a city of the candidate's locations, else the first saved; the others get `same_posting:<id>` and are never read by an agent, and the lists show their cities as `also_in` next to the one kept.
  2. **Structured extraction, Haiku** (`job-extractor` agent, 10 postings per batch): industry, seniority, minimum years, required and preferred skills, required spoken languages, salary, visa sponsorship. Stored as `extracted`, for scoring now and for the CV later. The rule filter then runs again with these: a dealbreaker industry, a required language the profile does not list, an extracted seniority more than one step from every wanted level (a senior candidate still sees mid and lead roles).
  3. **Deep matching, Sonnet, Opus or Haiku** (`job-matcher` agent, 10 postings per batch, the profile inside the batch file so the agent reads one file): a score from 0 to 100 on a fixed rubric (role and seniority 30, skills and experience 30, location, work mode and work authorization 20, pay and terms 10, preferences 10), a short summary, and at most three strengths and three gaps. Stored as `match`.
- **Agents that read posting text can only read.** Both agents have the Read tool only: no Write, Edit, Bash, web or email. Code writes the batch files they read (`data/score/batches/`, only the fields they need, never a `block` posting); the orchestrating command copies each answer unchanged into `data/score/results/`, and `jobfinder score save` checks it against the job schema (enums, numbers, list and string lengths) and keeps only postings of that batch. A broken or invalid answer leaves its postings for the next run.
- **What is left of the risk.** An injection that got past stage 2c could still bend the score or the summary of its own posting. The rubric tells the matcher to ignore such text and name it as a gap, the schema caps what an answer can hold, and the orchestrating command only copies answers into files.
- **Status does not change.** Stage 2d adds `filter`, `extracted` and `match`; choosing which postings get documents is step 3a (`/approve-jobs`, run by `/write-cv`). `jobfinder score list` gives the shortlist: postings at or above the threshold, best first, each with the `warn` reasons of its safety check.
- **Each posting once.** A posting is filtered, extracted and scored once. After the preferences change, `jobfinder score filter --all` filters every posting again; postings already scored keep their score.
- **Skill gaps.** `/skill-gaps` (`jobfinder score gaps`) adds up, in code, the required and preferred skills of the postings of the last 90 days that passed the filter and keeps the ones the profile lacks, with the same matching as the CV check (aliases, generic skills, the technologies of every job and project). A posting counts once per skill, 2 when required and 1 when preferred, times its score / 100 (0.5 unscored), so the skills of the postings that fit best come first. No agent and no extra tokens: it reads what `job-extractor` already stored. It changes nothing. `/add-skills` asks which of the top 16 the candidate really has, and at which level, and adds those to the profile: a true skill missing from the profile is otherwise dropped from every CV by the stage 3b check. It adds by the report's key (`jobfinder profile add-skill`), so the name, posting text, is written by code and never passes through a shell. Taken from ai-job-search's `upskill`, without its LLM step, and its rule of saving true facts to the profile.
- **English data.** Extracted facts and the summary, strengths and gaps are stored in English, like the profile; commands translate them for the user.

## Stage 3b decisions: CV and cover letter

- **One command, no review.** `/write-cv` runs the stage: it asks for how many of the scored postings without a CV (at or above the shortlist threshold, best score first) documents should be written: the first 10 (the default when there are more than 10, as many as `/apply` sends a day by default: a CV that is never sent is spent tokens), all, the first 20, or a custom number or choice; ids, `all` or a number as arguments skip the question. A tailored CV and cover letter are then written for each, without asking anything more. The candidate is never asked to read or confirm a document; the checks below take the place of a review. It runs two narrow commands with the Skill tool: `/approve-jobs` (step 3a; `jobfinder jobs approve` moves a posting from `new` or `shortlist` to `approved`, never one the safety check blocked) and `/execute-cv` (asks nothing; every approved posting, or one given posting, also one that is already `cv_ready`, to write it again). `/setup-cv` saves the writer model (Sonnet by default: code checks every number, skill and link against the profile, so the model mostly sets the cost; Opus writes more carefully, mostly in the cover letter) in `data/cv/settings.json`. Status moves to `cv_ready`; stage 4 picks the postings up from there.
- **The agent tailors, code checks and writes the facts.** The `cv-writer` agent (Read only, like the stage 2d agents) reads one input file written by code: the posting (with what stage 2d extracted and matched) and the profile **without contact details**, with every experience, achievement and project numbered. It returns a CV tailored to the posting: the summary, the skills in labelled groups, for each job the bullets most relevant first (each one a rewrite of one achievement, by reference, in the posting's terms, with an action verb and the result), the projects that fit, and the cover letter's body. Code writes names, contact details, titles, companies, places, dates, education and certifications from `profile.json`.
- **Checked against the profile in code** (`jobfinder cv save`):
  - A bullet may not add to its achievement: a rewrite with a number the achievement does not have, a skill or tool that neither the achievement nor that job's `technologies` name (from the profile and the posting's required and preferred skills), a link, an email address or hidden characters, or over 220 characters, is replaced by the achievement word for word and counted.
  - A skill the profile does not list is dropped; a group label that is not plain words becomes "Skills". A reference that does not exist is dropped; a job without bullets gets all of its achievements.
  - The summary and the cover letter are rejected if they hold a number that is not in the profile, a skill the posting asks for that the profile does not have, a link, an email address or hidden characters, or are too long. The command asks the agent once more with the error codes; a second failure leaves the posting `approved` for the next run.
  - What code cannot check is a claim in other words ("led a team" when the achievement says "built"); the agent's rules forbid it, and the checks keep every number and skill name honest.
- **Review, no research** (on by default; `/setup-cv` turns it off). The `cv-reviewer` agent (Sonnet, Read only) reads the posting and the saved answer as a hiring manager and returns the whole answer revised: the strongest true matches first, a bullet that uses the posting's term for similar but not the same work brought back to what the achievement says (the "would it survive an interview question" test), true skills the posting asks for named. Its answer has the writer's shape, so `cv save --review` runs every check again and renders it; a revision that breaks a rule gets no second try and the first documents stay. It reads nothing on the web (no company research) and costs about one more Sonnet run per posting (the reason `/setup-cv` can turn it off), which `/cost-report` counts with the writing. Taken from ai-job-search's `application-reviewer`, with its edits returned as a whole answer instead of text patches, so code checks them like the first version.
- **Contact details stay out of LLM prompts.** The agent never sees the name, email, phone or profile links, so an injection in posting text cannot reach them, and code rejects a link or an email address in what it writes.
- **PDF in the layout of Jake's Resume, rendered by Typst.** The libraries looked at: [RenderCV](https://pypi.org/project/rendercv/) (YAML to Typst CVs, but no cover letters and its own YAML schema), LaTeX with [Jake's Resume](https://github.com/jakegut/resume) itself (the reference look, but needs a TeX install and escaping of every string), WeasyPrint (HTML to PDF, needs Pango installed on the system), python-docx (DOCX; layout left to Word) and Typst templates such as [basic-resume](https://typst.app/universe/package/basic-resume). Chosen: the [`typst`](https://pypi.org/project/typst/) Python package (the compiler is in the wheel, nothing to install, a fraction of a second per document) with one template of our own, `src/jobfinder/cv/template.typ`, after Jake's layout: one column, the name centred in small caps over one contact line, small-caps headings over a rule, title and dates on one line with the company and place in italics below, small bullets, "Technical Skills" in labelled lines, the New Computer Modern font of the LaTeX original. The cover letter uses the same header. The data goes in as JSON (`sys.inputs`) and is placed as text, never parsed as markup, so no character in posting or agent text can change the layout. Paper is Letter for the US and Canada, A4 elsewhere.
- **ATS-friendly, and checked.** Real text with Unicode mapping, no ligatures or hyphenation, no tables, columns, images or icons, contact details in the body, standard headings (Summary, Experience, Projects, Technical Skills, Education, Certifications, Publications, Languages), dates as "Mar 2023 - Present" with an ASCII hyphen (some ATS imports, Workday among them, split a range only on "-" and drop a "2019 – 2023"; an education whose end year is still ahead says "(expected)"), the newest job first (education first under 2 years of experience). After rendering, code reads the text back out of both PDFs with `pypdf`, as an applicant tracking system would, and rejects the documents unless the name, the email, every heading, the summary, every bullet and every letter paragraph are found, and no year in the CV touches a non-ASCII dash.
- **One or two pages.** The CV takes one page when its content fits and at most two, so recent jobs keep their bullets; an applicant tracking system reads either. While it is longer than two, code drops the last bullet of the oldest job with more than two, then the last project, then down to one bullet per job (the agent orders bullets most relevant first), and reports how many it dropped.
- **One folder per posting,** `data/cv/<DD-MM-YYYY>/<company>-<source_id>/` under the day the documents were written (e.g. `data/cv/24-10-2026/acme-software-gmbh-4012345678/`: the company name and the source id in lowercase ASCII words, so posting text cannot reach outside the folder), with the agent's input and answer (untrusted) and the documents: the CV and cover letter named after the candidate (`Ayse-Yilmaz-CV.pdf`, `Ayse-Yilmaz-Cover-Letter.pdf`), the cover letter again as plain text (`Ayse-Yilmaz-Cover-Letter.txt`, to paste into a form), and the posting as a PDF (`Job-Description.pdf`: its fields, safety warnings and description as stored, so the candidate can read later what the job was; code only, no LLM, the text placed as text). The job records the paths in `documents` (relative to `data/`), which stage 4 reads.
- **English, unless the candidate asks otherwise** (decided, see the open questions). The profile is stored in English and every rewrite is checked against the English achievement (its numbers and skill names), so every CV and cover letter is in English, also for Turkish postings. Writing in the posting's language would mean translating achievements, which code cannot check for meaning.

## Stage 4 decisions: application

- **By hand or by the agent.** The candidate can apply by hand (below) or let the `job-sender` agent fill in the form in a Playwright browser window (`/apply`, see "By the agent"). On the hand path no command, agent or tool opens the apply link, fills in a form or sends an email.
- **Setup and execute.** `/write-cv` (stage 3) prepares every file for applying; `/execute-job-apply` asks nothing and shows `jobfinder apply list`, a table of every posting with status `cv_ready` (documents written, not applied to yet), best score first, with its documents folder, the apply link to click and the `warn` reasons of the safety check under it. No apply link means applying on the posting page (LinkedIn Easy Apply). A first design with a screen per application and form answers from the profile was dropped as too much.
- **Recorded after the candidate confirms.** `/mark-applied` runs `jobfinder apply mark`: code moves `cv_ready` to `applied` and writes `application.applied_at`, now or 00:00 UTC of the day the candidate gives (yesterday, or a date, when recorded late). Stage 5 bounds its Gmail search with it. Only `cv_ready` can be marked; a date in the future or before the posting was saved is refused.
- **No files of its own by hand.** The hand path writes only `application` on the posting; the agent path has `data/apply/`.

### By the agent

- **Playwright MCP, visible window.** The agent drives the Playwright MCP server of `.mcp.json` (version pinned), headed (no `--headless`, so the candidate can watch the application), with its own browser profile in `data/apply/browser-profile` (git-ignored like all of `data/`). It replaced Claude in Chrome, which drove the candidate's own Chrome: the page is read as a text snapshot with element references, a step's fields are filled in one call, and every answer is small, so an application costs fewer tokens and runs faster. Trade-offs: the profile is not the candidate's Chrome, so LinkedIn works only after a one-time sign-in there (`npx -y -p @playwright/mcp@0.0.83 playwright open --user-data-dir=data/apply/browser-profile <sign-in address>`), and the agent never waits for a CAPTCHA or a sign-in in the window (the run stops and the candidate applies by hand). Automation can still be detected, so sites may stop more often than a real Chrome did. Not a scraper and no cookie leaves the machine.
- **One agent, one posting at a time.** `/apply` lists the `cv_ready` postings, the candidate says once, up front, how many to apply to (the first 10 by default, best score first) and confirms the list, and for each the `job-sender` agent (Sonnet by default: an application is about 40 model requests, so the model sets most of its cost, and the form rules and the hook leave little for Opus to add; `/setup` can pick Opus as `sender_model`) opens the posting and uses LinkedIn Easy Apply when there is one, else follows the Apply button to the company's page or ATS. In Easy Apply it never picks a resume LinkedIn already has: it uploads the posting's CV ("Upload resume"), waits 5 seconds, checks that the new file is selected and goes on. Visa, work authorization and sponsorship questions are answered from the profile's `work_authorization` for the country in the question or the posting's location; an unclear country, or a fact the profile does not state, is asked.
- **Only `pass` postings.** A posting with a safety `warn` (unknown apply domain, shortener, chat contact) is applied to by hand. The documents must be on disk and the start link https. At most `max_per_day` (10) applications a day.
- **One approval up front, or one per application** (`/setup` asks; saved as `confirm_before_submit` in `data/apply/settings.json`). The agent always stops before the submit button and returns every field it filled and every file it attached, and the command always shows it. With `false` (recommended in `/setup`, the five-stage flow) the up-front confirmation of the list is the approval and the command lets the agent submit straight away; with `true` (the default when nothing is saved, the first design) it submits only on "Gönder". Required fields the profile does not answer are asked either way.
- **Unknown required fields are asked in the terminal.** A subagent cannot ask the candidate, so the agent stops with the questions; the command asks them (AskUserQuestion), code stores the answers and the command continues the same agent with SendMessage, so the tab and the form stay open. Answers go to `data/apply/answers.json`, not `profile.json`: form questions are many and specific, and the profile schema stays the profile; the candidate can keep an answer for that application only. The profile answers what it states (contact, work authorization, notice period, salary, languages). An optional field the input does not answer is left empty.
- **Code on both sides of the agent,** as in stages 2d, 3b and 5. `jobfinder apply batch` writes its input (start link, allowed domains, documents, the profile with contact details, saved answers) and the run; `jobfinder apply step` checks each answer against a schema and the page's domain, cleans what is shown to the candidate and says what the command does next; `jobfinder apply confirm` records the approval; on `submitted` code sets `applied` with `application.via`.
- **A hook on every browser call** (`python -m jobfinder.send`, only for `job-sender`), as the prompt injection check before the agent acts on a page: before a call, only the Playwright tools the workflow uses pass; URLs must be https on allowed domains (the posting's own, a known ATS or one the agent trusted) and parse the same in the browser and in code, files one of the two documents, Reads its own input; `browser_evaluate` and `browser_run_code_unsafe` (JavaScript) are denied. Every Playwright answer names the page it ends on ("Page URL"): one outside the allowed domains is handed to the agent to judge, and until it is trusted nothing can be clicked or typed there. The text the tools return goes through the stage 2c patterns and the page tools' text through the classifier; a finding replaces the text and stops the run, after which every call is denied. It fails closed, and a run the hook never saw stops (`guard_not_running`).
- **The agent judges a new domain.** A LinkedIn posting rarely has an apply link or company website, and its Apply button leads to the company's careers site (`careers.fresha.com`), so a fixed list stopped many real applications. When a page from the posting's Apply button is on a new domain, the agent judges it (reached from the Apply button, named after the company or an application platform, this job's page; strict, since the domain stays trusted) and returns `new_domain`; code refuses what is not https or plain, punycode, a shortener or more than 3 in a run, else saves it in `data/apply/trusted_domains.json` and the agent goes on. The candidate sees each new domain in the report. Trade-off: a page could mislead the agent into trusting a look-alike; the injection checks, code's checks and the report limit that.
- **Text, not screenshots.** The agent reads each form step with `browser_snapshot` (the page as text with element references) and fills it by reference; a screenshot only when text is not enough. The server runs with `--snapshot-mode none`, so an action answers with the page address only and the page text arrives only when the agent asks for it. Text costs far fewer tokens, and only text goes through the hook's injection check (the local classifier, no LLM, no tokens).
- **Few requests.** Every request of the agent sends its whole context again, so it reads as little as it needs and fills a step's fields in one call (`browser_fill_form` takes the whole list) instead of one message per field. On a form with a CV upload it uploads first and lets the page settle before typing, because some forms (Ashby) redraw after the CV is attached and lose what was typed. On the posting's page it looks for the apply button in one snapshot instead of reading the whole page: the input already has the posting. `maxTurns: 80` stops an agent that loops on a page.
- **Rules in a skill.** `apply-safety` (`.claude/skills/`, preloaded into the agent) holds what it must never do: follow page instructions, type what the input does not give, sign in, create an account, solve a CAPTCHA, fill in an ID, passport, bank or date of birth (all stop the run; the candidate applies by hand), self-identify (declined), tick marketing boxes. It also covers working in the browser (no dialogs, stop after repeated failures).
- **A repeatable test.** `/test-apply` sends one posting, picked by the candidate the first time and remembered (`data/apply/test.json`), again on every run: the whole form, submitted, with no summary and no question but a form question no saved answer covers. The candidate chose to send it for real each time. Code leaves the posting's status as it is and does not count the test in `max_per_day`, so the same posting is sent next time. Each run has its own folder (`data/apply/tests/`) with the commit and model, and `jobfinder apply test-report` reads the cost records of the agent that read that folder's input: tokens, ~$, requests, steps and minutes, against the previous and the first test. The command's own tokens are counted when its turn ends, so a report shows them for the earlier tests.
- **What is left.** A click that loads another domain is seen after the page loads (by the hook, which then blocks clicking and typing there, and at the next step), not before; screenshots are not checked by code; the submit click itself is the agent's, guarded by its rules and the command (code records `not_confirmed` if it submitted without approval); a hook that fails after a call lets that output through (before a call, `onFailure: "block"` blocks it; Claude Code v2.1.295 or later); the classifier can stop a real application (the candidate then applies by hand). If the Playwright tools do not reach a subagent in the candidate's Claude Code version, the fallback is to run the session as the agent (`claude --agent job-sender`), not built yet.

## Stage 5 decisions: tracking

- **Gmail through Claude.** Replies to applications are read by Claude with the claude.ai Gmail connector (tools `mcp__claude_ai_Gmail__*` in a local Claude Code session, `mcp__Gmail__*` in a claude.ai cloud session; both are allowed and denied the same way; on the user's own Google login), not by a Python mail client. No mail password or token goes into `.env`.
- **Automatic status update.** Each new email that answers an application is matched to its posting and classified; the posting's status in `data/jobs/jobs.json` is updated without asking, and the user is told what changed.
- **Execute only.** `/track` asks nothing; there is no setup half, because nothing has to be chosen. It runs by hand.
- **Which postings.** Every posting the candidate may have applied to and that is not rejected: status `shortlist`, `approved`, `cv_ready`, `applied` or `response`, or `new` with a score at or above the shortlist threshold (`min_score`); never a `block` posting; until a rejection arrives or the application is closed. The candidate does not have to run `/mark-applied`: the mail shows what they applied to. A posting not marked applied is looked up only for 60 days after it was saved, so the search does not grow forever.
- **Closing quiet applications.** An application still `applied` with no mail for 60 days or more is stale. `/track` only counts them and points to `/close-stale`, which asks (all, some or none) and moves the chosen ones to `no_response` (`application.closed_at`); they are no longer looked up. An application with an answer (`response`) is never closed. Taken from ai-job-search's `/outcome stale` (60 days there too).
- **People to reach.** From scoring on, `jobs list` and `score list` give two LinkedIn people-search links per posting, the company's recruiters and people in the same role, which `/show-list` and the shortlist show as "Kişi ara". They are links for the candidate to open: no tool opens, reads or scrapes them, and the company and title from the posting go in only as URL-encoded search text.
- **Applied from the mail.** A posting not marked yet that gets any mail about an application (a LinkedIn "application sent", an ATS confirmation, or straight away a reply) becomes `applied`, with `application.applied_at` = 00:00 UTC of the day of the first such mail. The agent is told to leave out job alerts and recommendations that only name the company or the role.
- **Email text is untrusted,** like posting text. The `mail-reader` agent (Haiku) has Read and three Gmail tools, all read-only: search threads, get a thread, get a message. It cannot send, reply, forward, draft, label, archive or delete mail, and cannot apply. Every Gmail tool that writes is also denied in `.claude/settings.json`, so no session in this repository can use it. The orchestrating command never calls Gmail itself.
- **Search by company, not the whole mailbox.** Reading every mail again on each run would cost too many tokens, so code builds one Gmail query per batch of 10 postings from their company names, leaving out LinkedIn's job alert senders (their mails name many companies and answer nothing), and only the threads it finds are read, all threads of a result page in one message.
- **Code on both sides of the agent,** as in stage 2d. `jobfinder track batch` writes the postings to look up (id, company, title, apply day, apply domain; no posting text beyond that) and the Gmail search query, built by code: mail since the last check (or the apply day, or the day the posting was saved) minus two days of slack, not from the user, naming one of the companies (search operators stripped, legal forms dropped). The command copies the agent's answer unchanged; `jobfinder track save` keeps only replies to postings of that batch, drops a message already stored, checks every value against the job schema and the day against the search window, and only then writes the status.
- **Six kinds of reply:** `received` (the application arrived), `assessment` (test or take-home), `interview`, `offer`, `rejection`, `other`. The four that answer the application set `tracking.outcome` (the latest one wins) and move the posting from `applied` to `response`; `received` and `other` are stored without changing anything.
- **Mail text is never stored.** A reply is its Gmail message id, the day, the kind and a note of at most 160 characters in English, written by the agent without names, addresses or links.
- **A broken answer changes nothing.** The batch's postings keep their last check time and are looked up again next run; overlapping searches are safe because a message is stored once.

## Cost tracking decisions

- **From the transcripts, by a hook.** Claude Code writes every response's token usage to its transcripts; `SubagentStop` and `Stop` hooks (`python -m jobfinder.cost`) sum them into `data/cost/usage.json`. Rejected: `/cost` (current session only, not per posting) and OpenTelemetry (needs a collector, not per posting).
- **Per posting where the work is per posting.** `cv-writer` and `job-sender` are linked to their posting through the input file their prompt names; scoring and tracking agents per batch; the main session per command.
- **`usage.json`, not a log.** An agent continued with SendMessage stops again, so its entry is rewritten, not appended.
- **Dollars as an estimate.** The plan is not billed per token; `config/pricing.json` holds Claude API prices so steps can be compared.

## Open questions

Decided when their turn comes:

- [x] Technology: **Claude Code** as the main interface (slash commands + agents), deterministic tools in **Python** (3.12+, `uv`).
- [x] Database: **one JSON file** (`data/jobs/jobs.json`) for now; SQLite when it gets slow (stage 2b).
- [x] First source: **LinkedIn**, through Apify `curious_coder/linkedin-jobs-scraper`. Apify first; more actors can be added to `config/apify_actors.json`, and freehire.me is a second, free source.
- [x] LLM steps of stage 2d: **Claude Code agents** on the user's Claude plan, not the Anthropic API.
- [x] Tracking source (stage 5): **Gmail, read by Claude** through a Gmail tool; the status in `jobs.json` is updated automatically.
- [x] CV and system language: **English** unless the user says otherwise. The CV and cover letter (stage 3b) are in English, also for Turkish postings, and so are the stored data, docs and prompts; the candidate is still talked to in their own language.
- [ ] Target market.
