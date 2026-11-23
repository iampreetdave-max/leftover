"""leftover runner: spends spare subscription quota on the queue in <space>/.leftover/run.json.

The /leftover skill does the judgment (scan projects, rank a menu, ask questions) and writes
run.json. This script does the unattended loop with no LLM in charge: read each account's quota,
pick the account whose weekly quota resets first (it is lost soonest), run one task headless in
a git worktree, stop at each account's keep line, write REPORT.md.

  python leftover.py budget          quota per account
  python leftover.py run <space>     work the queue (create <space>/.leftover/STOP to end early)
  python leftover.py test            self-check, no network

~/.leftover/config.json (optional; default is one Claude account in ~/.claude, keep_pct 5):
  {"accounts": [{"name": "work", "provider": "claude", "keep_pct": 5},
                {"name": "personal", "provider": "claude", "config_dir": "~/.claude-personal"}],
   "task_hours": 3}
keep_pct = weekly % left untouched for workdays before the reset (ignored within 12 h of it).

Adding a provider (Codex, ...) = a probe(acct) -> budget dict and a run(...) -> state dict like
the claude_* pair below, registered in PROVIDERS. Only documented surfaces are used (claude -p,
stream-json, rate_limit_event, CLAUDE_CONFIG_DIR) so CLI updates do not break it silently:
anything unexpected stops the run with a reason instead of spending blind.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

HOME = Path.home() / ".leftover"
CLAUDE = shutil.which("claude") or "claude"
EXCLUDES = "node_modules/\n.env*\n*.pem\n*.key\ndist/\nbuild/\n.next/\n__pycache__/\n.venv/\n"

RULES = """You are working alone and unattended; nobody can answer until the run ends.
You are in a git worktree on branch {branch}. The user's own checkout must not change.
Commit in small steps. Never push, merge, deploy, publish, send messages or email, spend money,
create accounts, or read or print secrets and .env values. Do not delete anything outside this
worktree. Install dependencies here if needed. Verify with the project's own checks (tests,
typecheck, build, actually running it) before calling it done. A choice you cannot ask about:
take the reversible default and log it. Where instruction files say to ask first (trackers,
client-facing text), put it under QUESTIONS instead. Truly blocked: stop and say what you need.

End your final message with exactly these sections:
RESULT: done | blocked | failed
CHANGED: 3-8 bullets, user-visible effect first
VERIFY: commands the user runs to see it, from {cwd}
DECISIONS: defaults you picked and why
QUESTIONS: what only the user can answer, or none"""


def config():
    try:
        cfg = json.loads((HOME / "config.json").read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        cfg = {}
    cfg.setdefault("accounts", [{"name": "default", "provider": "claude", "keep_pct": 5}])
    cfg.setdefault("task_hours", 3)
    return cfg


def local(epoch):
    return datetime.fromtimestamp(epoch).strftime("%a %d %b %H:%M")


# ---- claude provider ---------------------------------------------------------------------

def fold(s, line):
    """Fold one `claude -p --output-format stream-json --verbose` line into state dict s."""
    try:
        o = json.loads(line)
    except ValueError:
        return s
    t = o.get("type")
    if t == "system" and o.get("subtype") == "init":
        s.update(auth=o.get("apiKeySource"), version=o.get("claude_code_version"),
                 session_id=o.get("session_id"))
    elif t == "rate_limit_event":
        wins = (o.get("rate_limit_info") or {}).get("unifiedWindows") or {}
        rate = s.setdefault("rate", {})
        for key, w in (("weekly", wins.get("seven_day")), ("five_hour", wins.get("five_hour"))):
            if w:
                rate[key] = round(w["utilization"] * 100, 2)
                rate[key + "_reset"] = w["resetsAt"]
    elif t == "result":
        s.update(result=o.get("result") or "", is_error=bool(o.get("is_error")),
                 error_status=o.get("api_error_status"),
                 session_id=o.get("session_id") or s.get("session_id"))
    return s


def stop_reason(s, stop_at, stop_file=None):
    # money guard: an API key in the environment makes claude -p bill it, not the subscription
    if s.get("auth") not in (None, "none"):
        return f"auth is {s['auth']}, not the subscription"
    if s.get("rate", {}).get("weekly", 0) >= stop_at:
        return "budget"
    if stop_file and stop_file.exists():
        return "user"
    return None


def claude_proc(acct, args, cwd=None):
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    if acct.get("config_dir"):  # unset = the default ~/.claude login
        env["CLAUDE_CONFIG_DIR"] = str(Path(acct["config_dir"]).expanduser())
    return subprocess.Popen([CLAUDE, "-p", "--output-format", "stream-json", "--verbose", *args],
                            cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")


def kill(p):
    if os.name == "nt":  # whole tree: the agent may have started dev servers
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    else:
        p.kill()


def claude_probe(acct):
    """One tiny Haiku call; its rate_limit_event carries the account's 5-hour and weekly use."""
    p = claude_proc(acct, ["--model", "haiku", "--max-turns", "1", "--tools", "", "--strict-mcp-config"])
    try:
        out, _ = p.communicate("Reply with: ok", timeout=300)
    except subprocess.TimeoutExpired:
        kill(p)
        return None
    s = {}
    for line in out.splitlines():
        fold(s, line)
    why = stop_reason(s, 101)
    if why:
        sys.exit(f"{acct['name']}: {why}. Refusing to run.")
    return s.get("rate") if "weekly" in s.get("rate", {}) else None


def claude_run(acct, prompt, cwd, log_path, model=None, resume=None, stop_at=100, hours=3, stop_file=None):
    args = ["--permission-mode", "auto"] + (["--model", model] if model else []) + (["--resume", resume] if resume else [])
    p = claude_proc(acct, args, cwd)
    p.stdin.write(prompt)
    p.stdin.close()
    timer = threading.Timer(hours * 3600, kill, [p])
    timer.start()
    t0, s = time.time(), {}
    with open(log_path, "a", encoding="utf-8") as log:
        for line in p.stdout:
            log.write(line)
            s["stopped"] = stop_reason(fold(s, line), stop_at, stop_file)
            if s["stopped"]:  # ponytail: checked per stream line, so it can overshoot by one turn
                kill(p)
                break
    p.wait()
    timer.cancel()
    if not s.get("stopped") and "result" not in s and time.time() - t0 >= hours * 3600 - 5:
        s["stopped"] = "time"
    return s


PROVIDERS = {"claude": (claude_probe, claude_run)}


# ---- scheduling --------------------------------------------------------------------------

def keep_line(acct, b, now):
    """Weekly % at which to stop. Quota that expires within 12 h is free to spend."""
    return 100 - (0 if b["weekly_reset"] - now < 12 * 3600 else acct.get("keep_pct", 5))


def choose(accounts, budgets, now):
    """(acct, None) run now · (None, epoch) wait for a 5-hour window · (None, None) all spent."""
    open_ = [(a, budgets[a["name"]]) for a in accounts
             if budgets.get(a["name"]) and budgets[a["name"]]["weekly"] < keep_line(a, budgets[a["name"]], now) - 1]
    ready = [(a, b) for a, b in open_ if b.get("five_hour", 0) < 90]
    if ready:
        return min(ready, key=lambda ab: ab[1]["weekly_reset"])[0], None
    if open_:
        return None, min(b["five_hour_reset"] for _, b in open_) + 60
    return None, None


def section(text, name):
    m = re.search(rf"^{name}:\s*(.*?)(?=^[A-Z]{{4,}}:|\Z)", text or "", re.M | re.S)
    return m.group(1).strip() if m else ""


def questions(text):
    q = section(text, "QUESTIONS")
    return "" if q.lower().strip(" .") in ("", "none", "- none") else q


# ---- git ---------------------------------------------------------------------------------

def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def worktree(proj, task):
    """Worktree outside the project (and outside OneDrive) on leftover/<slug>; returns the cwd."""
    branch, wt = f"leftover/{task['slug']}", HOME / "wt" / f"{proj.name}-{task['slug']}"
    try:
        top = Path(git("rev-parse", "--show-toplevel", cwd=proj))
    except subprocess.CalledProcessError:
        if not task.get("git_init"):
            raise ValueError("not a git repo and git init was not approved")
        git("init", "-q", cwd=proj)
        (proj / ".git" / "info").mkdir(exist_ok=True)
        (proj / ".git" / "info" / "exclude").write_text(EXCLUDES)  # ignores without touching user files
        git("add", "-A", cwd=proj)
        git("commit", "-qm", "leftover: baseline", "--allow-empty", cwd=proj)
        top = proj
    if not wt.exists():
        try:
            git("rev-parse", "--verify", "-q", branch, cwd=top)
            git("worktree", "add", "-q", str(wt), branch, cwd=top)
        except subprocess.CalledProcessError:
            git("worktree", "add", "-q", str(wt), "-b", branch, cwd=top)
    task.update(branch=branch, worktree=str(wt))
    return wt / proj.resolve().relative_to(top.resolve())


# ---- run ---------------------------------------------------------------------------------

def run(space):
    space = Path(space).resolve()
    d = space / ".leftover"
    (d / "logs").mkdir(parents=True, exist_ok=True)
    state = json.loads((d / "run.json").read_text(encoding="utf-8-sig"))
    cfg, stop = config(), d / "STOP"
    stop.unlink(missing_ok=True)
    keep_awake()

    def log(msg):
        line = f"{datetime.now():%a %H:%M} {msg}"
        print(line, flush=True)
        with open(d / "runner.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def save():
        (d / "run.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        write_report(space, state, budgets)

    budgets = {}
    while not stop.exists():
        task = next((t for t in state["tasks"] if t.get("status", "pending") == "pending"), None)
        if not task:
            break
        budgets = {a["name"]: PROVIDERS[a["provider"]][0](a) for a in cfg["accounts"]}
        acct, wait = choose(cfg["accounts"], budgets, time.time())
        if not acct and not wait:
            log("every account is at its keep line; stopping")
            break
        if not acct:
            log(f"5-hour windows full; sleeping until {local(wait)}")
            while time.time() < wait and not stop.exists():
                time.sleep(60)
            continue
        run_task(space, task, acct, budgets[acct["name"]], cfg, log)
        save()
    log("run finished")
    save()


def run_task(space, task, acct, before, cfg, log):
    probe, runner = PROVIDERS[acct["provider"]]
    try:
        cwd = worktree(space / task["project"], task)
    except (ValueError, subprocess.CalledProcessError) as e:
        task.update(status="failed", note=f"setup: {getattr(e, 'stderr', '') or e}")
        return log(f"#{task['id']} setup failed: {task['note']}")
    retry = task.get("attempts", 0) > 0
    resume = task.get("session_id") if retry and task.get("account") == acct["name"] else None
    head = (f"Continue the same task. Earlier progress is committed on this branch (see git log).\n"
            if retry else "") + f"Project: {task['project']}\nTask: {task['title']}\n\n{task['brief']}\n\n"
    log(f"#{task['id']} {task['project']}: {task['title']} on {acct['name']} ({before['weekly']}% used)")
    t0 = time.time()
    s = runner(acct, head + RULES.format(branch=task["branch"], cwd=cwd), str(cwd),
               space / ".leftover" / "logs" / f"{task['id']}-{task['slug']}.jsonl",
               model=task.get("model"), resume=resume, stop_at=keep_line(acct, before, t0),
               hours=cfg["task_hours"], stop_file=space / ".leftover" / "STOP")
    if (s.get("stopped") or "").startswith("auth"):
        sys.exit(f"{acct['name']}: {s['stopped']}. Refusing to run.")
    after = probe(acct) or before
    pct, minutes = round(after["weekly"] - before["weekly"], 2), round((time.time() - t0) / 60)
    task.update(account=acct["name"], session_id=s.get("session_id"), cli=s.get("version"),
                pct=round(task.get("pct", 0) + pct, 2), minutes=task.get("minutes", 0) + minutes)
    if s.get("result") and not s.get("is_error"):
        word = (section(s["result"], "RESULT").split() or ["done"])[0].lower().strip(".")
        task.update(result=s["result"], status=word if word in ("done", "blocked", "failed") else "done")
    elif s.get("stopped") in ("budget", None) and task.get("attempts", 0) < 2:
        # usage limit, API error or budget stop: retry later, possibly on another account
        task.update(status="pending", attempts=task.get("attempts", 0) + 1,
                    note=s.get("result") or s.get("stopped") or "no result")
    else:
        task.update(status=f"stopped: {s['stopped']}" if s.get("stopped") else "failed",
                    note=s.get("result") or "no result; see the log")
    with open(HOME / "ledger.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"date": f"{datetime.now():%Y-%m-%d}", "account": acct["name"],
                            "project": task["project"], "task": task["title"], "model": task.get("model"),
                            "size": task.get("size"), "pct": pct, "minutes": minutes,
                            "status": task["status"]}) + "\n")
    log(f"#{task['id']} {task['status']}: {pct}% weekly, {minutes} min")


def write_report(space, state, budgets):
    tasks = state["tasks"]
    out = [f"# leftover report ({datetime.now():%a %d %b %H:%M})", "",
           " · ".join(f"{k}: {v}" for k, v in Counter(t.get("status", "pending") for t in tasks).items())]
    out += [f"- {n}: {b['weekly']}% of weekly used, resets {local(b['weekly_reset'])}"
            for n, b in budgets.items() if b]
    asks = [(t, questions(t.get("result"))) for t in tasks]
    if any(q for _, q in asks):
        out += ["", "## Questions for you"]
        out += [f"\n**#{t['id']} {t['project']}: {t['title']}**\n{q}" for t, q in asks if q]
    out += ["", "## Tasks"]
    for t in tasks:
        out += ["", f"### #{t['id']} {t['project']}: {t['title']}",
                f"{t.get('status', 'pending')} · {t.get('account', '-')} · {t.get('pct', 0)}% weekly · {t.get('minutes', 0)} min"]
        if t.get("branch"):
            out.append(f"Branch `{t['branch']}` in worktree `{t['worktree']}`. "
                       f"Review: `git -C \"{space / t['project']}\" log --stat HEAD..{t['branch']}`")
        if t.get("note") and not t.get("result"):
            out.append(f"Note: {t['note']}")
        if t.get("result"):
            out += ["", t["result"].strip()]
    (space / ".leftover" / "REPORT.md").write_text("\n".join(out) + "\n", encoding="utf-8")


def keep_awake():
    """Block system sleep for this process's lifetime. Closing a laptop lid still sleeps."""
    if os.name == "nt":
        import ctypes
        f = ctypes.windll.kernel32.SetThreadExecutionState
        f.argtypes = [ctypes.c_uint]
        f(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    elif sys.platform == "darwin":
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    # ponytail: Linux has no keep-awake yet; add systemd-inhibit when someone needs it


def budget():
    cfg, now = config(), time.time()
    for a in cfg["accounts"]:
        b = PROVIDERS[a["provider"]][0](a)
        if not b:
            print(f"{a['name']}: no subscription limits reported (API-key login or not Pro/Max/Team)")
            continue
        line = keep_line(a, b, now)
        print(f"{a['name']}: {b['weekly']}% of weekly used, {max(0, round(line - b['weekly'], 1))}% spendable "
              f"(keep {100 - line}%), resets {local(b['weekly_reset'])}; "
              f"5h window {b.get('five_hour', '?')}%, resets {local(b.get('five_hour_reset', now))}")


def selftest():
    s = {}
    for line in ['{"type":"system","subtype":"init","apiKeySource":"none","session_id":"x","claude_code_version":"9"}',
                 '{"type":"rate_limit_event","rate_limit_info":{"unifiedWindows":{"five_hour":'
                 '{"utilization":0.2,"resetsAt":100},"seven_day":{"utilization":0.81,"resetsAt":900}}}}',
                 "not json",
                 '{"type":"result","is_error":false,"result":"did it\\nRESULT: blocked\\nQUESTIONS: which db?"}']:
        fold(s, line)
    assert s["auth"] == "none" and s["rate"] == {"weekly": 81, "weekly_reset": 900, "five_hour": 20, "five_hour_reset": 100}
    assert section(s["result"], "RESULT") == "blocked" and questions(s["result"]) == "which db?"
    assert questions("RESULT: done\nQUESTIONS: none") == ""
    assert stop_reason({"auth": "ANTHROPIC_API_KEY"}, 100).startswith("auth")
    assert stop_reason(s, 81) == "budget" and stop_reason(s, 95) is None
    a, b = {"name": "a", "keep_pct": 5}, {"name": "b", "keep_pct": 5}

    def B(w, wr, f=0, fr=50):
        return {"weekly": w, "weekly_reset": wr, "five_hour": f, "five_hour_reset": fr}
    day = 86400
    assert choose([a, b], {"a": B(50, 5 * day), "b": B(50, day)}, 0)[0] is b   # expires first wins
    assert choose([a, b], {"a": B(95, 5 * day), "b": B(94.5, 5 * day)}, 0) == (None, None)  # at keep line
    assert choose([a], {"a": B(97, 3600)}, 0)[0] is a                           # reset within 12 h: keep 0
    assert choose([a], {"a": B(50, 5 * day, f=95, fr=777)}, 0) == (None, 837)  # wait for the 5h window
    assert choose([a], {"a": None}, 0) == (None, None)

    # full offline run through a fake provider: git init, worktree, report, ledger
    import tempfile
    global HOME
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        os.environ.setdefault(k, "leftover-test")

    def fake_run(acct, prompt, cwd, log_path, **kw):
        assert "Never push" in prompt and kw["stop_at"] == 95
        Path(cwd, "x.txt").write_text("x")
        git("add", "-A", cwd=cwd)
        git("commit", "-qm", "x", cwd=cwd)
        return {"stopped": None, "result": "RESULT: done\nQUESTIONS: ship it?", "session_id": "s1"}
    PROVIDERS["fake"] = (lambda acct: B(10, time.time() + 5 * day), fake_run)
    with tempfile.TemporaryDirectory() as tmp:
        HOME, space = Path(tmp) / "home", Path(tmp) / "space"
        (space / "proj").mkdir(parents=True)
        (space / "proj" / "a.txt").write_text("mine")
        (space / ".leftover").mkdir()
        HOME.mkdir()
        (HOME / "config.json").write_text('{"accounts": [{"name": "f", "provider": "fake", "keep_pct": 5}]}')
        (space / ".leftover" / "run.json").write_text(json.dumps({"tasks": [
            {"id": 1, "project": "proj", "slug": "t", "title": "T", "brief": "b", "git_init": True},
            {"id": 2, "project": "proj", "slug": "u", "title": "U", "brief": "b"}]}))
        run(space)
        t1, t2 = json.loads((space / ".leftover" / "run.json").read_text())["tasks"]
        assert t1["status"] == "done" and t1["account"] == "f" and t2["status"] == "done", (t1, t2)
        assert "ship it?" in (space / ".leftover" / "REPORT.md").read_text(encoding="utf-8")
        assert len((HOME / "ledger.jsonl").read_text().splitlines()) == 2
        assert sorted(os.listdir(space / "proj")) == [".git", "a.txt"]  # user's checkout untouched
        assert git("log", "--oneline", "leftover/t", cwd=space / "proj").count("\n") == 1
    print("ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "budget":
        budget()
    elif cmd == "run" and len(sys.argv) > 2:
        run(sys.argv[2])
    elif cmd == "test":
        selftest()
    else:
        sys.exit(__doc__)
