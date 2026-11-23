---
name: leftover
description: Spend leftover weekly Claude quota on dormant side projects. Scans every subfolder of an ideas folder, shows a ranked menu of tasks sized to the remaining weekly budget across all the user's accounts, asks every question up front, then hands the picked tasks to an unattended runner and later reports results and remaining questions. Use when the user types /leftover, or says "use my leftover limit", "spend the rest of my quota", "weekend run", "work on my ideas while I'm away".
---

# /leftover

Spends subscription quota that would otherwise expire on the projects in the current folder
(the "space"; each subfolder is a project). You do the judgment: scan, rank, ask. `leftover.py`
(next to this file) does the unattended loop: per-account quota checks, one task at a time in a
git worktree, stop at each account's keep line, `REPORT.md`. Read its docstring once.

Args: none = plan · `status` · `stop`.

## Files

- `~/.leftover/config.json`: accounts and `task_hours` (format in the leftover.py docstring).
- `~/.leftover/ledger.jsonl`: one line per task attempt with `account`, `model`, `size`, `pct`
  (weekly % it cost). Use it for estimates.
- `<space>/.leftover/run.json`: the queue you write; the runner adds status and results.
- `<space>/.leftover/REPORT.md`, `runner.log`, `logs/<task>.jsonl`, `STOP`.

## 0. Last run first

If `REPORT.md` exists: summarize it in a few lines, list branches waiting for review, then ask
its "Questions for you" (AskUserQuestion, batches of 4, recommended option first). Append each
answer to `<project>/LEFTOVER.md` under `## Answers` so future runs see it. Archive `run.json`
and `REPORT.md` to `.leftover/runs/<date>-run.json` / `-REPORT.md`. If `run.json` still has
`pending` tasks and the runner is not running (`runner.log` idle for 15+ min), offer to relaunch.

## 1. Budget

Run `python "<skill dir>/leftover.py" budget`. No `~/.leftover/config.json` yet: ask (one
AskUserQuestion) how much weekly % to keep for workdays before each reset (0 / 5 / 10 / 15) and
whether they have other subscription accounts. Write the config. Each extra Claude account needs
its own config dir, logged in once by the user in their own terminal:
`$env:CLAUDE_CONFIG_DIR="$HOME\.claude-<name>"; claude` then `/login` (bash: `CLAUDE_CONFIG_DIR=~/.claude-<name> claude`).
Never set `config_dir` for the default `~/.claude` login.

Estimates in weekly %: median `pct` from the ledger for the same account, model and size when
there are 2+ rows; otherwise these guesses for a Max 20x plan (x4 on Max 5x, x15 on Pro,
Team ~ Max 5x): Opus S 1 · M 3 · L 8; Sonnet S 0.3 · M 1 · L 2.5. Say they are guesses until
the ledger has data. Quota is reported in whole percents, so small tasks often log 0. Put the total in plain words: "about 5 medium Opus tasks".

## 2. Scan

Projects: every subfolder of the space except names starting with `.` or `_`, and any whose
`LEFTOVER.md` says `skip`. Per project read: `LEFTOVER.md` (the user's goals, off-limits areas,
past answers; obey it), `PROGRESS.md`, `IDEA.md`, `README.md`, `CLAUDE.md`/`AGENTS.md`,
`git log -10 --format="%ad %s" --date=short`, `git status --short`, the manifest's scripts, TODO
count. More than 5 projects: one Explore subagent per project, 15-line summary each.

## 3. Menu

Propose about 10 tasks, at least one per project when a sensible one exists. Good leftover
tasks: the project's own documented next step; make it run from a clean clone; fix listed bugs;
add the missing test or typecheck; a research spike ending in a written answer in the repo; an
idea turned into a one-page spec or thin prototype; publish-ready prep (README, demo script)
without publishing. Never: anything needing credentials, payments, new accounts, pushing,
deploying, publishing or contacting people.

Rank by `impact x autonomy / est_pct`. impact 3 = makes it usable, demoable or shippable;
2 = a clear step on a known path; 1 = hygiene. autonomy 1.0 = verifiable by tests, build or a
written deliverable with no taste calls; 0.6 = some judgment with safe defaults; 0.3 = mostly
taste. Model: Sonnet for mechanical work, Opus where reasoning or design matters.

Show one table `# | project | task | why now | size | est % | model | questions`, mark the top
set that fits the total spendable, and ask for numbers ("1 2 5 7").

## 4. Questions up front

For the picked tasks, ask every question whose answer changes the work (scope, naming, which
option, what to leave alone), plus "OK to git init <project>?" for non-git folders. AskUserQuestion
in batches of 4, recommended default first. Anything unanswered takes the default and the
runner's report logs it.

## 5. Launch

Write `<space>/.leftover/run.json` in run order:

```json
{"tasks": [{"id": 1, "project": "<subfolder>", "slug": "short-kebab-unique", "title": "...",
  "model": "sonnet", "size": "M", "git_init": false,
  "brief": "Goal, done-when criteria, the user's answers, files to start from."}]}
```

The runner appends the safety rules and report format to every brief; do not repeat them.
Start it detached so this session can close:
- Windows: `Start-Process -WindowStyle Minimized python -ArgumentList '"<skill dir>\leftover.py" run "<space>"'`
- macOS/Linux: `nohup python3 "<skill dir>/leftover.py" run "<space>" >/dev/null 2>&1 &`

Tell the user: it runs in its own minimized window; keep the machine on power with the lid
open (the runner blocks sleep, not lid close); a restart stops it and `/leftover` offers to
relaunch. Work lands on `leftover/<slug>` branches in `~/.leftover/wt`, never in their checkout.

## status / stop

`status`: `budget` output plus each task's status from `run.json` and the tail of `runner.log`.
`stop`: create `<space>/.leftover/STOP`; the runner ends the current task and writes the report.

Follow the user's global and project instruction files; where they say to ask first (trackers,
client-facing text), that becomes a question, never a guess.
