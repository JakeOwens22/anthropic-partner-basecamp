#!/usr/bin/env python3
"""readout.py: one page that says what your agent IS and what it just DID.

    python3 readout.py                          # uses .workshop/last_trace.json
    python3 readout.py --trace path/to.json     # any saved trace
    python3 readout.py --open                   # write it and open in the browser
    python3 readout.py --client                 # the same evidence, in client language

TWO PAGES, ONE SET OF NUMBERS. The default page is for an engineer: tools,
turns, tokens, stop_reason. `--client` writes readout-client.html from exactly
the same files, for the person who signs. It carries what the agent does, what
it is fenced from doing, what is proved and by which gate, what is not measured
yet, your own `Still broken:` line, and your named account. It invents nothing:
every sentence on it is either sourced from the code, saved in .workshop/, or
typed by you into PITCH.md and ACCOUNT.md.

Two halves, one self-contained HTML file, readout.html at the repo root:

  ARCHITECTURE: read live from your agent.py: every tool and its description
  length, which tools are yours and which are served over MCP, the loop
  ceiling, the prompt sizes. This is the "what we built" half of your team's
  submission.

  ALL FIVE TICKET TYPES: the totals from your last `run.py --all`, if you have
  run one: turns, tool calls and tokens per ticket type, and where a loop ended still
  asking. This is the "prove it generalizes" half.

  THE LOOP: your latest wire trace, drawn as the loop it actually was: each
  API turn, what went up, what came back, which tools fired, and where
  stop_reason finally changed. This is the "prove it ran" half. It is ONE
  conversation: after a `run.py --all` run it is the last of the five ticket types,
  and the page says which ticket that was so nobody reads it as the whole set.

Pushing readout.html is the submission. There is nothing to upload. It is also
the fastest way to explain your agent to another team: one page, no code tour.

It writes readout-trace.json beside it (the trace summary alone) and carries a
copy of the same numbers (plus whichever gates this laptop has saved) inside
the page in a <script id="evidence"> block, so whoever scores it can read a
cloned repo that never had a .workshop/ folder.

Given, like the tracer. Reading it is the point, editing it is not.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT_TRACE = os.path.join(HERE, ".workshop", "last_trace.json")
# Written by `python3 run.py --all`: every Stage 1 shape, plus the totals. The
# single trace below it is one conversation; this is the run that generalizes.
LAST_RUN_PATH = os.path.join(HERE, ".workshop", "last_run.json")
# The readout is the team's submission: it lives at the repo ROOT (committed and
# pushed, unlike .workshop/, which is gitignored). Pushing it IS submitting it.
OUT_PATH = os.path.join(HERE, "readout.html")
# The client half. Same data, different register, same directory, so the two
# pages travel together when the team pushes.
OUT_CLIENT_PATH = os.path.join(HERE, "readout-client.html")
PROFILE_PATH = os.path.join(HERE, ".workshop", "profile.json")
PITCH_PATH = os.path.join(HERE, "PITCH.md")
ACCOUNT_PATH = os.path.join(HERE, "ACCOUNT.md")
EVALS_PATH = os.path.join(HERE, ".workshop", "evals.json")
GIVEN_TOOL_COUNT = 9  # the nine shipped schemas; anything past this is yours


# ---------------------------------------------------------------------------
# The claims dictionary
# ---------------------------------------------------------------------------
# Gate id -> the one sentence a client hears. This is the only place in the
# repo where a saved gate becomes client language, and it is deliberately
# narrow: every sentence is a statement about behavior that the gate actually
# read on the wire. No accuracy, no dollars, no "reliable", no "production
# ready". A gate that saved on a BLOCKED release still gets an honest
# sentence, because "we ran the suite and it blocked" is a stronger claim than
# a screen that is all green.
#
# If you are tempted to make one of these bigger, read what the gate checks
# first. The gate is the warrant for the sentence; the sentence cannot outrun
# it.
CLAIMS = {
    "1.2": "The agent finishes a conversation that needs several system "
           "lookups, instead of stopping halfway through and going quiet.",
    "1.3": "The agent works out which of your systems to ask, and in what "
           "order, without being told which one holds the answer.",
    "1.4": "The same agent handled five different disruption ticket types, "
           "including the two where the correct outcome is to refuse and "
           "hand to a person.",
    "2.1": "We added a capability your customers ask for every storm day, and "
           "the agent reached for it unprompted on a message that never "
           "named it.",
    "2.2": "That capability now runs as a service of its own, so your team "
           "can version and reuse it without anyone touching the agent.",
    "3.1": "There is an instrument. We wrote eval cases with named authors, "
           "made two of them release-blocking, and ran them against this "
           "agent.",
    "4.1": "We declared one number before we touched anything, changed one "
           "thing, and measured the same set again on the same laptop.",
}

# The same seven, at four words each, for the demo panel's gate dots. Kept
# beside CLAIMS on purpose: if a sentence above changes, the dot label changes
# with it. demo/index.html carries a copy of this map and names this file.
CLAIMS_SHORT = {
    "1.2": "Completes the whole conversation",
    "1.3": "Asks the right system",
    "1.4": "Handles five ticket types",
    "2.1": "Reaches for new capability",
    "2.2": "Capability runs as service",
    "3.1": "Eval cases, authored, run",
    "4.1": "One declared number moved",
}

GATE_ORDER = ["1.2", "1.3", "1.4", "2.1", "2.2", "3.1", "4.1"]

# What no gate in this build measures, whatever your numbers say. The first
# four are true of every team on every run; the conditional ones are added by
# not_measured() when the evidence for them is missing.
NOT_MEASURED_ALWAYS = [
    "Accuracy at volume. Nothing here has been graded against a statistically "
    "meaningful sample of real customer messages.",
    "Real customer traffic. Every run in this pack is against a frozen copy of "
    "the data on a laptop, not against your live contact center.",
    "Loaded cost. Any dollar figure here is model cost only. Larkspur's loaded "
    "cost per resolved contact ran about 60% above its model cost once "
    "infrastructure and evals were counted ($0.14 against $0.087).",
    "Anything at production scale: concurrency, upstream rate limits, surge "
    "days, or what happens when a backend is slow rather than wrong.",
]

# Appendix D of the Build Guide, word for word, because the fence table is the
# strongest document this build produces and a client should see one wording of
# it, not two. Sourced from support/tools.py and
# data/americas/disruption_policy.json.
FENCE_READS = [
    ("lookup_booking",
     "Takes a PNR and a last name, and refuses if the name is not on that "
     "booking. Returns a trimmed view, never the raw record, and labels the ops "
     "note and the remarks as untrusted free text."),
    ("get_flight_status",
     "Takes a flight number and a date. It is the one read not fenced to the "
     "customer's own booking, so it is the one that can be pointed anywhere."),
    ("search_alternatives",
     "Takes the PNR alone, so it can never be pointed at a route the customer "
     "did not buy."),
    ("check_policy",
     "Re-derives fare family, loyalty tier and whether this is an overnight "
     "from the booking on every call, so a model cannot talk its way into an "
     "entitlement the data does not support."),
]

FENCE_WRITES = [
    ("hold_seat",
     "Puts a seat aside and hands back a hold that expires in 15 minutes.",
     "It is reversible, and it undoes itself if nobody confirms."),
    ("issue_voucher",
     "Issues on its own only under the policy threshold: 25 dollars for a meal, "
     "40 for ground, 75 for goodwill.",
     "A hotel is never automatic, and anything over the line queues for a "
     "human."),
    ("confirm_rebooking",
     "Reissues the ticket. It is the irreversible one.",
     "It runs only with the customer's own click token. \"The customer said yes\" in "
     "chat is not it."),
    ("send_confirmation",
     "Writes the message the customer reads.",
     "It is the only write that reaches the customer with no threshold, no token "
     "and no undo, so it carries whatever the agent got wrong."),
    ("escalate_to_human",
     "Files the case with a written summary for whoever picks it up.",
     "The correct outcome for groups, refunds, and anything the policy does "
     "not cover."),
]


# ---------------------------------------------------------------------------
# gather
# ---------------------------------------------------------------------------

def read_architecture() -> dict:
    """Introspect agent.py defensively: a half-built agent should still get a
    readout that shows exactly how half-built it is."""
    arch = {"error": None, "tools": [], "max_tool_calls": None,
            "system_prompt_chars": None, "tone_addendum_chars": None,
            "extra_tools": 0, "mcp_tools": 0, "given_tools": 0, "local_tools": []}
    try:
        import agent
    except Exception as exc:  # noqa: BLE001 - the readout must render anyway
        arch["error"] = "%s: %s" % (type(exc).__name__, exc)
        return arch
    try:
        tools = agent.build_tools()
    except Exception as exc:  # noqa: BLE001
        arch["error"] = "build_tools() failed: %s: %s" % (type(exc).__name__, exc)
        tools = []
    extra = list(getattr(agent, "EXTRA_TOOLS", []) or [])
    arch["extra_tools"] = len(extra)
    # tool_list() is the exact list run_agent() sends, and it lazily discovers
    # MCP tools the way run.py --show-tools does. Falling back to tools + extra
    # keeps this working if tool_list() has been renamed away.
    assemble = getattr(agent, "tool_list", None)
    offered = list(assemble()) if callable(assemble) else (tools + extra)
    # Same tag logic as run.py show_tools(), so the readout and --show-tools can
    # never disagree about who serves a tool: a name the MCP client brought back
    # is `via mcp`, a name in EXTRA_TOOLS is `yours`, everything else is given.
    # Asked of the client, not of the agent, and after tool_list() has run: the
    # client's own record of what came over the wire is the honest answer to
    # "which of these are MCP", and it cannot be fooled by where a name sits in
    # the list.
    try:
        from support import mcp_client
        over_mcp = set(getattr(mcp_client, "tool_names", set()) or set())
    except Exception:  # noqa: BLE001 - no client in the clone is not an error
        over_mcp = set()
    extra_names = {t.get("name") for t in extra}
    for t in offered:
        desc = t.get("description", "") or ""
        name = t.get("name", "?")
        mcp = name in over_mcp
        mine = name in extra_names
        arch["tools"].append({
            "name": name,
            "desc_chars": len(desc),
            "desc_head": desc[:110],
            "given": not mcp and not mine,
            "mcp": mcp,
        })
    arch["mcp_tools"] = sum(1 for t in arch["tools"] if t["mcp"])
    arch["given_tools"] = sum(1 for t in arch["tools"] if t["given"])
    arch["max_tool_calls"] = getattr(agent, "MAX_TOOL_CALLS", None)
    prompt = getattr(agent, "SYSTEM_PROMPT", None)
    if prompt is None:
        try:
            from support import prompts  # optional; layout varies
            prompt = getattr(prompts, "SYSTEM_PROMPT", None)
        except Exception:  # noqa: BLE001
            prompt = None
    arch["system_prompt_chars"] = len(prompt) if isinstance(prompt, str) else None
    tone = getattr(agent, "TONE_ADDENDUM", None)
    arch["tone_addendum_chars"] = len(tone.strip()) if isinstance(tone, str) else None
    arch["local_tools"] = sorted((getattr(agent, "LOCAL_TOOLS", {}) or {}).keys())
    return arch


def read_trace(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def read_pod() -> str:
    """The team name off the first line of TEAM.md.

    `# Team: <name>` is what ships now. The older `# Pod:` heading still reads,
    so a repo made from the old template keeps working."""
    team = os.path.join(HERE, "TEAM.md")
    if os.path.exists(team):
        with open(team) as f:
            for line in f:
                head = line.strip().lower()
                if head.startswith("# team") or head.startswith("# pod"):
                    return line.split(":", 1)[-1].strip()
    return ""


def read_last_run() -> dict:
    """The --all aggregate, or {} if nobody has run all five ticket types yet."""
    try:
        with open(LAST_RUN_PATH) as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return {}
    return payload if payload.get("totals") else {}


def read_banked() -> dict:
    """What this laptop has saved, out of the gitignored profile. The page
    carries a copy because .workshop/ never travels with a clone. Without it a
    facilitator grading a pushed repo sees a build with no gates at all."""
    try:
        with open(PROFILE_PATH) as f:
            profile = json.load(f)
    except (OSError, ValueError):
        return {"name": None, "banked": {}}
    return {"name": profile.get("name"),
            "banked": profile.get("banked") or {}}


def read_pitch() -> dict:
    """PITCH.md, parsed by its labels rather than dumped whole.

    The labels are the contract: verify.py reads `Still broken:` and `Lever:`
    by name, so anything that parses this file has to be as forgiving about
    markdown as the gates are. Same shape of regex, same reason: people write
    `**Lever:** cost` and `- Still broken: the tone gate`.
    """
    labels = ["Built", "Does", "Number", "Safety check", "Next", "Still broken",
              "Lever", "Costs", "Wrong", "Runs it", "Left out"]
    out = {}
    try:
        with open(PITCH_PATH) as f:
            body = f.read()
    except OSError:
        return out
    for label in labels:
        pattern = (r"^[ \t>*_-]*\**[ \t]*%s\**[ \t]*:[ \t]*\**[ \t]*(\S.*?)\**[ \t]*$"
                   % label.replace(" ", r"[ _-]?"))
        match = re.search(pattern, body, re.M | re.I)
        if match:
            value = match.group(1).strip()
            # The shipped Lever: line is the list of three choices, not a choice. An unfilled line
            # is not an answer, and putting one on a client page is worse than
            # leaving the row out.
            if value.startswith("<") and value.endswith(">"):
                continue
            out[label] = value
    out["_raw"] = body
    return out


def read_account() -> dict:
    """The three lines of ACCOUNT.md, or {} if nobody filled them in.

    An unfilled field is left out rather than rendered empty: a client page
    with a blank Account row on it says the team did not do the one thing only
    they could do, in front of the person it was for.
    """
    out = {}
    try:
        with open(ACCOUNT_PATH) as f:
            body = f.read()
    except OSError:
        return out
    for label in ("Account", "Workflow", "Date"):
        match = re.search(r"^[ \t>*_-]*\**[ \t]*%s\**[ \t]*:[ \t]*\**[ \t]*(\S.*?)\**[ \t]*$"
                          % label, body, re.M | re.I)
        if match:
            out[label] = match.group(1).strip()
    return out


def read_evals() -> dict:
    """The last eval_harness run, or {}. Used only to say whether an
    instrument exists, never to state a pass rate as a quality claim."""
    try:
        with open(EVALS_PATH) as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return {}
    return payload.get("report") or {}


def read_bench() -> dict:
    """The before/after pair, if both exist. Read for one purpose: deciding
    whether this page is allowed to put a dollar figure on itself."""
    out = {}
    for label in ("before", "after"):
        path = os.path.join(HERE, ".workshop", "bench-%s.json" % label)
        try:
            with open(path) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        out[label] = doc.get("summary") or {}
    return out


def not_measured(evals: dict, bench: dict, banked: dict) -> list:
    """The always-true list, plus whatever this laptop has no evidence for.

    Absence is the finding. A page that quietly omits "we never ran the eval
    suite" is the page that gets a team caught in the room.
    """
    items = list(NOT_MEASURED_ALWAYS)
    if not evals:
        items.append("Whether any answer was correct. There is no eval run on "
                     "this laptop, so nothing here has graded the agent's "
                     "output at all.")
    if not (bench.get("before") and bench.get("after")):
        items.append("Cost and speed. There is no before-and-after bench pair "
                     "on this laptop, so any number about dollars or latency "
                     "would be a guess.")
    if "1.4" not in banked:
        items.append("Whether the loop generalizes past one ticket. The "
                     "five-ticket-type gate has not saved here.")
    return items


def _from_sweep(trace: dict, last_run: dict):
    """The `--all` row this trace came from, or None.

    run.py writes .workshop/last_trace.json on every run, and under --all the
    LAST shape is the one that survives. So the trace sitting next to a sweep is
    usually one arbitrary ticket out of five, and saying "last run" about it
    invites a team to read it as their build. This says which ticket it is, and
    only when the numbers line up with that row.
    """
    rows = (last_run or {}).get("shapes") or []
    if not rows:
        return None
    s = trace.get("summary") or {}
    tokens = s.get("tokens") or {}
    row = rows[-1]
    same = (row.get("turns") == s.get("turns")
            and row.get("tool_calls") == s.get("tool_calls")
            and row.get("tokens_in") == tokens.get("input"))
    return row if same else None


def _trace_label(trace: dict, last_run: dict) -> str:
    row = _from_sweep(trace, last_run)
    if row is None:
        return "trace shown: one conversation, the last one you ran"
    return ("trace shown: %s, the last of the %d ticket types in the sweep"
            % (row.get("pnr", "?"), len((last_run or {}).get("shapes") or [])))


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

CSS = """
body { font: 15px/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #faf7f2; color: #262220; margin: 0; padding: 36px 28px 64px; }
.page { max-width: 880px; margin: 0 auto; }
h1 { font-size: 26px; margin: 0 0 2px; } h1 em { color: #b4562e; font-style: italic; }
h2 { font-size: 15px; letter-spacing: .08em; text-transform: uppercase;
  color: #b4562e; margin: 34px 0 12px; }
.sub { color: #75695f; margin: 0 0 8px; }
.strip { display: flex; gap: 10px; flex-wrap: wrap; margin: 18px 0 6px; }
.stat { background: #fff; border: 1.5px solid #e5dccf; border-top: 4px solid #b4562e;
  border-radius: 8px; padding: 10px 16px; min-width: 108px; }
.stat b { display: block; font-size: 24px; font-weight: 800; }
.stat span { color: #75695f; font-size: 12.5px; }
table { border-collapse: collapse; width: 100%; background: #fff;
  border: 1.5px solid #e5dccf; border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #efe8dc;
  font-size: 13.5px; vertical-align: top; }
th { background: #f4eee4; font-size: 12px; letter-spacing: .05em; text-transform: uppercase;
  color: #75695f; }
.yours td { background: #fdf3ec; }
.pill { display: inline-block; background: #f0e7d9; border-radius: 999px;
  padding: 1px 10px; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
.pill.you { background: #f5dbc9; }
.turn { background: #fff; border: 1.5px solid #e5dccf; border-left: 5px solid #b4562e;
  border-radius: 8px; padding: 12px 16px; margin: 0 0 4px; }
.turn.done { border-left-color: #3d7a52; }
.turn .hd { font-weight: 700; }
.turn .meta { color: #75695f; font-size: 12.5px; margin-top: 2px; }
.tool { font-family: ui-monospace, Menlo, monospace; font-size: 12.5px;
  background: #f4eee4; border-radius: 6px; padding: 4px 10px; margin: 6px 0 0;
  overflow-wrap: anywhere; }
.loopback { color: #b4562e; font-size: 12.5px; margin: 2px 0 6px 18px; }
.stopped { color: #3d7a52; font-weight: 700; }
.warn { background: #fdf3ec; border: 1.5px solid #eac9ae; border-radius: 8px;
  padding: 10px 14px; color: #8a4a22; }
.foot { color: #a2968b; font-size: 12px; margin-top: 40px; }
"""


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def render_all_shapes(out: list, last_run: dict) -> None:
    """The --all totals, above the single trace. One conversation proves the
    loop runs; five ticket types through the same function prove it generalizes, and
    that is the half a sponsor asks about."""
    totals = last_run.get("totals") or {}
    rows = last_run.get("shapes") or []
    out.append("<h2>All five ticket types: what it did across the set</h2>")
    out.append("<p class='sub'>python3 run.py --all · %s</p>"
               % esc(last_run.get("generated", "")))
    out.append("<div class='strip'>")
    for value, label in [
        ("%s/%s" % (totals.get("resolved", "?"), totals.get("shapes", "?")), "returned text"),
        (totals.get("turns", "?"), "API turns, total"),
        (totals.get("tool_calls", "?"), "tool calls, total"),
        ("{:,}".format(totals.get("tokens_in", 0)), "tokens in, total"),
        ("{:,}".format(totals.get("tokens_out", 0)), "tokens out, total"),
    ]:
        out.append("<div class='stat'><b>%s</b><span>%s</span></div>" % (esc(value), esc(label)))
    out.append("</div>")
    if rows:
        out.append("<table><tr><th>pnr</th><th>ticket type</th><th>turns</th><th>tools</th>"
                   "<th>in</th><th>out</th><th>stop_reason</th></tr>")
        for r in rows:
            stop = r.get("stop_reason")
            flag = "" if stop != "tool_use" else " class='yours'"
            out.append("<tr%s><td><span class='pill'>%s</span></td><td>%s</td><td>%s</td>"
                       "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                       % (flag, esc(r.get("pnr", "?")), esc(r.get("shape", "")),
                          esc(r.get("turns", "?")), esc(r.get("tool_calls", "?")),
                          "{:,}".format(r.get("tokens_in", 0) or 0),
                          "{:,}".format(r.get("tokens_out", 0) or 0), esc(stop)))
        out.append("</table>")
    unfinished = totals.get("unfinished") or []
    if unfinished:
        out.append("<p class='warn'>%s ended on stop_reason=tool_use. The loop stopped with "
                   "the model still asking for a tool. That is an unfinished run, not a fast "
                   "one.</p>" % esc(", ".join(unfinished)))


def render(arch: dict, trace: dict, pod: str, trace_path: str, evidence: dict,
           last_run: dict = None) -> str:
    out = ["<!doctype html><meta charset='utf-8'>"]
    out.append("<title>Agent readout%s</title>" % (": " + esc(pod) if pod else ""))
    out.append("<style>%s</style><div class='page'>" % CSS)
    out.append("<h1>Agent <em>readout.</em></h1>")
    sub = "Team %s · " % esc(pod) if pod else ""
    out.append("<p class='sub'>%s%s</p>" % (sub, time.strftime("%Y-%m-%d %H:%M")))

    # -- architecture --------------------------------------------------------
    out.append("<h2>Architecture: what the agent is</h2>")
    if arch["error"]:
        out.append("<p class='warn'>agent.py could not be fully read: %s<br>"
                   "The loop half below still renders. Fix the import and re-run for "
                   "the full picture.</p>" % esc(arch["error"]))
    n_tools = len(arch["tools"])
    shortest = min((t["desc_chars"] for t in arch["tools"]), default=0)
    out.append("<div class='strip'>")
    for value, label in [
        (n_tools, "tools offered"),
        (arch["extra_tools"], "beyond the given %d" % GIVEN_TOOL_COUNT),
        (arch["mcp_tools"], "served over MCP"),
        (arch["max_tool_calls"] if arch["max_tool_calls"] is not None else "?", "loop ceiling"),
        (arch["system_prompt_chars"] if arch["system_prompt_chars"] is not None else "?",
         "system prompt chars"),
        (arch["tone_addendum_chars"] if arch["tone_addendum_chars"] is not None else 0,
         "tone addendum chars"),
    ]:
        out.append("<div class='stat'><b>%s</b><span>%s</span></div>" % (esc(value), esc(label)))
    out.append("</div>")
    if arch["tools"]:
        out.append("<table><tr><th>tool</th><th>description</th><th>chars</th><th></th></tr>")
        for t in arch["tools"]:
            yours = "" if t["given"] else " class='yours'"
            if t.get("mcp"):
                tag = "<span class='pill you'>via mcp</span>"
            elif not t["given"]:
                tag = "<span class='pill you'>yours</span>"
            else:
                tag = ""
            flag = " ⚠" if t["desc_chars"] < 40 else ""
            out.append("<tr%s><td><span class='pill'>%s</span></td><td>%s</td>"
                       "<td>%d%s</td><td>%s</td></tr>"
                       % (yours, esc(t["name"]), esc(t["desc_head"]), t["desc_chars"], flag, tag))
        out.append("</table>")
        if shortest < 40:
            out.append("<p class='sub'>⚠ a description under 40 chars. The description is the "
                       "main routing surface, and the field descriptions inside input_schema "
                       "route too.</p>")
    if arch["mcp_tools"]:
        out.append("<p class='sub'>%d of those %d tools are served by support/mcp_server.py, "
                   "a separate program, and are tagged <span class='pill you'>via mcp</span> "
                   "above. Claude cannot tell: same name, same description, same tokens.</p>"
                   % (arch["mcp_tools"], n_tools))
    if arch["local_tools"]:
        out.append("<p class='sub'>local dispatch: %s</p>"
                   % ", ".join("<span class='pill you'>%s</span>" % esc(n)
                               for n in arch["local_tools"]))

    # -- all five shapes ------------------------------------------------------
    if last_run:
        render_all_shapes(out, last_run)

    # -- the loop -------------------------------------------------------------
    s = trace.get("summary", {})
    tokens = s.get("tokens", {})
    out.append("<h2>The loop: one conversation, turn by turn</h2>")
    sweep_row = _from_sweep(trace, last_run or {})
    if sweep_row is not None:
        where = ("Trace shown: %s (%s), the last of the %d ticket types in the sweep above, "
                 "not a summary of all five · "
                 % (esc(sweep_row.get("pnr", "?")), esc(sweep_row.get("shape", "")),
                    len((last_run or {}).get("shapes") or [])))
    else:
        where = "Trace shown: one conversation, the last one you ran · "
    out.append("<p class='sub'>%strace: %s</p>"
               % (where, esc(os.path.relpath(trace_path, HERE))))
    out.append("<div class='strip'>")
    for value, label in [
        (s.get("turns", "?"), "API turns"),
        (s.get("tool_calls", "?"), "tool calls"),
        ("{:,}".format(tokens.get("input", 0)), "tokens in"),
        ("{:,}".format(tokens.get("output", 0)), "tokens out"),
        ("%ss" % s.get("elapsed", "?"), "wall clock"),
    ]:
        out.append("<div class='stat'><b>%s</b><span>%s</span></div>" % (esc(value), esc(label)))
    out.append("</div>")

    turns = trace.get("turns", [])
    for i, t in enumerate(turns, start=1):
        stop = t.get("stop_reason")
        done = stop != "tool_use"
        usage = t.get("usage") or [0, 0]
        out.append("<div class='turn%s'>" % (" done" if done else ""))
        out.append("<div class='hd'>Turn %d → messages.%s()</div>" % (i, esc(t.get("kind", "?"))))
        out.append("<div class='meta'>sent: %s msg in context · %s tools offered</div>"
                   % (esc(t.get("n_messages", "?")), len(t.get("tools") or [])))
        if t.get("error"):
            out.append("<div class='meta'>← <b>ERROR</b>: %s</div>" % esc(t["error"]))
        else:
            out.append("<div class='meta'>← stop_reason=<b%s>%s</b> · blocks: %s · "
                       "in %s / out %s · %.1fs</div>"
                       % (" class='stopped'" if done else "", esc(stop),
                          esc(", ".join(t.get("blocks") or []) or "-"),
                          "{:,}".format(usage[0]), "{:,}".format(usage[1]),
                          t.get("elapsed") or 0.0))
        for call in t.get("tool_calls") or []:
            args = json.dumps(call.get("input", {}), default=str)
            args = args if len(args) <= 100 else args[:99] + "…"
            out.append("<div class='tool'>⚙ %s(%s)</div>" % (esc(call.get("name", "?")), esc(args)))
        out.append("</div>")
        if not done and i < len(turns):
            out.append("<div class='loopback'>↺ stop_reason=tool_use → results appended → "
                       "loop continues</div>")
    if turns:
        last_stop = turns[-1].get("stop_reason")
        if last_stop == "tool_use":
            out.append("<p class='warn'>The trace ENDS on stop_reason=tool_use. The loop "
                       "stopped before the model was finished. That is the Build 1 bug, "
                       "visible right here.</p>")

    out.append("<p class='foot'>Generated by readout.py · Larkspur Airlines is a fictional "
               "training scenario · Confidential / do not distribute</p></div>")

    # The machine-readable half, travelling INSIDE the page. .workshop/ is
    # gitignored, so a facilitator who clones the team repo has no profile.json
    # and no last_trace.json: this block, and readout-trace.json beside it, are
    # the only evidence that survives a push.
    payload = json.dumps({
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pod": pod or None,
        "banked_by": evidence.get("name"),
        "banked": evidence.get("banked") or {},
        "trace": trace.get("summary") or {},
        "all_shapes": (last_run or {}).get("totals") or None,
    }, default=str)
    out.append("<script type=\"application/json\" id=\"evidence\">%s</script>"
               % payload.replace("</", "<\\/"))
    return "\n".join(out)


# ---------------------------------------------------------------------------

CLIENT_CSS = """
body { font: 15.5px/1.62 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #faf7f2; color: #262220; margin: 0; padding: 40px 28px 72px; }
.page { max-width: 780px; margin: 0 auto; }
h1 { font-size: 27px; margin: 0 0 3px; letter-spacing: -.01em; }
h1 em { color: #b4562e; font-style: italic; }
h2 { font-size: 13px; letter-spacing: .1em; text-transform: uppercase;
  color: #b4562e; margin: 38px 0 12px; }
.sub { color: #75695f; margin: 0 0 6px; font-size: 13.5px; }
.lede { font-size: 17px; line-height: 1.6; margin: 0 0 4px; }
table { border-collapse: collapse; width: 100%; background: #fff;
  border: 1.5px solid #e5dccf; border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 9px 13px; border-bottom: 1px solid #efe8dc;
  font-size: 13.5px; vertical-align: top; }
th { background: #f4eee4; font-size: 11.5px; letter-spacing: .05em;
  text-transform: uppercase; color: #75695f; }
tr:last-child td { border-bottom: none; }
.pill { display: inline-block; background: #f0e7d9; border-radius: 999px;
  padding: 1px 9px; font-family: ui-monospace, Menlo, monospace; font-size: 12px;
  white-space: nowrap; }
.gate { display: flex; gap: 12px; align-items: baseline; background: #fff;
  border: 1.5px solid #e5dccf; border-left: 5px solid #3d7a52; border-radius: 8px;
  padding: 11px 15px; margin: 0 0 6px; }
.gate .id { font-family: ui-monospace, Menlo, monospace; font-size: 12.5px;
  color: #3d7a52; font-weight: 700; flex: 0 0 34px; }
.gate .txt { flex: 1; }
.gate .short { display: block; color: #75695f; font-size: 12px; margin-top: 3px;
  letter-spacing: .04em; text-transform: uppercase; }
ul.plain { margin: 0; padding-left: 20px; }
ul.plain li { margin-bottom: 7px; }
.warn { background: #fdf3ec; border: 1.5px solid #eac9ae; border-radius: 8px;
  padding: 12px 15px; color: #8a4a22; font-size: 14px; }
.broken { background: #fff; border: 1.5px solid #e5dccf; border-left: 5px solid #b4562e;
  border-radius: 8px; padding: 13px 16px; font-size: 15px; }
.broken b { display: block; font-size: 11.5px; letter-spacing: .1em;
  text-transform: uppercase; color: #b4562e; margin-bottom: 4px; }
.acct { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 2px; }
.acct div { background: #fff; border: 1.5px solid #e5dccf; border-top: 4px solid #b4562e;
  border-radius: 8px; padding: 10px 16px; min-width: 150px; }
.acct span { display: block; color: #75695f; font-size: 11.5px; letter-spacing: .05em;
  text-transform: uppercase; margin-bottom: 3px; }
.acct b { font-size: 15px; font-weight: 700; }
.foot { color: #a2968b; font-size: 12px; margin-top: 44px; }
"""


def client_lede(arch: dict, pitch: dict) -> str:
    """One paragraph, in the client's words, about what this agent does.

    The first sentence is the team's own `Does:` line if they wrote one, because
    nothing this script can generate beats the sentence they will actually say
    out loud. The rest is counted from agent.py, so the paragraph cannot claim a
    capability the file does not offer.
    """
    n_tools = len(arch["tools"])
    parts = []
    does = pitch.get("Does")
    if does:
        parts.append(does.rstrip(".") + ".")
    else:
        parts.append("This agent handles a Larkspur disruption chat: it reads the "
                     "customer's own booking, checks the flight, derives what they "
                     "are entitled to from Larkspur's policy table, and offers "
                     "alternatives it can hold while they decide.")
    if n_tools:
        line = ("It reaches flight status, reservations and the policy table through "
                "%d named tools, one tool per thing it is allowed to ask for" % n_tools)
        if arch["mcp_tools"]:
            line += (", and %d of those %d run in a separate program your own team can "
                     "version and reuse" % (arch["mcp_tools"], n_tools))
        parts.append(line + ".")
    parts.append("It is chat only. Voice, refunds and partner-airline segments are "
                 "out of scope by agreement, and it hands those to a person with the "
                 "conversation attached.")
    return " ".join(parts)


def render_client(arch: dict, pod: str, evidence: dict, pitch: dict, account: dict,
                  evals: dict, bench: dict, last_run: dict) -> str:
    banked = evidence.get("banked") or {}
    out = ["<!doctype html><meta charset='utf-8'>"]
    out.append("<title>Larkspur disruption agent%s</title>"
               % (": " + esc(pod) if pod else ""))
    out.append("<style>%s</style><div class='page'>" % CLIENT_CSS)
    out.append("<h1>The disruption agent, <em>in plain terms.</em></h1>")
    who = "Team %s · " % esc(pod) if pod else ""
    if evidence.get("name"):
        who += "%s · " % esc(evidence["name"])
    out.append("<p class='sub'>%s%s</p>" % (who, time.strftime("%Y-%m-%d %H:%M")))

    # -- what it does ---------------------------------------------------------
    out.append("<h2>What it does</h2>")
    out.append("<p class='lede'>%s</p>" % esc(client_lede(arch, pitch)))
    if arch["error"]:
        out.append("<p class='warn'>agent.py could not be fully read on this laptop, "
                   "so the tool counts above are incomplete. Regenerate this page "
                   "before you show it to anybody.</p>")

    # -- what it cannot do ----------------------------------------------------
    out.append("<h2>What it cannot do, and what stops it</h2>")
    out.append("<p class='sub'>The page to hand a CIO. Every line is sourced from the "
               "code and the policy table, not from a slide. Reads first, then the "
               "five things it can change and the fence on each.</p>")
    out.append("<table><tr><th>Reads</th><th>What it does</th></tr>")
    for name, what in FENCE_READS:
        out.append("<tr><td><span class='pill'>%s</span></td><td>%s</td></tr>"
                   % (esc(name), esc(what)))
    out.append("</table>")
    out.append("<table style='margin-top:12px'><tr><th>Writes</th><th>What it does</th>"
               "<th>The fence</th></tr>")
    for name, what, fence in FENCE_WRITES:
        out.append("<tr><td><span class='pill'>%s</span></td><td>%s</td><td>%s</td></tr>"
                   % (esc(name), esc(what), esc(fence)))
    out.append("</table>")

    # -- what is proved -------------------------------------------------------
    out.append("<h2>What is proved, and by which check</h2>")
    proved = [g for g in GATE_ORDER if g in banked]
    if proved:
        out.append("<p class='sub'>%d of %d checks saved on this laptop. Each check reads "
                   "what the agent did on the wire, not how it was written, so the sentence "
                   "beside it is the widest claim that check supports.</p>"
                   % (len(proved), len(GATE_ORDER)))
        for gate in proved:
            out.append("<div class='gate'><span class='id'>%s</span><span class='txt'>%s"
                       "<span class='short'>%s · evidence %s</span></span></div>"
                       % (esc(gate), esc(CLAIMS[gate]), esc(CLAIMS_SHORT[gate]),
                          esc(banked.get(gate) or "saved")))
    else:
        out.append("<p class='warn'>No checks have been saved on this laptop, so there is "
                   "nothing on this page anybody should treat as proved. Run "
                   "<span class='pill'>python3 verify.py 1.2</span> and regenerate.</p>")
    if evals:
        blocking = evals.get("blocking_suites") or []
        scored = evals.get("scored") or evals.get("cases")
        out.append("<p class='sub'>The eval run behind check 3.1: %s of %s scored cases "
                   "passed, release %s. A blocked release is a working instrument, and it "
                   "is the reason this page can say what it says.</p>"
                   % (esc(evals.get("passed", "?")), esc(scored),
                      "BLOCKED by " + esc(", ".join(blocking)) if blocking else "clear"))

    # -- what is not measured -------------------------------------------------
    out.append("<h2>What is not measured yet</h2>")
    out.append("<ul class='plain'>")
    for item in not_measured(evals, bench, banked):
        out.append("<li>%s</li>" % esc(item))
    out.append("</ul>")
    if bench.get("before") and bench.get("after"):
        out.append("<p class='sub'>There is a before-and-after bench pair on this laptop. "
                   "Whatever it says is model cost and laptop latency on a frozen copy of the data. Put "
                   "the caveat above on the same slide as the number.</p>")

    # -- still broken ---------------------------------------------------------
    broken = pitch.get("Still broken")
    if broken:
        out.append("<h2>What we are telling you before you find it</h2>")
        out.append("<div class='broken'><b>Still broken</b>%s</div>" % esc(broken))

    # -- the four questions ---------------------------------------------------
    answered = [(q, pitch[key]) for key, q in
                (("Costs", "What it costs"), ("Wrong", "When it is wrong"),
                 ("Runs it", "Who runs it"), ("Left out", "What you left out"))
                if pitch.get(key)]
    if answered:
        out.append("<h2>Your four questions</h2>")
        out.append("<table><tr><th>You asked</th><th>Our answer</th></tr>")
        for question, answer in answered:
            out.append("<tr><td>%s</td><td>%s</td></tr>" % (esc(question), esc(answer)))
        out.append("</table>")

    # -- the account ----------------------------------------------------------
    if account:
        out.append("<h2>Where this goes next</h2>")
        out.append("<div class='acct'>")
        for label in ("Account", "Workflow", "Date"):
            if account.get(label):
                out.append("<div><span>%s</span><b>%s</b></div>"
                           % (esc(label), esc(account[label])))
        out.append("</div>")

    out.append("<p class='foot'>Generated by readout.py --client from this repository's own "
               "evidence · Larkspur Airlines is a fictional training scenario · "
               "Confidential / do not distribute</p></div>")

    payload = json.dumps({
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pod": pod or None,
        "banked_by": evidence.get("name"),
        "banked": banked,
        "claims": {g: CLAIMS[g] for g in proved},
        "claims_short": {g: CLAIMS_SHORT[g] for g in proved},
        "still_broken": broken or None,
        "account": account or None,
        "all_shapes": (last_run or {}).get("totals") or None,
    }, default=str)
    out.append("<script type=\"application/json\" id=\"evidence\">%s</script>"
               % payload.replace("</", "<\\/"))
    return "\n".join(out)


def main_client(args) -> int:
    """The client page. It needs no trace: it is a claim page, not a run page,
    so it renders off the architecture, the saved gates and the team's own
    words. That is deliberate. A team that has saved nothing gets a page that
    says so, which is the most useful version of this page they could hold."""
    out_path = args.out or OUT_CLIENT_PATH
    arch = read_architecture()
    pod = read_pod()
    evidence = read_banked()
    pitch = read_pitch()
    account = read_account()
    evals = read_evals()
    bench = read_bench()
    last_run = read_last_run()

    page = render_client(arch, pod, evidence, pitch, account, evals, bench, last_run)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write(page)

    banked = sorted((evidence.get("banked") or {}).keys())
    print("wrote %s" % os.path.relpath(out_path, HERE))
    print("  claims on the page: %d of %d gates%s"
          % (len(banked), len(GATE_ORDER),
             (" (" + ", ".join(banked) + ")") if banked else " (none saved here yet)"))
    for gate in GATE_ORDER:
        if gate in banked:
            print("    %-4s %s" % (gate, CLAIMS_SHORT[gate]))
    print("  Still broken: %s" % (pitch.get("Still broken") or "NOT WRITTEN in PITCH.md"))
    four = [k for k in ("Costs", "Wrong", "Runs it", "Left out") if pitch.get(k)]
    print("  Priya's four questions: %d of 4 answered in PITCH.md%s"
          % (len(four), (" (" + ", ".join(four) + ")") if four else ""))
    if account:
        print("  named account: %s"
              % " · ".join("%s %s" % (k.lower(), account[k])
                           for k in ("Account", "Workflow", "Date") if account.get(k)))
    else:
        print("  named account: ACCOUNT.md is empty. Three lines, and nobody else "
              "can write them.")
    print("  not measured yet: %d item(s) listed on the page"
          % len(not_measured(evals, bench, evidence.get("banked") or {})))
    if args.open:
        webbrowser.open("file://" + os.path.abspath(out_path))
    return 0


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Render the agent architecture + loop readout.")
    parser.add_argument("--trace", default=DEFAULT_TRACE, help="saved trace JSON")
    parser.add_argument("--out", default=None)
    parser.add_argument("--open", action="store_true", help="open the result in a browser")
    parser.add_argument("--client", action="store_true",
                        help="write readout-client.html: the same evidence, client language")
    args = parser.parse_args()

    if args.client:
        return main_client(args)
    args.out = args.out or OUT_PATH

    if not os.path.exists(args.trace):
        print("No trace at %s" % os.path.relpath(args.trace, HERE))
        print("Run the agent once first:  python3 run.py <PNR> --trace")
        return 1

    arch = read_architecture()
    trace = read_trace(args.trace)
    pod = read_pod()
    evidence = read_banked()
    last_run = read_last_run()
    page = render(arch, trace, pod, args.trace, evidence, last_run)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(page)

    # The same summary, on its own, beside the page. A grader that wants the
    # numbers should not have to parse HTML to get them.
    side = os.path.join(os.path.dirname(os.path.abspath(args.out)) or ".",
                        "readout-trace.json")
    with open(side, "w") as f:
        json.dump(trace.get("summary") or {}, f, indent=2, default=str)

    s = trace.get("summary", {})
    print("wrote %s" % os.path.relpath(args.out, HERE))
    print("wrote %s" % os.path.relpath(side, HERE))
    print("  architecture: %d tools (%d given, %d yours, %d via mcp), loop ceiling %s"
          % (len(arch["tools"]), arch["given_tools"], arch["extra_tools"],
             arch["mcp_tools"], arch["max_tool_calls"]))
    print("  %s: %s turns, %s tool calls, %s in / %s out"
          % (_trace_label(trace, last_run),
             s.get("turns", "?"), s.get("tool_calls", "?"),
             "{:,}".format(s.get("tokens", {}).get("input", 0)),
             "{:,}".format(s.get("tokens", {}).get("output", 0))))
    if last_run:
        t = last_run.get("totals") or {}
        print("  all five ticket types: %s/%s returned text, %s turns, %s tool calls, %s in / %s out"
              % (t.get("resolved", "?"), t.get("shapes", "?"), t.get("turns", "?"),
                 t.get("tool_calls", "?"), "{:,}".format(t.get("tokens_in", 0)),
                 "{:,}".format(t.get("tokens_out", 0))))
    else:
        print("  all five ticket types: not on the page (run python3 run.py --all to add them)")
    banked = sorted((evidence.get("banked") or {}).keys())
    print("  embedded evidence: gates %s%s"
          % (", ".join(banked) or "none saved on this laptop",
             " (%s)" % evidence["name"] if evidence.get("name") else ""))
    if args.open:
        webbrowser.open("file://" + os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
