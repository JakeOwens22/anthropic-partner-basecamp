#!/usr/bin/env python3
"""pod_sync.py: one repo, several people, no merge conflicts.

    python3 pod_sync.py --status         # where the team is, and where you are
    python3 pod_sync.py --push-canon     # you built it: publish it as the canon
    python3 pod_sync.py --take-canon     # everyone else: pick the canon up

The protocol is one sentence. Nobody commits agent.py mid-build. One person
pushes the canon at the end of the build. Agree who before the clock runs out.
Everyone else takes it.

That is the whole reason this file exists. Several people editing one agent.py
in one repo at the same time produces a merge conflict inside a function they
are all still learning to read, and no build has time for that. So during
the build everyone builds their own agent.py locally, and the repo only ever
holds one version: the one the team chose.

Nothing here throws your work away. --take-canon saves your own agent.py into
.workshop/mine/ before it touches anything, and prints where it went.

Design rule: never ends on a stack trace, every failure names the fix. You
should never have to read raw git output to know what to do.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RULE = "─" * 66
PROFILE_PATH = os.path.join(HERE, ".workshop", "profile.json")
MINE_DIR = os.path.join(HERE, ".workshop", "mine")

# What a canon push publishes. Anything not on this list stays local, on
# purpose: .env is yours, .workshop/ is your own progress, and .venv/ is a
# machine detail nobody else can use.
#
# readout.html and readout-trace.json are in .gitignore so nobody hand-commits
# a stale readout. This list is the exception, and cmd_push_canon force-adds
# them: the readout is published by the build's committer, regenerated, or not
# at all.
CANON_FILES = ["agent.py", "readout.html", "readout-trace.json", "PITCH.md",
               "evals/cases.json", "build2_probe.txt"]

# The step ids every surface uses: guide, verify.py, decks, hints.
GATE_NAMES = {"1.2": "Make the loop keep going", "1.3": "Make the tools route",
              "1.4": "All five ticket types", "2.1": "Your own tool",
              "2.2": "The same tool, over MCP", "3.1": "Build the proof",
              "4.1": "Make the change, measure it"}


# ---------------------------------------------------------------------------
# git, wrapped so no participant ever reads a raw error
# ---------------------------------------------------------------------------

def git(*args, timeout=40):
    try:
        r = subprocess.run(["git", *args], cwd=HERE, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 127, "git is not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def git_out(*args, timeout=40):
    code, out = git(*args, timeout=timeout)
    return out if code == 0 else ""


def last_line(text):
    return text.splitlines()[-1] if text else "no response"


# ---------------------------------------------------------------------------
# the roster
# ---------------------------------------------------------------------------
# TEAM.md is the roster: the team's name and one typed name per person. The
# overnight review reads the names off it, and --status prints it as one
# paste-able line. Nothing derives a job from its order. The team decides in the
# moment who does what, and the one rule that holds is the canon rule: one
# person pushes at the end of the build, agreed before the clock runs out.

TEAM_PATH = os.path.join(HERE, "TEAM.md")
ROSTER_PLACEHOLDER = re.compile(r"^<.*>$")


def read_roster():
    """The names in TEAM.md, in order, placeholders dropped."""
    try:
        with open(TEAM_PATH, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        name = line[2:].strip()
        if not name or ROSTER_PLACEHOLDER.match(name):
            continue
        names.append(name)
    return names


def fail(headline, *fix_lines):
    """Every exit that is not a success looks like this: one plain sentence,
    then the exact thing to type."""
    print("\n%s" % headline)
    for line in fix_lines:
        print("  %s" % line)
    print(RULE)
    return 1


def preflight():
    """The two conditions every verb needs. Returns an error message or None."""
    code, out = git("rev-parse", "--is-inside-work-tree", timeout=15)
    if code != 0 or out.split()[-1:] != ["true"]:
        return ("This folder is not a git repo, so there is no team to sync with.",
                "Clone the team repo (do not download the zip):",
                "  git clone <your team repo URL>")
    code, _ = git("remote", "get-url", "origin", timeout=15)
    if code != 0:
        return ("This clone has no 'origin' remote, so there is nowhere to push or pull.",
                "If you made this folder with `git init`, clone the team repo instead.",
                "Or point it at the team repo:",
                "  git remote add origin <your team repo URL>")
    return None


def branch_name():
    code, out = git("rev-parse", "--abbrev-ref", "HEAD", timeout=15)
    if code == 0 and out and out != "HEAD":
        return out
    return "main"


def upstream_ref():
    """origin/<branch>, with a fallback for a branch that has no upstream yet."""
    code, out = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=15)
    if code == 0 and out:
        return out
    guess = "origin/%s" % branch_name()
    code, _ = git("rev-parse", "--verify", "--quiet", guess, timeout=15)
    if code == 0:
        return guess
    code, out = git("symbolic-ref", "refs/remotes/origin/HEAD", "--short", timeout=15)
    return out if code == 0 and out else "origin/main"


def fetch():
    code, out = git("fetch", "-q", "origin", timeout=60)
    return code == 0, out


def ahead_behind(ref):
    out = git_out("rev-list", "--left-right", "--count", "HEAD...%s" % ref)
    parts = (out.split() + ["0", "0"])[:2]
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


# ---------------------------------------------------------------------------
# what this laptop has banked
# ---------------------------------------------------------------------------

def load_profile():
    """Read-only view of verify.py's own file. Same shape, never written here."""
    try:
        with open(PROFILE_PATH) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - a missing or half-written file means nothing banked
        return {"name": None, "banked": {}, "caught_up": [], "banked_from_checkpoint": []}


def step_order(step):
    """Sort '1.2' before '1.10' before '2.1', and never raise on junk."""
    try:
        return tuple(int(part) for part in str(step).split("."))
    except (TypeError, ValueError):
        return (99,)


def banked_steps(profile):
    """The step ids this laptop has banked, e.g. ['1.2', '1.3']."""
    steps = [str(key) for key in (profile.get("banked") or {}) if str(key).strip()]
    return sorted(steps, key=step_order)


def canon_commits(ref):
    """Every canon push that has reached the remote, newest first, with the
    committer's note (the commit body) so a teammate who missed the build can
    read what changed and why."""
    out = git_out("log", ref, "--format=%an%x1f%ar%x1f%s%x1f%b%x1e",
                  "--grep=^canon:", "-n", "40")
    rows = []
    for rec in out.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) >= 3:
            rows.append({"author": parts[0].strip(), "when": parts[1].strip(),
                         "subject": parts[2].strip(),
                         "body": (parts[3].strip() if len(parts) > 3 else "")})
    return rows


def print_note(body, indent="            "):
    """The committer's note under a canon line, or nothing."""
    for line in (body or "").splitlines():
        if line.strip():
            print(indent + line.rstrip())


def gate_of(subject):
    """`canon: gate 1.2 by Alice` -> '1.2'. Also reads the old bare integers."""
    words = subject.replace(":", " ").split()
    for i, w in enumerate(words):
        if w == "gate" and i + 1 < len(words):
            candidate = words[i + 1]
            if re.match(r"^\d+(\.\d+)?$", candidate):
                return candidate
    return None


def bench_warning():
    before = os.path.exists(os.path.join(HERE, ".workshop", "bench-before.json"))
    after = os.path.exists(os.path.join(HERE, ".workshop", "bench-after.json"))
    if before and not after:
        return ["You have a bench-before and no bench-after. The gate compares the two,",
                "so right now the team has a baseline and no result. After the lever is",
                "pulled, run:  python3 bench.py --label after"]
    return None


# ---------------------------------------------------------------------------
# --status
# ---------------------------------------------------------------------------

def cmd_status(args):
    print("\n%s\nLARKSPUR TEAM SYNC  ·  status\n%s" % (RULE, RULE))
    ok, out = fetch()
    if not ok:
        print("  remote    could not reach origin")
        print("            %s" % last_line(out))
        print("            Everything below is from your last successful fetch.")
    ref = upstream_ref()
    origin = git_out("remote", "get-url", "origin") or "(none)"
    ahead, behind = ahead_behind(ref)

    print("  repo      %s" % origin)
    print("  branch    %s, tracking %s" % (branch_name(), ref))
    if behind and ahead:
        state = "%d behind and %d ahead of the team" % (behind, ahead)
    elif behind:
        state = "%d commit(s) behind the team" % behind
    elif ahead:
        state = "%d commit(s) here that the team does not have" % ahead
    else:
        state = "level with the team"
    print("  you       %s" % state)

    canon = canon_commits(ref)
    if canon:
        top = canon[0]
        gate = gate_of(top["subject"])
        print("  canon     %s, pushed by %s %s"
              % ("step %s (%s)" % (gate, GATE_NAMES.get(gate, "?")) if gate else top["subject"],
                 top["author"], top["when"]))
        print_note(top.get("body"))
    else:
        print("  canon     nothing published yet (nobody has run --push-canon)")

    profile = load_profile()
    steps = banked_steps(profile)
    if steps:
        codes = ", ".join("%s:%s" % (s, profile["banked"][s]) for s in steps)
        print("  saved     step(s) %s on this laptop  [%s]" % (", ".join(steps), codes))
    else:
        print("  saved     nothing saved on this laptop yet (python3 verify.py <step>)")

    names = sorted(set(c["author"] for c in canon))
    print("  team      %d canon push(es) by %d name(s)" % (len(canon), len(names)))
    if names:
        print("            pushed by: %s" % ", ".join(names))

    # One paste-able line. The overnight review reads these names, and it is
    # the fastest way to see the roster is actually finished.
    people = read_roster()
    if people:
        print("\n  roster: %s" % ", ".join(people))
    else:
        print("\n  roster    TEAM.md has no names yet. Whoever created the repo types one")
        print("            line per person under the team name and commits it once.")

    warn = bench_warning()
    if warn:
        print("\n  warning   %s" % warn[0])
        for line in warn[1:]:
            print("            %s" % line)

    print("\n" + RULE)
    if behind:
        print("You are behind. If the build is over:  python3 pod_sync.py --take-canon")
    elif ahead:
        print("You have local commits the team does not. If you are the build's committer:")
        print("  python3 pod_sync.py --push-canon")
    else:
        print("Nothing to sync. Build.")
    print(RULE)
    return 0


# ---------------------------------------------------------------------------
# --push-canon
# ---------------------------------------------------------------------------

def resolve_gate(args, profile, ref, prefer="remote"):
    """Which build are we syncing?

    The flag always wins. After that the two verbs want different answers, and
    getting this backwards is how a committer gets refused for a step they
    never claimed to be pushing:

      --push-canon  is publishing what passed HERE, so the highest step saved
                    on this laptop is the right guess (prefer="local").
      --take-canon  is picking up what the team published, so the newest canon
                    on the remote is (prefer="remote").
    """
    if args.gate:
        return str(args.gate).strip(), "you named it with --gate"
    steps = banked_steps(profile)
    remote_gate = next((g for g in (gate_of(r["subject"]) for r in canon_commits(ref)) if g),
                       None)
    local = (steps[-1], "the highest step saved on this laptop") if steps else None
    remote = ((remote_gate, "the newest canon on the remote is step %s" % remote_gate)
              if remote_gate else None)
    order = (local, remote) if prefer == "local" else (remote, local)
    for answer in order:
        if answer:
            return answer
    return None, "nothing saved here and no canon on the remote"


def gate_label(gate):
    return "step %s (%s)" % (gate, GATE_NAMES.get(gate, "?"))


def push_with_retry(ref, attempts=3):
    """Two people pushing canon in the same minute is normal, not an error.
    Fetch, rebase, try again, up to three times."""
    for n in range(1, attempts + 1):
        code, out = git("push", "origin", "HEAD", timeout=60)
        if code == 0:
            return True, "pushed on attempt %d" % n, ""
        if n == attempts:
            return False, "three pushes in a row were rejected", "rejected"
        fetch()
        # --autostash: a canon push happens with other files still half-edited,
        # and a rebase that refuses over a dirty tree would strand the commit.
        rcode, _ = git("rebase", "--autostash", ref, timeout=60)
        if rcode != 0:
            git("rebase", "--abort")
            return False, ("someone else pushed a canon for this block while you were "
                           "pushing yours, and the two versions change the same lines"), "conflict"
    return False, "gave up after three attempts", "rejected"


def regenerate_readout():
    """The readout is half the submission, and a stale one is worse than none:
    it shows the agent as it was two blocks ago."""
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "readout.py")],
                           cwd=HERE, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)
    if r.returncode == 0:
        return True, ""
    return False, last_line((r.stdout + r.stderr).strip())


class PushCancelled(Exception):
    """Ctrl-C at the note prompt: stop, and leave nothing half done."""


def canon_note(gate, banked, args):
    """The commit body under a canon push: the step, what is green here, the
    size of the change against the previous canon, and one line from the
    committer on why. Asked for at the prompt when --note was not given and
    someone is at the keyboard; skipped quietly otherwise."""
    lines = ["Step %s (%s) saved. Green on this laptop: %s."
             % (gate, GATE_NAMES.get(gate, "?"), ", ".join(banked) or gate)]
    stat = git_out("diff", "--cached", "--numstat")
    changed = []
    for row in stat.splitlines():
        parts = row.split("\t")
        if len(parts) == 3 and parts[2] in CANON_FILES and parts[0] != "-":
            changed.append("%s +%s -%s" % (parts[2], parts[0], parts[1]))
    if changed:
        lines.append("Changed since the last canon: %s." % ", ".join(changed))
    note = (getattr(args, "note", None) or "").strip()
    if not note and sys.stdin.isatty():
        try:
            print("\n  One line for the team on what changed and why "
                  "(Enter to skip; Ctrl-C to stop without pushing; --note \"...\" next time):")
            note = input("  > ").strip()
        except EOFError:
            note = ""
        except KeyboardInterrupt:
            raise PushCancelled()
    if note:
        lines.append("Why: %s" % note)
    else:
        print("  No note recorded with this canon. Next time add one line for the team:")
        print("    python3 pod_sync.py --push-canon --note \"what changed and why\"")
    lines.append("Behind? python3 pod_sync.py --take-canon, then in claude: /catchup")
    return "\n".join(lines)


def cmd_history(args):
    print("\n%s\nLARKSPUR TEAM SYNC  ·  history\n%s" % (RULE, RULE))
    fetch()
    ref = upstream_ref()
    canon = canon_commits(ref)
    if not canon:
        print("  No canon has been pushed yet. The first --push-canon starts the history.")
        print(RULE)
        return 0
    print("  Every canon push, newest first, with the committer's note.\n")
    for row in canon:
        gate = gate_of(row["subject"])
        print("  %s  pushed by %s, %s" % (gate_label(gate) if gate else row["subject"],
                                          row["author"], row["when"]))
        print_note(row.get("body"), indent="      ")
        print("")
    print(RULE)
    print("To pick up the newest one:  python3 pod_sync.py --take-canon")
    print(RULE)
    return 0


def cmd_push_canon(args):
    print("\n%s\nLARKSPUR TEAM SYNC  ·  push canon\n%s" % (RULE, RULE))
    fetch()
    ref = upstream_ref()
    profile = load_profile()
    gate, why = resolve_gate(args, profile, ref, prefer="local")
    banked = banked_steps(profile)

    if gate is None:
        return fail("Nothing has passed on this laptop yet, so there is no canon to push.",
                    "Run the build's gate first:",
                    "  python3 verify.py <step>",
                    "Then push. The canon is the version that passed, not the newest one.")
    if gate not in banked:
        return fail("Step %s has not passed on this laptop (%s)." % (gate, why),
                    "Saved here: %s" % (", ".join(banked) or "nothing"),
                    "The canon is the version that passed, so run the gate first:",
                    "  python3 verify.py %s" % gate,
                    "If you are pushing a different build, name it:",
                    "  python3 pod_sync.py --push-canon --gate <step>")

    ok, why_not = regenerate_readout()
    if ok:
        print("  readout regenerated from your current agent.py and trace")
    else:
        print("  readout.py could not regenerate (%s)" % why_not)
        print("  Pushing the readout.html already in the folder, if there is one.")
        print("  Fix later with: python3 run.py <PNR> --trace, then python3 readout.py")

    present = [p for p in CANON_FILES if os.path.exists(os.path.join(HERE, p))]
    if not present:
        return fail("None of the files a canon push publishes exist here yet.",
                    "Expected at least agent.py in %s" % HERE,
                    "Are you in the exercise folder? `pwd` should end in /exercise.")
    staged = []
    for path in present:
        # -f: readout.html and readout-trace.json are gitignored so that nobody
        # hand-commits a stale readout. This command is the one place they are
        # meant to be published, and it has just regenerated them.
        code, out = git("add", "-f", "--", path)
        if code == 0:
            staged.append(path)
        elif path == "agent.py":
            return fail("git could not stage agent.py, which is the one file that has to go.",
                        last_line(out),
                        "Fix that, then re-run this command.")
        else:
            print("  skipped %s (%s)" % (path, last_line(out)))
    present = staged

    code, _ = git("diff", "--cached", "--quiet")
    if code == 0:
        print("\n  Nothing changed: what you have is already the team's canon.")
        print(RULE)
        return 0

    author = git_out("config", "user.name") or "unknown"
    # The subject line is what gate_of() and the status board read; keep it.
    # The body is for the teammate who was not here: what changed, and why, in
    # the committer's own words. /catchup reads it back through --history.
    message = "canon: gate %s by %s" % (gate, author)
    try:
        body = canon_note(gate, banked, args)
    except PushCancelled:
        git("reset", "-q", "--", *present)   # unstage; the files themselves are untouched
        return fail("Stopped. Nothing was committed or pushed.",
                    "Your files are exactly as they were. When you are ready:",
                    "  python3 pod_sync.py --push-canon --note \"what changed and why\"")
    code, out = git("commit", "-m", message, "-m", body, timeout=60)
    if code != 0:
        return fail("git could not make the commit.", last_line(out),
                    "Usually this is an unset identity. Set it, then re-run:",
                    "  git config user.name \"Your Name\"",
                    "  git config user.email \"you@yourfirm.com\"")

    ok, detail, kind = push_with_retry(ref)
    if not ok and kind == "conflict":
        return fail("Your canon is committed here but did not reach the team repo: %s." % detail,
                    "Two canons for one block cannot both be the canon, so the team picks one.",
                    "  1. Agree out loud which version it is. Ten seconds, not a debate.",
                    "  2. If it is theirs:  python3 pod_sync.py --take-canon",
                    "     (your version is saved to .workshop/mine/ before anything moves)",
                    "  3. If it is yours:   take theirs the same way, put your change back on",
                    "     top of it, re-run the gate, and push again.",
                    "Nothing is lost either way. Both versions still exist on two laptops.")
    if not ok:
        return fail("Your canon is committed here but did not reach the team repo: %s." % detail,
                    "Do this, in order:",
                    "  1. python3 pod_sync.py --status      (see where the team is)",
                    "  2. git pull --rebase --autostash     (bring their commits under yours)",
                    "  3. python3 pod_sync.py --push-canon  (same command again)",
                    "Still stuck after two tries? Raise a hand. Nothing is lost: your",
                    "commit is here and the team can pull it from your screen if it has to.")

    print("\n  Published as the team's canon: %s" % gate_label(gate))
    for path in present:
        print("    %s" % path)
    print("\n" + RULE)
    print("Tell the team. Everyone else now runs:")
    print("  python3 pod_sync.py --take-canon")
    print(RULE)
    return 0


# ---------------------------------------------------------------------------
# --take-canon
# ---------------------------------------------------------------------------

def save_my_agent(gate):
    """Before anything else. Their file, kept, with a name that sorts by time
    so a second take-canon in the same block never overwrites the first."""
    src = os.path.join(HERE, "agent.py")
    if not os.path.exists(src):
        return None
    os.makedirs(MINE_DIR, exist_ok=True)
    block = ("step%s" % str(gate).replace(".", "-")) if gate else "step-unknown"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(MINE_DIR, "agent-%s-%s.py" % (block, stamp))
    n = 2
    while os.path.exists(path):  # same second, same block: keep both, in order
        path = os.path.join(MINE_DIR, "agent-%s-%s-%d.py" % (block, stamp, n))
        n += 1
    with open(src) as fh:
        body = fh.read()
    with open(path, "w") as fh:
        fh.write(body)
    return path


def colliding_untracked(ref):
    """Files the incoming canon tracks that exist here but are NOT tracked here.

    These are exactly the ones a pull refuses to overwrite, and the usual pair
    is readout.html / readout-trace.json: gitignored locally, published by the
    committer, so the first --take-canon after a canon push meets two untracked
    files sitting where two incoming tracked files want to go. Nothing here is
    a merge problem; they just have to move.
    """
    incoming = [p.strip() for p in git_out("ls-tree", "-r", "--name-only", ref).splitlines()
                if p.strip()]
    tracked = set(p.strip() for p in git_out("ls-files").splitlines() if p.strip())
    return [p for p in incoming
            if p not in tracked and os.path.exists(os.path.join(HERE, p))]


def move_aside(paths):
    """Same drawer as save_my_agent, same promise: nothing here deletes a file.
    Returns [(path, where it went)]."""
    moved = []
    if not paths:
        return moved
    os.makedirs(MINE_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for rel in paths:
        flat = rel.replace("\\", "-").replace("/", "-")
        dest = os.path.join(MINE_DIR, "%s-%s" % (stamp, flat))
        n = 2
        while os.path.exists(dest):
            dest = os.path.join(MINE_DIR, "%s-%s-%d" % (stamp, flat, n))
            n += 1
        try:
            shutil.move(os.path.join(HERE, rel), dest)
        except OSError:
            continue
        moved.append((rel, os.path.relpath(dest, HERE)))
    return moved


def clear_collisions(ref):
    """Move-aside pass with the one line of narration it deserves."""
    moved = move_aside(colliding_untracked(ref))
    for rel, dest in moved:
        print("  %s was here but never committed, and the canon carries it. Moved to %s"
              % (rel, dest))
    return moved


def restore_deleted():
    """After a mixed reset, files the canon added but this laptop never had
    read as deletions. They are not: nobody deleted anything."""
    out = git_out("ls-files", "--deleted")
    for path in out.splitlines():
        if path.strip():
            git("checkout", "--", path)


def cmd_take_canon(args):
    print("\n%s\nLARKSPUR TEAM SYNC  ·  take canon\n%s" % (RULE, RULE))
    ok, out = fetch()
    if not ok:
        return fail("Could not reach the team repo, so there is no canon to take.",
                    last_line(out),
                    "Check wifi first, then:",
                    "  python3 pod_sync.py --status",
                    "If the repo is private, the creator has to add you as a collaborator.")
    ref = upstream_ref()
    profile = load_profile()
    gate, why = resolve_gate(args, profile, ref)
    banked = banked_steps(profile)

    saved = save_my_agent(gate)
    if saved:
        print("  your agent.py saved to %s" % os.path.relpath(saved, HERE))
    else:
        print("  no agent.py here to save (nothing of yours can be lost)")

    # Advisory, never a refusal. A team at the end of a block needs everyone on
    # the canon more than it needs one person's gate, and refusing here strands
    # whoever ran out of time on a file the next block does not start from.
    if gate is not None and gate not in banked and not args.force:
        print("\n  Heads up: the canon carries step %s, which you have not saved "
              "(saved here: %s)." % (gate, ", ".join(banked) or "nothing"))
        print("  Taking it anyway. Your file is saved at %s, and the step you missed is"
              % (os.path.relpath(saved, HERE) if saved else "nowhere: there was none"))
        print("  still worth finishing on your own copy: python3 verify.py %s" % gate)
    if gate is None:
        print("  which build this is could not be worked out (%s), so no gate check" % why)

    # Before anything moves: untracked files the canon tracks. Otherwise the
    # pull below refuses over readout.html and the only advice left is a
    # re-clone, which costs the team ten minutes it does not have.
    clear_collisions(ref)

    ahead, behind = ahead_behind(ref)
    if ahead:
        unwound = git_out("log", "--format=  %h %s", "%s..HEAD" % ref)
        code, out = git("reset", "--mixed", ref, timeout=60)
        if code != 0:
            return fail("Could not move your branch onto the team's canon.", last_line(out),
                        "Your agent.py is saved at %s, so nothing of yours is at risk."
                        % (os.path.relpath(saved, HERE) if saved else "(none)"),
                        "Show this line in the room and keep building on your own copy.")
        restore_deleted()
        git("checkout", "--", "agent.py")
        print("  you had local commit(s) the team does not have. They are unwound onto the")
        print("  canon, and every file they changed is still here as an uncommitted change:")
        for line in unwound.splitlines():
            print("  %s" % line)
    else:
        git("checkout", "--", "agent.py")
        if behind:
            code, out = git("pull", "--rebase", "--autostash", "origin", branch_name(),
                            timeout=60)
            if code != 0:
                git("rebase", "--abort")
                # One more pass: a pull that fails names what is in the way, and
                # a file that appeared since the first sweep is still just a
                # file that has to move, not a reason to re-clone.
                if clear_collisions(ref):
                    code, out = git("pull", "--rebase", "--autostash", "origin",
                                    branch_name(), timeout=60)
                    if code != 0:
                        git("rebase", "--abort")
            if code != 0:
                return fail("Could not fast-forward onto the team's canon.", last_line(out),
                            "Your agent.py is saved at %s."
                            % (os.path.relpath(saved, HERE) if saved else "(none)"),
                            "Simplest fix, and it costs a minute:",
                            "  1. copy .workshop/ somewhere safe (your saved codes live there)",
                            "  2. re-clone the team repo into a new folder",
                            "  3. copy .workshop/ back in")

    code, _ = git("diff", "--quiet", ref, "--", "agent.py")
    if code != 0:
        return fail("agent.py here still does not match the team's canon.",
                    "Your version is saved at %s."
                    % (os.path.relpath(saved, HERE) if saved else "(none)"),
                    "Run this to take it by hand, then say in the room that it happened:",
                    "  git checkout %s -- agent.py" % ref)

    canon = canon_commits(ref)
    print("\n  agent.py is now the team's canon", end="")
    if canon:
        top = canon[0]
        print(" (%s, pushed by %s %s)" % (top["subject"], top["author"], top["when"]))
        if top.get("body"):
            print("  What changed, in the committer's words:")
            print_note(top["body"], indent="    ")
    else:
        print("")
    if saved:
        print("  Yours is not gone. It is at %s and you can diff the two:"
              % os.path.relpath(saved, HERE))
        print("    diff %s agent.py" % os.path.relpath(saved, HERE))
    print(RULE)
    print("The next build starts from this file, on every laptop on the team.")
    print(RULE)
    return 0


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Keep one repo in sync across a team.")
    parser.add_argument("--status", action="store_true",
                        help="where the team is, and where you are")
    parser.add_argument("--push-canon", action="store_true",
                        help="publish your version as the team's canon (one person per block)")
    parser.add_argument("--take-canon", action="store_true",
                        help="pick up the team's canon (everyone else)")
    parser.add_argument("--history", action="store_true",
                        help="every canon push so far, with the committer's note on what "
                             "changed and why (what /catchup reads)")
    parser.add_argument("--note", default=None,
                        help="with --push-canon: one line for the team on what changed and why")
    parser.add_argument("--gate", default=None,
                        help="which verify.py step this build is, e.g. 1.4 "
                             "(default: worked out for you)")
    parser.add_argument("--force", action="store_true",
                        help="with --take-canon: you already know your gate has not passed. "
                             "Skip the heads-up (taking it is no longer refused)")
    args = parser.parse_args()

    verbs = [args.status, args.push_canon, args.take_canon, args.history]
    if sum(1 for v in verbs if v) != 1:
        print("\n%s\nLARKSPUR TEAM SYNC\n%s" % (RULE, RULE))
        return fail("Pick exactly one thing to do.",
                    "python3 pod_sync.py --status        where the team is",
                    "python3 pod_sync.py --push-canon    you built it, publish it",
                    "python3 pod_sync.py --take-canon    everyone else, pick it up",
                    "python3 pod_sync.py --history       what changed and why, every push")

    problem = preflight()
    if problem:
        print("\n%s\nLARKSPUR TEAM SYNC\n%s" % (RULE, RULE))
        return fail(*problem)

    if args.status:
        return cmd_status(args)
    if args.history:
        return cmd_history(args)
    if args.push_canon:
        return cmd_push_canon(args)
    return cmd_take_canon(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped. Nothing was pushed. Re-run the same command when you are ready.")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - a stack trace is never the last thing a participant sees
        print("\n%s" % RULE)
        print("pod_sync hit something it does not have a fix for:")
        print("  %s: %s" % (type(exc).__name__, exc))
        print("This is a workshop bug. Say so in the room, it will be wrong for someone else too.")
        print("Meanwhile your work is safe: nothing here deletes a file, and your own")
        print("agent.py copies are in .workshop/mine/.")
        print(RULE)
        sys.exit(1)
