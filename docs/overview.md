# job-finder: technical overview

The step-by-step guide for people who never touch the code is the [README](../README.md); this page is for developers and for the command-line way of working.

A personal, agent-based job search assistant built on Claude. It collects job
postings, screens them for safety and scores them against the candidate's
profile. For the best ones it prepares an ATS-friendly CV and cover letter from
the candidate's real data only, applies in a Playwright browser window and
follows the replies in Gmail.

Claude Code is the runtime: each workflow is a slash command, focused work runs
in restricted subagents, and deterministic work is done by small Python tools
that print JSON. It is built for one person (the repository owner), step by
step, with the simplest design that works.

## Documentation

| Document | What is in it |
|----------|---------------|
| [docs/plan.md](plan.md) | Stages, rules, decisions and open questions |
| [docs/architecture.md](architecture.md) | How it is built: commands, agents, Python tools, flows, security, extending |
| [docs/data.md](data.md) | Where data lives, file formats, the job schema, the git policy |
| [docs/commands.md](commands.md) | Every slash command and `jobfinder` tool |
| [CLAUDE.md](../CLAUDE.md) | Working rules for AI coding agents (Turkish) |

## Status

Five stages, one command each; all done.

| Stage | Command | What it does |
|-------|---------|--------------|
| 1. Setup | `/setup` | Every setup step in order: the profile from your CV (`/create-profile`), the Apify token and a test fetch (`/setup-sources`), the search queries (`/setup-job-search`), scoring (`/setup-job-scoring`), the CV writer (`/setup-cv`), how applications are approved, and checks of the safety model, Gmail and the browser |
| 2. Search | `/search` | Fetches postings, maps them to one schema and drops duplicates, runs the safety check (patterns + a local classifier model), then filters and scores them (rule filter in code, Haiku extraction, Sonnet/Opus scoring in read-only agents) and shows the shortlist |
| 3. CV and cover letter | `/write-cv` | For how many postings (all, first 10, first 20, custom), an agent tailors the CV and writes the cover letter; code checks them against the profile and renders ATS-friendly PDFs in the layout of Jake's Resume with Typst; no review step |
| 4. Applying | `/apply` | Confirm once how many postings (e.g. the first 10); an agent applies to each in a Playwright browser window (Playwright). By hand instead: `/execute-job-apply` lists the links, `/mark-applied` records it |
| 5. Tracking | `/track` | Claude reads mail about applications in Gmail (read-only); code marks applied and updates the status |

`/show-list` shows every posting and the stage it has reached, at any time.

## Key principles

- **Job text is untrusted input** (prompt injection risk). No LLM reads it before
  the safety check, which blocks postings that ask for fees or carry instructions
  for an AI (found by patterns and by a local classifier model). Only two agents
  read it (to extract facts, to score, and to tailor the CV), and they can only read:
  no writing, no commands, no email, no applying. Their answers are checked in code.
- **Mail is untrusted input too.** Replies to applications are read from Gmail
  through the claude.ai connector by one agent that can only search and read mail:
  sending, replying, forwarding, drafts, labels and deleting are denied in
  `.claude/settings.json`. Code checks its answer and writes the status; no mail
  text is stored.
- **No invented facts.** Generated documents use only data in `profile.json`: the
  agent picks achievements and skills by reference, code writes them word for word,
  and a number that is not in the profile rejects the summary or the cover letter.
- **You approve the applications.** By hand, nothing opens an apply link, fills in a form or sends email for you. With `/apply`, you confirm up front which postings (and, if you chose so in `/setup`, each summary before it is sent); one agent fills in the form in a Playwright browser window, only on the posting's own domains, and asks you what your profile does not answer; a hook checks each of its browser calls and stops it on prompt injection. Sign-in, CAPTCHA and fields like an ID or bank account stop it, and you apply by hand.
- **No user data in git.** Everything under `data/` is git-ignored; only the
  folder structure is in the repository. `profile.md`, used in scoring prompts,
  has no contact details, and neither has the CV agent's input: code adds them.
- **Secrets** (`APIFY_TOKEN`, optional `HF_TOKEN`) live in `.env`, which is git-ignored and denied to Claude Code.
- **Cost.** Scoring runs as Claude Code agents on your own Claude plan (no API key,
  no per-token cost): a rule filter in code first, then the small Haiku model, and
  Sonnet or Opus only for postings that are still in; a run reads at most a set
  number of postings. A fetch refuses to start beyond the Apify credit left.
- **No single-source dependency.** LinkedIn scraping is never given personal cookies.

## Installation

Prerequisites:

- [Claude Code](https://code.claude.com/docs) (`claude` on your PATH)
- [Node.js](https://nodejs.org/) (for `npx`), only for `/apply`: the agent that fills in applications drives a browser through the Playwright MCP server of `.mcp.json`, started with `npx` from a pinned version, so nothing is added to `package.json` or `uv`
- [uv](https://docs.astral.sh/uv/), which also installs Python 3.12 if it is missing:

  ```bash
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Windows (PowerShell)
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  # or: brew install uv
  ```

Then, once:

```bash
git clone https://github.com/furkan-34/job-finder.git
cd job-finder
uv sync          # creates .venv with Python 3.12 and the locked dependencies
uv run pytest    # optional: check the setup
cp .env.example .env   # then set APIFY_TOKEN
uv run jobfinder safety model   # downloads the local prompt injection model (~81 MB), once
npx -y @playwright/mcp@0.0.83 install-browser chromium   # the browser of /apply, once (its "run npm install first" warning does not apply)
```

Claude Code uses your own Claude login, so no Anthropic API key is needed. Job
sources need an [Apify](https://apify.com) token (free plan: $5 credit per month).
The free sources (freehire.me and the Greenhouse, Lever and Ashby company job boards) need
no token; the company boards read only the companies listed in `boards` of their entries in
`config/apify_actors.json`.

## Usage

```bash
cp ~/path/to/cv.pdf data/profile/
claude
```

Then, in Claude Code:

1. `/setup`: builds the profile from the CV and asks for anything missing, checks
   the Apify token (with a 5-posting LinkedIn test), then saves the search (date
   filter, actors, queries and postings per query, 25 by default), the scoring
   (Sonnet or Opus, shortlist threshold 70 and size 10, 50 postings scored per
   run), the CV writer (Sonnet or Opus) and how applications are approved (once
   up front, or each one; at most 10 a day). It also checks the local safety model,
   the Gmail connector and the Playwright browser. Run it again to change any part.
2. `/search`: searches with the saved settings, adds the new postings, runs the
   safety check, filters them by your preferences, reads out their facts, scores
   them from 0 to 100 against your profile and shows the shortlist with the safety
   warnings of each posting. Postings not scored yet from earlier runs are scored too.
3. `/write-cv`: choose for how many scored postings without a CV you want to
   apply, best score first: all (the default), the first 10, the first 20 or a
   custom number. For each one a CV tailored to the posting and a cover letter are
   written in English, as PDF in the layout of Jake's Resume, in
   `data/cv/<DD-MM-YYYY>/<company>-<id>/`. Code checks every number and skill
   against your profile, keeps the CV to one or two pages and reads the text back
   as an applicant tracking system would, so there is nothing to review.
4. `/apply`: the agent works in a Playwright browser window with its own profile,
   not your Chrome. When Claude Code first asks, approve the `playwright` server of
   `.mcp.json`. For LinkedIn, sign in once in that profile with
   `npx -y -p @playwright/mcp@0.0.83 playwright open --user-data-dir=data/apply/browser-profile https://www.linkedin.com/login`
   (close the window afterwards). Both `npx` commands name the pinned version on purpose:
   a newer Playwright would look for a different browser build. Say how many of the postings with
   documents to apply to (the first 10 by default) and confirm the list once; an
   agent then fills in each application, one after another: LinkedIn
   Easy Apply or the company's apply page. It asks you the required questions your
   profile does not answer (saved in `data/apply/answers.json` for next time) and
   shows what it filled in; the posting is then marked applied. Postings with a
   safety warning, or that the agent stops on, are applied to by hand:
   `/execute-job-apply` lists them with their links and `/mark-applied` records it.
   `/test-apply` sends one posting you pick once again and again, to compare the
   agent's tokens, cost and time after a change.
5. `/track`: looks in Gmail, company by company, for mail about the postings you
   may have applied to: a LinkedIn "application sent" or another confirmation
   marks the posting applied (no `/mark-applied` needed), and replies are stored as
   test, interview, offer, rejection or other, moving the posting to `response`
   when the company answers. Needs the Gmail connector: claude.ai → Settings →
   Connectors → Gmail.

`/show-list` shows, at any time, how many postings are at each stage and a table of
those in progress: score, company, title, applied on, reply and link.

New postings are added to `data/jobs/jobs.json`; a posting already there, or a
repost of it under a new id (same company, title and city), is skipped. Last, each
new posting gets a safety check in code and a small local classifier model (not an
LLM, free, offline): `block` (asks for a fee, prompt injection, hidden text), `warn`
(apply link to an unknown domain, link shortener, chat contact, text addressed to
an AI) or `pass`. Without the model (`jobfinder safety model`) only the code
patterns run.

Scoring never reads a `block` posting. Code first drops postings that hit a
dealbreaker or a wrong employment type, seniority or work mode; the `job-extractor`
agent (Haiku) reads out the facts of the rest (industry, required skills and
languages, salary, ...) and code filters again; the `job-matcher` agent scores what
is left against `profile.md` with a fixed rubric. Run `/setup` again (or one setup command) to
change answers. Details:
[docs/commands.md](commands.md).

### Web UI

```bash
uv run jobfinder ui
```

Opens a local page (127.0.0.1 only): **Jobs** lists every posting with its stage, score and
dates, with tabs, filters, sorting and a side panel per posting (approve for the CV stage, mark
as applied). **Setup** has a tab for each stage's settings. The buttons that need Claude
(find jobs, start scoring, generate CVs, batch apply, check replies, setup) open a terminal tab in
the page that runs the command in a real Claude Code session, so you can watch it and answer its
questions there; each button gets its own tab and they run side by side. The terminal panel needs macOS or Linux and the `claude` command.
