# /create-profile - Build the candidate profile from a CV and other documents

You are running stage 1a of the job finder: build `data/profile/profile.json` from the candidate's CV (and the other documents they put in `data/profile/sources/`: a LinkedIn data export, certificates, a portfolio or project write-up), complete it by asking the candidate, and render `data/profile/profile.md`. The schema is `schemas/profile.schema.json`; its `x-level` field decides what must be asked (see `docs/plan.md`, "Profile schema decisions").

Talk to the candidate in the language they use (Turkish by default). Store every profile value in English: translate their answers.

Never invent anything. Every value in the profile comes from the CV, a document in `data/profile/sources/` or the candidate's own answer in this conversation.

All profile edits go through the deterministic tools below, never by hand-editing the JSON (except the initial write in Step 2). Each prints JSON:

```bash
uv run jobfinder profile validate             # {"valid", "errors"}
uv run jobfinder profile missing              # {"pending_confirmations", "missing", "complete"}
uv run jobfinder profile set '<pointer>' '<json>'   # e.g. set '/identity/phone' '"+90 555 ..."'
uv run jobfinder profile render               # writes data/profile/profile.md
```

`set` takes a JSON value: strings in double quotes, `null` for a skipped field, whole lists or objects allowed. `/-` appends to a list (`set '/experience/-' '{...}'`). Quote both arguments in single quotes.

## Asking questions

Follow the general "Asking questions" rule in `CLAUDE.md` (AskUserQuestion for every choice). Command-specific details:

- Confirmations in Step 3: one question per pending item, with options "Correct" and "Fix" (the candidate types the correction through "Other"). The tool takes at most 4 questions per call, so send batches of 4.
- Enum fields in Step 4 (`work_mode`, `seniority`, `employment_types`, `work_modes`, booleans such as `needs_sponsorship_elsewhere`): offer the allowed values as options. Use `multiSelect: true` for list fields (`seniority`, `employment_types`).
- AskUserQuestion allows at most 4 options. For enums with more values (`seniority` has 6, `employment_types` 5, language levels 7), offer the 3-4 most likely values for this candidate (from the CV) as options and name the rest in the question text; the candidate picks one of those through "Other".
- Recommended fields: add a "Skip" option so the candidate can skip.

Use plain chat text only for free-form answers (name, phone, city, salary amount, the achievement follow-ups in Step 5).

Follow these steps **in order**.

---

## Step 0: Find the CV and check for an existing profile

`$ARGUMENTS` may name a CV path. Otherwise use Glob on `data/profile/*.pdf` (and `data/*.pdf`, where older setups kept it; the Python tools move it into `data/profile/` on their next run). If there are several, ask which one; if there is none, tell the candidate to put their CV in `data/profile/` (everything under `data/` is git-ignored) and stop.

Then use Glob on `data/profile/sources/**` for other documents (PDF, CSV, TXT, MD; a LinkedIn data export is a folder of CSV files such as `Profile.csv`, `Positions.csv`, `Education.csv`, `Skills.csv`, `Certifications.csv`, `Projects.csv`). They are optional: name the ones found, or, when there are none, say in one sentence that a LinkedIn export ("Get a copy of your data" in LinkedIn's settings), certificates or project write-ups put there make the profile richer, and that more true facts in the profile mean fewer facts the CV check (stage 3b) has to drop. Do not wait for them.

If `data/profile/profile.json` already exists, ask before doing anything:

> **A)** Continue the existing profile (skip to Step 3) · **B)** Re-extract from the CV and the documents and replace it · **C)** Add what the documents in `data/profile/sources/` say to the existing profile (Step 1b)

Only B overwrites the file. Offer C only when `data/profile/sources/` has documents.

## Step 1: Extract

Dispatch the **`profile-extractor`** agent (Agent tool, `subagent_type: profile-extractor`) with the CV path and the paths of the documents found in `data/profile/sources/` (`Mode: draft`). It reads them and returns one JSON block; where the documents disagree (dates, titles, companies), it uses the CV's value and adds a pending confirmation naming both. Do not read the CV or the documents yourself; the agent does.

If the reply has no JSON block, or it does not parse, dispatch once more with the parse error; if it fails again, stop and report.

## Step 1b: Add from other documents (option C only)

Dispatch **`profile-extractor`** with `Mode: enrich`, the path `data/profile/profile.json` and the paths of the documents in `data/profile/sources/`. It returns `additions` (facts the documents give that the profile lacks) and `conflicts` (the documents disagree with the profile), each with a JSON Pointer.

- **Additions:** ask in batches of up to four with AskUserQuestion, one question per item, showing *field - value - document*, options "Ekle" and "Ekleme". Apply the added ones with `set` (a pointer ending in `/-` appends) and check `errors_at_pointer`.
- **Conflicts:** one question per item, *field - profile value - document value*, options "Profildeki doğru" and "Belgedeki doğru"; set the document's value only when the candidate picks it.

If there is nothing to add and no conflict, say so. Then run `validate` and continue with Step 3 (the profile may have new pending items) through Step 6.

## Step 2: Save the draft

Move `pending_confirmations` out of the draft and write `data/profile/profile.json` with the Write tool as:

```json
{
  "meta": {
    "schema_version": "1.0",
    "updated_at": "<today, YYYY-MM-DD>",
    "source_cv": "<CV file name>",
    "pending_confirmations": [ ...from the draft... ]
  },
  ...the rest of the draft...
}
```

Run `validate` and tell the candidate in two or three sentences what was found (and from which documents): roles, years, sections filled, how many items need confirming, how many fields are missing. Pass on any notes the agent returned about CV content it could not place.

## Step 3: Confirm uncertain fields

Run `missing`. Take the `pending_confirmations` in batches of up to four and ask them with AskUserQuestion (see *Asking questions*), one question per item, each showing *field - value - why it is uncertain*. Apply each correction with `set`. When the batch is done, clear it by setting `/meta/pending_confirmations` to the entries still unanswered (`[]` when none are left).

## Step 4: Fill missing fields

Run `missing` again and ask for the `missing` entries **one topic per message**, in this order: identity and contact, work authorization, experience details, education, skills and languages, then preferences. Group the fields of one topic in a single message and show the allowed values from `expects` in plain words (for example the seniority options), never the raw schema.

- **required:** must be answered. Re-ask if the answer is empty.
- **recommended:** ask, and say it can be skipped (a "Skip" option). A skipped field is set to `null`, which `missing` treats as answered.
- **Dealbreakers:** ask for industries, companies and keywords to avoid. Lists may be empty, but then the candidate must say so explicitly; only after that set `/preferences/dealbreakers/confirmed` to `true`.
- **Office days:** ask how many days a week the candidate can work at the office, 0 to 5 (0 = remote only), and say why: at `/apply`, a form asking for that many days or fewer is answered yes without asking again.
- **Minimum salary:** a soft preference that affects the score, not a hard filter. Say so when asking.

Apply each answer with `set` and check the tool's `errors_at_pointer`; if it reports an error, fix the value (format, enum) and set it again before moving on.

## Step 5: Strengthen weak achievements

Read the `achievements` of each experience entry. For bullets that are vague or have no outcome ("worked on backend services", "responsible for testing"), ask the candidate one short follow-up per role: what changed because of their work, with a number if they know one (latency, users, revenue, time saved, team size). Rewrite a bullet only with what the candidate tells you; if they have nothing to add, keep it as it is. At most one question round per role.

## Step 6: Finish

Repeat Steps 3-4 until `missing` reports `"complete": true`. Then run `render` and give the candidate a short summary: target roles and seniority, locations and work modes, dealbreakers, and anything they skipped. Remind them that nothing under `data/` is committed to git, that `profile.md` deliberately contains no contact details, and that they can re-run `/create-profile` (option A) at any time to change answers.
