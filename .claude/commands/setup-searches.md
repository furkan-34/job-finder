# /setup-searches - Choose the search queries and how many postings each one fetches

You are running one narrow step of stage 2a: build the list of LinkedIn search queries from the candidate's profile, let the candidate pick which ones to keep and how many postings each one may return, and save the result to `data/searches/searches.json`. **That is all this command does.** It does not fetch postings, does not schedule anything and does not change the profile.

Talk to the candidate in the language they use (Turkish by default). Follow the "Asking questions" rule in `CLAUDE.md`.

`$ARGUMENTS` may carry `data_sources=all|NAME,NAME` from `/setup-job-search`. Use them only for the cost estimate; without `data_sources`, estimate for all actors.

Tools (each prints JSON):

```bash
uv run jobfinder profile validate
uv run jobfinder search validate [--data-source NAME ...]   # {"valid", "errors", "searches", "data_sources", "max_postings_per_run", "max_cost_per_run_usd"}
```

Follow these steps **in order**.

---

## Step 0: Profile

Run `uv run jobfinder profile validate`. If the profile is missing or invalid, tell the candidate to run `/create-profile` first and stop. Then read `data/profile/profile.md`.

## Step 1: Existing searches

Run `uv run jobfinder search validate`. If the file exists and is valid, show the saved searches as a short table (keywords, location, limit) and ask: **Keep as is** (stop here and say nothing changed) · **Change limits only** (go to Step 4 with the saved list) · **Build again** (continue).

## Step 2: Draft the queries (do not ask yet)

Build one query per target role and location in `preferences`, at most 8 in total:

- **keywords:** since August 2026 LinkedIn search is AI based, so seniority, work mode and employment type go into the keyword text. Write a short phrase a recruiter would use: seniority word(s) + role + work mode when the location allows only some modes, e.g. `Senior Backend Engineer remote`, `Platform Engineer hybrid`. Add `contract` or `part time` only when the candidate wants only that. Do not add technologies, company names or negative words (dealbreakers are filtered later, in stage 2d).
- **location:** a place LinkedIn resolves, written out in English: `Istanbul, Turkey`, `Germany`, `European Union`. For `anywhere` or a remote-only region use `Worldwide` or the region name.
- **id:** short, lowercase, words joined by `-`, unique, e.g. `senior-backend-istanbul`.

If the candidate has more combinations than 8, keep the ones closest to their first target role and first location, and say what you left out.

## Step 3: Pick the queries

Show the draft as a numbered table (keywords, location). Ask with AskUserQuestion, `multiSelect: true`, which queries to keep, at most 4 options per question (send several questions in one call when there are more than 4 drafts). The candidate can correct wording through "Other"; apply corrections exactly as written.

## Step 4: Postings per query

For every kept query ask how many postings it may return, one question per query (up to 4 per call), header = the query id, question text = the keywords and location. Options, in this order: `25` (default, recommended), `10`, `50`, `100`; any other number through "Other" (1 to 1000). The default is 25: use it for a query only when the candidate picks it, never without asking.

Then give the cost in one or two sentences: each query runs once per chosen actor, and each actor's price per 1,000 results is in `config/apify_actors.json` (the LinkedIn actor is about $1), so the worst case per run is the sum of the limits × the summed price per result. Compare it with the Apify free plan's $5 monthly credit: say how many runs a month that credit covers in the worst case, and if one run is over $5, say so and offer once to lower the limits.

## Step 5: Save

Write `data/searches/searches.json` with the Write tool:

```json
{
  "updated_at": "<today, YYYY-MM-DD>",
  "searches": [
    {"id": "senior-backend-istanbul", "keywords": "Senior Backend Engineer hybrid",
     "location": "Istanbul, Turkey", "limit": 25, "enabled": true}
  ]
}
```

Run `uv run jobfinder search validate` (with a `--data-source` option per chosen actor, if given). If it reports errors, fix them and save again. Finish with the table of saved searches and `max_postings_per_run` / `max_cost_per_run_usd`, and say the list can be changed any time by running `/setup-searches` again or by editing the file (`"enabled": false` pauses a query).
