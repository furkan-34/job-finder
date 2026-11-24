# /add-skills - Add the skills the postings ask for that the candidate really has

You are adding to the profile the skills that the postings ask for, the profile does not list, and the candidate says they have (see `docs/architecture.md`, "Skill gaps"). The CV check (stage 3b) drops every skill the profile does not have, so a true skill missing from the profile is lost from every CV; this command closes that gap. **It adds only what the candidate picks**; it never adds a skill on its own.

Talk to the candidate in the language they use (Turkish by default). Run only `uv run jobfinder ...` with Bash. Do not open `data/jobs/jobs.json` or edit `data/profile/profile.json` by hand. Ask every choice with AskUserQuestion (the "Asking questions" rule in `CLAUDE.md`).

```bash
uv run jobfinder score gaps --top 16 [--days N]
uv run jobfinder profile add-skill <key> <expert|advanced|intermediate> [--days N]
uv run jobfinder profile render
```

`$ARGUMENTS` may give `--days` (default 90); pass the same value to both tools.

## Step 1: The missing skills

Run `score gaps --top 16`. If it fails because the profile is missing or invalid, point to `/create-profile`; stop. If `skills` is empty, say that the profile already covers every skill the postings ask for; stop.

## Step 2: Which ones the candidate has

Ask with AskUserQuestion, `multiSelect: true`, up to four questions of up to four skills each, in the report's order (heaviest first): "Bu yeteneklerden hangileri sende gerçekten var?". Each option's label is the `skill`, its description "N ilan istiyor" (`postings`). Say that a skill they only know a little, or do not have, should be left unpicked, since the CV will state it as theirs. If they pick nothing, say that nothing changed; stop.

A skill the candidate types through "Other" is not in the report: tell them to add it with `/create-profile` (option A), because only skills from the report are added here.

## Step 3: Levels

For the picked skills, ask the level with AskUserQuestion, up to four questions per call, options "Expert", "Advanced", "Intermediate" (the profile's levels; nothing lower is listed in a CV).

## Step 4: Add

For each picked skill run `profile add-skill <key> <level>` with the `key` from the report, never the skill's name: the name is posting text and code writes it. If a call returns `ok: false`, name the skill and go on. Then run `profile render`.

Tell the candidate in one or two sentences which skills were added at which level, that the next CVs (`/write-cv`) can use them, and that `/create-profile` (option A) changes or removes them.
