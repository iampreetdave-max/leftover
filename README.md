# leftover

Your Claude subscription resets every week whether you used it or not. Most weeks end with
quota left over, and most of us have a folder of side projects nobody has touched in months.
**leftover** puts the two together: it reads what is left of your weekly limit, looks through
your projects, and offers a ranked list of jobs that fit. You pick a few and answer its
questions in one go, then walk away. When you come back there is a report, a git branch per
job, and a short list of things only you can decide.

By [Preet Dave](https://iampreetdave.me). MIT licensed.

## How it works

1. **Budget.** Reads the 5-hour and weekly usage of every account you give it, straight from
   Claude Code, and subtracts the reserve you want to keep for workdays before the reset.
2. **Scan.** Every subfolder of the folder you run it in is a project. It reads each one's
   README, progress notes, git history and TODOs.
3. **Menu.** About ten jobs, ranked by impact x how well it can be done alone / cost in weekly
   %, with the model to use and how many questions each needs. You reply with numbers.
4. **Questions up front.** Everything the picked jobs need from you, asked at once, each with a
   recommended default. Anything you skip takes the default and is logged.
5. **Unattended run.** A small Python runner takes over, with no AI in charge of the loop. Before
   each job it checks every account's quota, picks the one whose weekly quota resets first (it
   is lost soonest), and runs the job headless in a git worktree. It waits out 5-hour limits,
   retries, and stops at your reserve line.
6. **Report.** `REPORT.md` lists what was done, the branch and the commands to check each job,
   the decisions it made for you, and your open questions. The next `/leftover` asks them and
   remembers the answers for future runs.

## Install

```
claude plugin marketplace add iampreetdave-max/leftover
claude plugin install leftover@leftover
```

Restart Claude Code. Or inside Claude Code: `/plugin marketplace add iampreetdave-max/leftover`
then `/plugin install leftover@leftover`.

Without the plugin system, copy `skills/leftover` into `~/.claude/skills/`.

## Use

```
cd ~/side-projects        # any folder whose subfolders are your projects
claude
/leftover:leftover        # or just say "use my leftover limit"
```

Run it in the hours before your weekly reset. That is when unused quota is about to disappear,
and quota that expires within 12 hours is spent in full regardless of your reserve. The
runner opens in its own window, so you can close Claude. Check on it with
`/leftover:leftover status` and end it early with `/leftover:leftover stop`.

Drop a `LEFTOVER.md` in any project to steer it: goals, areas to leave alone, or `skip`.

## What it will and will not do

- Works only in git worktrees under `~/.leftover/wt` on `leftover/<job>` branches. Your
  checkout and uncommitted changes are never touched. Folders that are not git repos are
  only initialised if you say yes.
- Never pushes, merges, deploys, publishes, sends messages, spends money, creates accounts or
  reads secrets. Each job must verify its work with the project's own tests, typecheck or build.
- **Never bills an API key by accident.** If `ANTHROPIC_API_KEY` is set in your environment,
  `claude -p` would charge it instead of your subscription. The runner removes it from every run
  and stops if Claude Code reports any auth other than your subscription.
- Blocks system sleep while it runs (Windows, macOS). Closing a laptop lid or restarting still
  stops it; `/leftover` offers to pick up where it left off.

## Several accounts

Give each extra account its own config folder and log in once:

```
CLAUDE_CONFIG_DIR=~/.claude-personal claude      # then /login
```

Then list them in `~/.leftover/config.json`:

```json
{"accounts": [
  {"name": "work", "provider": "claude", "keep_pct": 5},
  {"name": "personal", "provider": "claude", "config_dir": "~/.claude-personal", "keep_pct": 0}
]}
```

`keep_pct` is the weekly % to leave for your own work before the reset. The first `/leftover`
asks for it and writes this file for you.

## Other agents (Codex and friends)

The runner talks to a tool through two functions: `probe(account)` returns 5-hour and weekly
use with reset times, and `run(...)` runs a prompt in a folder and streams the result. Claude
Code is the one built in. Adding another CLI means writing that pair and registering it in
`PROVIDERS` in [`leftover.py`](skills/leftover/leftover.py). Pull requests welcome.

## Built to survive updates

It only uses documented Claude Code features: headless `claude -p` with `stream-json`, the
`rate_limit_event` it emits, and `CLAUDE_CONFIG_DIR`. No scraped tokens, no private endpoints,
no parsing of the terminal UI. If any of those change shape, the runner stops with a reason
instead of spending blind. Each job records the Claude Code version it ran on, and CI runs the
self-check on Linux, macOS and Windows.

## Requirements

- Claude Code signed in with a Pro, Max or Team plan
- Python 3.9+ and git
- No extra Python packages

## Files

| Where | What |
|---|---|
| `~/.leftover/config.json` | accounts, `task_hours` (max hours per job, default 3) |
| `~/.leftover/ledger.jsonl` | what each job cost in weekly %; later estimates use it |
| `<folder>/.leftover/run.json` | the queue for this run and its results |
| `<folder>/.leftover/REPORT.md` | the report |

## Limits worth knowing

- Usage is reported in whole percents, so small jobs often show as 0% and estimates firm up
  over a few runs.
- Jobs run one at a time. Quota, not time, is the constraint, and it keeps each job's cost
  measurable.
- Linux has no sleep blocking yet.

## Check it

```
python skills/leftover/leftover.py test      # offline self-check, includes a full fake run
python skills/leftover/leftover.py budget    # your accounts' quota right now
```

## License

MIT
