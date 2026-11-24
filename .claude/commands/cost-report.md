# /cost-report - Show how many tokens the LLM steps used

You are showing the token use of the LLM steps (every stage, see `docs/architecture.md`, "Cost tracking"): a hook records every agent run and every command of the main session in `data/cost/usage.json`, and the tool sums them. **That is all this command does.** It asks nothing and changes nothing. Claude Code's own `/cost` shows the current session only; this one shows every run, per posting.

Talk to the candidate in the language they use (Turkish by default). Run only `uv run jobfinder ...` with Bash. Do not open `data/jobs/jobs.json` or `data/cost/usage.json`.

```bash
uv run jobfinder cost report [--by job|agent|command|day] [--since YYYY-MM-DD] [--job <id> ...]
```

`$ARGUMENTS` may name the grouping (`job`, `agent`, `command`, `day`), a day to start from, or job ids; with nothing, group by job.

## Table

Run the tool. If `total.runs` is 0, say that nothing is recorded yet: the hook in `.claude/settings.json` records from the next agent run on (it needs `uv`); stop.

Show one table, a row per entry of `rows`, as they come (most expensive first), numbers with thousands separators:

- `by job`: title and company (the id when there is no title), CV tokens (`stages.cv.total_tokens`), application tokens (`stages.apply.total_tokens`), total tokens, `~$` (`cost_usd`). Only postings an agent worked on alone are here; scoring and tracking read batches of postings and show under `--by agent`.
- `by agent` / `by command` / `by day`: the key, runs, requests, input + cache writes, cache reads, output, total tokens, `~$`. `main` is the main session (the commands themselves), `(no command)` its messages outside a command.

Under it, one line with `total.total_tokens` and `total.cost_usd`, and that the dollars are an estimate at Claude API prices from `config/pricing.json`: on a Claude plan nothing is billed per token, and the plan's usage limit is not measured in dollars. If `unknown_models` is not empty, name them: their tokens are counted but not priced (add them to `config/pricing.json`).
