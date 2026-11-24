# /setup-cv - Set up how CVs and cover letters are written

You are running the setup half of stage 3b (CV and cover letter, see `docs/plan.md`): ask the candidate two choices and save them in `data/cv/settings.json`, so that `/execute-cv` can run without asking anything. **This command writes no documents.** It ends by pointing to `/execute-cv`.

Talk to the candidate in the language they use (Turkish by default). Follow the "Asking questions" rule in `CLAUDE.md`. Run only `uv run jobfinder ...` with Bash.

Tools (each prints JSON):

```bash
uv run jobfinder profile validate
uv run jobfinder cv settings
uv run jobfinder cv configure --writer-model sonnet|opus --review true|false
```

## Step 1: Preconditions and current state

Run `uv run jobfinder profile validate`. If the profile is missing or invalid, tell the candidate to run `/create-profile` first and stop.

Run `uv run jobfinder cv settings`. If the file exists (`"exists": true`), show the saved model and whether review is on in one line.

Say in two or three sentences what stage 3b does: for each approved posting the `cv-writer` agent tailors the CV to the posting (a summary, skills in groups, each achievement of the profile rewritten in the posting's terms, the projects that fit) and writes a cover letter; code checks every piece against `profile.json` (a rewrite may not add a number or a skill, the summary and letter may use only the profile's numbers and no links), adds the contact details and renders both as PDF in the layout of Jake's Resume, at most two pages and checked as an applicant tracking system reads it; the cover letter also goes into a .txt file, and the posting itself into Job-Description.pdf. There is no review step: `/write-cv` picks the postings and writes everything. The documents are in English. The agent runs on the candidate's own Claude plan: no API key and no extra cost, but it counts toward the plan's usage limits.

## Step 2: Ask

One AskUserQuestion call with two questions:

- Header "Model": "Which model should write the CV and cover letter?" · "Sonnet" (recommended; code checks every fact, and it uses about half of what Opus uses of the plan) · "Opus" (more careful writing, especially the cover letter).
- Header "İnceleme": "Should a second agent review each CV and letter as a hiring manager would?" · "Açık" (recommended; the `cv-reviewer` agent, Sonnet, reads each saved CV and letter, moves the strongest matches up, brings back wording that stretches what an achievement says, names true skills the posting asks for, and returns a revised version that code checks again; if the revision breaks a rule the first version stays. About one more Sonnet run per posting; `/cost-report` shows what it used) · "Kapalı" (only the writer runs; spends less of the plan).

## Step 3: Save

Run `uv run jobfinder cv configure --writer-model <model> --review <true|false>`. Report the saved settings in one sentence, then say: "Run `/write-cv` to choose postings and write their CV and cover letter." Change this later by running `/setup-cv` again.
