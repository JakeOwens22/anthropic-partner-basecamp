#!/usr/bin/env python3
"""setup.py: run this until it says READY. That is the whole setup.

    python3 setup.py            # check this seat
    python3 setup.py --fix      # create .venv and install, then re-run
    python3 setup.py --no-live  # skip the live API call (faster re-runs)

It checks one thing: that this laptop can do the build. Python, the SDK, a
credential that actually works, git, and a real reach to the team repo. Run it
at your own desk, with time to fix what it finds. Everything it names is a 10
minute fix before the day and a lost build in the room.

Design rule: this script never ends on a stack trace and never ends on
"something went wrong". Every failure names the fix. If you hit one it cannot
explain, that is a bug in the workshop. Say so in the room, because it will be
wrong for someone else too.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RULE = "─" * 66
MIN_PY = (3, 9)
GOOD_PY = (3, 11)
MODEL = "claude-sonnet-5"


class Check:
    """One line on the board, with a stable id so a tool can track it."""

    def __init__(self, id, ok, label, fix="", severity="required"):
        self.id, self.ok, self.label, self.fix, self.severity = id, ok, label, fix, severity


def _git(*args, **kwargs):
    """Never let git open an interactive prompt. A password box behind a script
    looks like a hang, and a hang has no fix line."""
    timeout = kwargs.pop("timeout", 20)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="echo",
               GCM_INTERACTIVE="never")
    try:
        r = subprocess.run(["git"] + list(args), cwd=HERE, capture_output=True,
                           text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 127, "git is not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def last_line(text):
    return text.splitlines()[-1] if text else "no response"


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------

def check_python():
    v = sys.version_info
    label = "Python %d.%d.%d at %s" % (v[0], v[1], v[2], sys.executable)
    if v < MIN_PY:
        return Check("python", False, label,
                     "Too old. Install Python 3.11 from python.org, or "
                     "`brew install python@3.11`.")
    if v < GOOD_PY:
        return Check("python", True, label + "  (3.11 preferred, 3.9 works)")
    return Check("python", True, label)


def check_venv():
    """Advisory. What has to be true is that the SDK imports, and the next
    check says so. A venv is how you get there without an IT ticket."""
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return Check("venv", True, "isolated environment: %s" % os.path.basename(sys.prefix))
    conda = os.environ.get("CONDA_PREFIX")
    if conda and os.path.realpath(sys.executable).startswith(os.path.realpath(conda)):
        return Check("venv", True, "isolated environment: conda (%s)" % os.path.basename(conda))
    if os.path.isdir(os.path.join(HERE, ".venv")):
        return Check("venv", False, ".venv is here but this shell is not in it",
                     "source .venv/bin/activate     (.venv\\Scripts\\activate on Windows)\n"
                     "     Not blocking, as long as the SDK check below is green.",
                     "advisory")
    return Check("venv", False, "running on system Python, no .venv here",
                 "Not blocking, as long as the SDK check below is green. If it is not:\n"
                 "       python3 setup.py --fix\n"
                 "     creates .venv here and installs into it. Nothing touches your system\n"
                 "     Python, so no admin rights and no IT ticket.",
                 "advisory")


def check_sdk():
    try:
        import anthropic
    except ImportError:
        return Check("sdk", False, "anthropic SDK installed",
                     "Fix: python3 setup.py --fix\n"
                     "     or: python3 -m pip install -r requirements.txt")
    return Check("sdk", True, "anthropic SDK %s" % getattr(anthropic, "__version__", "?"))


def check_writable():
    """.workshop/ is where your saved gates and bench numbers live. If it
    cannot be written, verify.py has nowhere to put your receipt."""
    target = os.path.join(HERE, ".workshop")
    try:
        os.makedirs(target, exist_ok=True)
        probe = os.path.join(target, ".probe")
        with open(probe, "w") as handle:
            handle.write("ok")
        os.remove(probe)
        return Check("writable", True, "can write to .workshop/ (your gates and bench numbers)")
    except OSError as exc:
        return Check("writable", False, "can write to .workshop/",
                     "%s\n     Move the folder somewhere you own. Desktop or home, not a "
                     "synced or managed directory." % exc)


def check_git_identity():
    code_v, out_v = _git("--version", timeout=15)
    if code_v != 0:
        return Check("gitid", False, "git installed",
                     "The whole session runs out of one shared repo, so git is not optional.\n"
                     "     macOS: xcode-select --install\n"
                     "     Windows: https://git-scm.com/download/win\n"
                     "     Linux: your package manager, e.g. sudo apt install git")
    code_n, name = _git("config", "user.name", timeout=15)
    code_e, email = _git("config", "user.email", timeout=15)
    if code_n != 0 or not name or code_e != 0 or not email:
        return Check("gitid", False, "git identity set",
                     "Your commits would show up as nobody.\n"
                     "       git config --global user.name \"Your Name\"\n"
                     "       git config --global user.email \"you@yourfirm.com\"")
    return Check("gitid", True, "git identity: %s <%s>" % (name, email))


def check_env_ignored():
    """A key in the repo is the one mistake here that follows you home."""
    code, _ = _git("check-ignore", "-q", ".env")
    if code == 127:
        return Check("envleak", True, ".env is gitignored (skipped, git is not installed)",
                     "", "advisory")
    if code != 0:
        return Check("envleak", False, ".env is gitignored",
                     "Your API key would go to GitHub on the next push. Put `.env` in "
                     ".gitignore\n     before you write the file, and say so in the room if "
                     "it was ever pushed.")
    code, out = _git("log", "--all", "--oneline", "--", ".env")
    if code == 0 and out:
        return Check("envleak", False, ".env has never been committed",
                     "Found in this repo's history: %s\n"
                     "     A key reached this repo's history. Rotate it now and say so in "
                     "the room.\n"
                     "     Rotating is the fix. Deleting the file is not, because the old "
                     "commit still has it." % out.splitlines()[0])
    return Check("envleak", True, ".env is gitignored and never committed")


def check_origin():
    """A real reach for the team repo. This is the check that catches the
    corporate proxy that allows browsers and blocks git, which otherwise shows
    up as a dead ten minutes in the first build."""
    code, url = _git("remote", "get-url", "origin", timeout=15)
    if code != 0:
        return Check("origin", False, "git can reach the team repo",
                     "This folder has no 'origin', so it is not a clone of the team repo.\n"
                     "     Clone it, do not download the zip:\n"
                     "       git clone <your team repo URL>")
    code, out = _git("ls-remote", "--heads", "origin", timeout=30)
    if code == 0:
        return Check("origin", True, "git can reach the team repo (%s)" % url)
    text = out.lower()
    if "authentication" in text or "could not read username" in text \
            or "permission denied" in text or "403" in text:
        return Check("origin", False, "git can reach the team repo",
                     "git got to the remote and was turned away. If the repo is private, "
                     "whoever\n     created it has to add you as a collaborator, and you have "
                     "to accept the invite.\n"
                     "     That is the usual answer, not your SSH key.\n"
                     "     https auth: `gh auth login`.  SSH: `ssh -T git@github.com`.")
    return Check("origin", False, "git can reach the team repo",
                 "%s\n"
                 "     git could not get to the remote at all. You clone and push on the day, "
                 "so this\n     has to work. In order:\n"
                 "       1. off the corporate VPN, or on guest wifi, try again\n"
                 "       2. behind a proxy? git config --global http.proxy http://your.proxy:port\n"
                 "       3. raise it before the session, not during it"
                 % last_line(out))


def check_claude_cli():
    path = shutil.which("claude")
    if not path:
        return Check("claude", False, "Claude Code installed",
                     "The go-read step and /build both lean on it. Install:\n"
                     "       npm install -g @anthropic-ai/claude-code    (or see docs.claude.com)",
                     "advisory")
    return Check("claude", True, "Claude Code installed (%s)" % path)


def check_mcp_seam():
    """Advisory. The MCP server and client are stdlib-only and need no
    credential, so a broken clone can be caught here in a fifth of a second
    instead of at the end of Build 2. Never blocks READY: nothing before Build
    2 is where it gets used."""
    script = os.path.join(HERE, "support", "mcp_selftest.py")
    cmd = "python3 support/mcp_selftest.py"
    if not os.path.exists(script):
        return Check("mcp", False, "MCP self-test",
                     "support/mcp_selftest.py is missing, so the clone is incomplete.\n"
                     "     Re-clone before Build 2.", "advisory")
    try:
        r = subprocess.run([sys.executable, script], cwd=HERE, capture_output=True,
                           text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return Check("mcp", False, "MCP self-test: timed out",
                     "Run it on its own and read the last line:\n       %s" % cmd, "advisory")
    except OSError as exc:
        return Check("mcp", False, "MCP self-test: could not run (%s)" % exc,
                     "Run it on its own and read the last line:\n       %s" % cmd, "advisory")
    verdict = last_line(r.stdout or r.stderr)
    if r.returncode == 0:
        return Check("mcp", True, "MCP self-test: %s" % verdict)
    return Check("mcp", False, "MCP self-test: %s" % verdict,
                 "Nothing before Build 2 needs this, so it is not blocking. For the\n"
                 "     failing line and why:\n       %s" % cmd, "advisory")


def check_credential():
    """Advisory only. The live call below is the verdict. An unset API key does
    NOT mean there is no credential: an `ant auth login` profile is invisible
    here on some setups, and federated credentials always are."""
    try:
        from support.client import credential_status
    except Exception as exc:  # noqa: BLE001
        return Check("credential", True, "credential: could not be read (%s)" % exc,
                     "", "advisory")
    mode, detail = credential_status()
    if mode == "offline":
        return Check("credential", False, "credential: OFFLINE MODE (%s)" % detail,
                     "LARKSPUR_OFFLINE=1 is set, and there is no offline path in this pack.\n"
                     "     Every build calls the real API, so this setting only hides the "
                     "problem\n     until the room is watching. Unset it:\n"
                     "       unset LARKSPUR_OFFLINE            (and take it out of .env)")
    if mode == "empty-key":
        return Check("credential", False, "credential: %s" % detail,
                     "An empty key beats every other credential and fails every request.")
    if mode == "unknown":
        return Check("credential", True, "credential: %s" % detail, "", "advisory")
    if "does not start with sk-ant-" in detail or "AWS_REGION is not set" in detail:
        return Check("credential", False, "credential: %s" % detail,
                     "Check for a stray quote, a trailing space, or a truncated paste.")
    return Check("credential", True, "credential: %s" % detail)


NO_CREDENTIAL = (
    "No credential reached the API. Pick whichever you already have:\n"
    "       1. Already sign in to Claude? Use that, no API key needed:\n"
    "            brew install anthropics/tap/ant   (or see docs.claude.com for "
    "Linux/Windows)\n"
    "            ant auth login\n"
    "            ant auth status        # confirms which credential is active\n"
    "       2. Have an API key? cp .env.example .env and paste it in\n"
    "       3. Neither? Ask for one now. Every build calls the API."
)


def check_live_call():
    """The cheapest possible proof that the credential actually works."""
    try:
        from support.client import MissingCredential, get_client
    except Exception as exc:  # noqa: BLE001
        return Check("live", False, "live call to Claude",
                     "support/ did not import (%s: %s). The clone is incomplete, so "
                     "re-clone." % (type(exc).__name__, exc))
    if os.environ.get("LARKSPUR_OFFLINE") == "1":
        return Check("live", False, "live call to Claude",
                     "Skipped, because LARKSPUR_OFFLINE=1 is set. See the credential check "
                     "above:\n     unset it and run again. The live call is the only proof "
                     "that matters here.")
    try:
        client = get_client()
        response = client.messages.create(
            model=MODEL, max_tokens=8,
            messages=[{"role": "user", "content": "Say OK"}],
        )
        text = "".join(getattr(b, "text", "") for b in response.content).strip()
        return Check("live", True, "live call to Claude: %r" % (text or "(empty)"))
    except MissingCredential as exc:
        return Check("live", False, "live call to Claude", str(exc))
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if "resolve authentication" in str(exc).lower() or name == "AuthenticationError":
            hint = NO_CREDENTIAL
        else:
            hint = {
                "APIConnectionError": "Could not reach the API at all. A proxy is probably "
                                      "swallowing it. Try guest wifi or a phone hotspot, and "
                                      "raise it before the day if it stays "
                                      "broken.",
                "PermissionDeniedError": "That credential is valid but has no access to "
                                         "%s." % MODEL,
                "RateLimitError": "Rate limited, because everyone hit the API at once. Wait "
                                  "30s and run this again.",
                "NotFoundError": "Model %s is not available on this credential or region."
                                 % MODEL,
            }.get(name, "Paste this into Claude Code. It has the repo in context.")
        return Check("live", False, "live call to Claude", "%s: %s\n     %s" % (name, exc, hint))


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def run_checks(live=True):
    """Dependency order: this laptop, then git, then the credential. The live
    call runs last and only when nothing above it is broken, so a run that is
    going to fail anyway does not spend a token."""
    checks = [check_python(), check_venv(), check_sdk(), check_writable(),
              check_git_identity(), check_env_ignored(), check_origin(),
              check_claude_cli(), check_mcp_seam(), check_credential()]
    if live and all(c.ok for c in checks if c.severity == "required"):
        checks.append(check_live_call())
    return checks


def do_fix():
    venv = os.path.join(HERE, ".venv")
    python = os.path.join(venv, "Scripts" if os.name == "nt" else "bin", "python")
    if not os.path.exists(python):
        print("creating %s ..." % os.path.relpath(venv, HERE))
        result = subprocess.run([sys.executable, "-m", "venv", venv])
        if result.returncode != 0:
            print("\ncouldn't create a venv with %s" % sys.executable)
            print("try: python3 -m pip install --user virtualenv && python3 -m virtualenv .venv")
            return 1
    print("installing dependencies ...")
    subprocess.run([python, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    result = subprocess.run([python, "-m", "pip", "install", "-q", "-r",
                             os.path.join(HERE, "requirements.txt")])
    if result.returncode != 0:
        print("\ninstall failed. If you're behind a proxy, try:")
        print("  %s -m pip install --proxy http://your.proxy:port -r requirements.txt" % python)
        return 1
    activate = ".venv\\Scripts\\activate" if os.name == "nt" else "source .venv/bin/activate"
    print("\n%s\nDone. Now run these two lines:\n\n  %s\n  python3 setup.py\n%s"
          % (RULE, activate, RULE))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Check this seat is ready to build.")
    parser.add_argument("--fix", action="store_true",
                        help="create .venv and install dependencies")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--no-live", action="store_true", help="skip the live API call")
    args = parser.parse_args()

    if args.fix:
        return do_fix()

    checks = run_checks(live=not args.no_live)
    blocking = [c for c in checks if not c.ok and c.severity == "required"]

    if args.json:
        print(json.dumps({
            "ready": not blocking,
            "blocking": len(blocking),
            "checks": [{"id": c.id, "ok": c.ok, "label": c.label, "fix": c.fix,
                        "severity": c.severity} for c in checks],
        }, indent=2))
        return 0 if not blocking else 1

    print("\n%s\nLARKSPUR SETUP  ·  %s %s\n%s"
          % (RULE, platform.system(), platform.release(), RULE))
    for c in checks:
        mark = "  ✓" if c.ok else ("  !" if c.severity == "advisory" else "  ✗")
        print("%s %s" % (mark, c.label))
        if c.fix and not c.ok:
            for line in c.fix.splitlines():
                print("      %s" % line)

    print("\n" + RULE)
    if not blocking:
        print("READY. That is the whole setup.")
        print("Next: python3 run.py K7PQ2M --trace")
        print(RULE)
        return 0
    print("BLOCKED on %d check(s). Fix the ✗ lines above, top to bottom." % len(blocking))
    print("Then: python3 setup.py")
    print(RULE)
    return 1


if __name__ == "__main__":
    sys.exit(main())
