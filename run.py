#!/usr/bin/env python3
"""run.py: drive your agent and watch what it does.

    python3 run.py K7PQ2M                # the plain tool loop (step 1.2)
    python3 run.py K7PQ2M --trace        # same, with the wire trace
    python3 run.py K7PQ2M --trace -v     # same, with the whole of every tool result
    python3 run.py --show-tools          # step 1.3: what Claude sees about your tools
    python3 run.py --tool-tax            # what those schemas cost on every turn
    python3 run.py --all --trace         # every Stage 1 ticket type (step 1.4)

--trace prints the wire: every API turn, the parameters you sent, the blocks
that came back, the tools Claude picked, what each tool answered, and what it
cost. Read it. The trace is the lesson. The answer is the by-product.

Tool results print under the call that asked for them, shortened to a headline.
-v prints the whole recorded result, which is what you want when a tool answered
with an error about its own arguments.

Every run also writes the trace to .workshop/last_trace.json, with or without
--trace, and under --all it is the LAST of the five that survives. That file is
what `python3 readout.py` turns into the team's one-page readout, so a run you
never made is a readout you cannot render.

--all also writes .workshop/last_run.json: one row per ticket type plus the
totals, because "it worked on K7PQ2M" and "it worked on all five" are different
claims and only the second one is worth putting on a readout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import agent  # noqa: E402
from support import LAST, DEFAULT_LAST_NAME, DEFAULT_PNR, STAGE1_TASKS  # noqa: E402

DEFAULT_MESSAGE = "My flight was disrupted. Can you help me figure out what happens next?"


def _last_name_for(pnr: str):
    for task in STAGE1_TASKS:
        if task["pnr"] == pnr:
            return task["last_name"]
    return None


def run_one(pnr: str, last_name: str, message: str, trace: bool, shape: str = "",
            verbose: bool = False) -> dict:
    """Runs one conversation and returns the row --all aggregates.

    Returns None only when there is nothing to aggregate: the agent raised
    NotImplementedError (the unbuilt state, not a measurement) or no tracer was
    ever created. A run that BROKE still returns a row, carrying its exception,
    because a shape that crashed is a fact about this loop and dropping it
    silently is how a three-row table ends up under a five-shape headline."""
    print(f"\n=== {pnr} ({last_name}) ===")
    result, failure = "", None
    try:
        result = agent.run_agent(pnr, last_name, message)
    except NotImplementedError as exc:
        print(f"\n[--] {exc}\n     That's the exercise, not a bug. Build it, then re-run.")
        return None
    except Exception as exc:  # noqa: BLE001
        # A run that raised is still a run that happened, and the tracer already
        # recorded the turn that broke. One line here, then straight on to the
        # trace: the message the API sent back is a statement about the
        # conversation this agent assembled, and the trace is where you read it.
        #
        # NOT truncated. This banner carries the API's whole message, because
        # the sentence that names what the API could not accept is usually the
        # second one, and the step above this says to read the error verbatim.
        failure = f"{type(exc).__name__}: {exc}"
        print(f"\n[!!] {failure}")

    tracer = LAST.tracer
    if failure is not None:
        print(
            "\n  The run stopped on that instead of answering. The trace below ends on the\n"
            "  turn that broke, marked ERROR, and the turns before it are the ones that\n"
            "  set it up. Read the error as a statement about the conversation that went\n"
            "  out, not about your Python."
        )
    elif result:
        print(f"\n{result}")
    else:
        print(
            "\n(nothing)\n\n"
            "  Claude didn't send any text. Read the LAST turn in the trace below.\n"
            "  If it says stop_reason=tool_use, Claude asked for a tool and the\n"
            "  conversation ended before anyone answered it. If it says end_turn, the\n"
            "  conversation did finish, properly, with text, so the question is which\n"
            "  response's text this function handed back."
        )

    if tracer is None:
        return None

    # Saved on EVERY run, traced or not. readout.py reads exactly this file, and
    # under --all the last shape to run is the one that survives here.
    saved, save_error = None, None
    try:
        saved = tracer.save(os.path.join(HERE, ".workshop", "last_trace.json"))
    except OSError as exc:
        save_error = f"{type(exc).__name__}: {exc}"

    s = tracer.summary()
    where = os.path.relpath(saved, HERE) if saved else f"not saved ({save_error})"
    if failure is not None or trace:
        # The wire is the whole point of a broken run, so it prints with or
        # without --trace.
        print("\n" + tracer.render(verbose=verbose))
        print(f"\n  trace → {where}   (python3 readout.py renders this one)")
    else:
        print(
            f"\n  [{s['turns']} turns · {s['tool_calls']} tool calls · "
            f"{s['tokens']['output']} out tokens · {s['elapsed']}s · trace → {where}]"
            f"  add --trace to see the wire"
        )

    tokens = s.get("tokens") or {}
    return {
        "pnr": pnr,
        "shape": shape,
        "turns": s.get("turns", 0),
        "tool_calls": s.get("tool_calls", 0),
        "tokens_in": tokens.get("input", 0),
        "tokens_out": tokens.get("output", 0),
        "cache_read": tokens.get("cache_read", 0),
        # The last turn's stop_reason is the one that says whether the loop
        # closed or just ran out of road. tool_use here is an unfinished run.
        "stop_reason": tracer.turns[-1].get("stop_reason") if tracer.turns else None,
        "elapsed": s.get("elapsed"),
        "resolved": bool(result) and failure is None,
        "error": failure,
    }


def run_all(message: str, trace: bool, verbose: bool = False) -> None:
    """Every Stage 1 shape through the same function, then the totals. A loop
    that works on one ticket and nowhere else is a loop tuned to one ticket, and
    the footer is where that stops being an opinion."""
    rows, unbuilt = [], []
    for t in STAGE1_TASKS:
        row = run_one(t["pnr"], t["last_name"], message, trace, t["shape"], verbose)
        if row:
            rows.append(row)
        else:
            unbuilt.append(t)
    if not rows:
        return

    broke = [r for r in rows if r.get("error")]
    totals = {
        "attempted": len(STAGE1_TASKS),
        "shapes": len(rows),
        "failed": len(broke),
        "resolved": sum(1 for r in rows if r["resolved"]),
        "turns": sum(r["turns"] for r in rows),
        "tool_calls": sum(r["tool_calls"] for r in rows),
        "tokens_in": sum(r["tokens_in"] for r in rows),
        "tokens_out": sum(r["tokens_out"] for r in rows),
        "cache_read": sum(r["cache_read"] for r in rows),
        "unfinished": [r["pnr"] for r in rows if r["stop_reason"] == "tool_use"],
        "errors": [{"pnr": r["pnr"], "shape": r["shape"], "error": r["error"]} for r in broke],
    }

    path = os.path.join(HERE, ".workshop", "last_run.json")
    where = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "shapes": rows, "totals": totals}, fh, indent=2, default=str)
        where = os.path.relpath(path, HERE)
    except OSError as exc:
        where = f"not saved ({type(exc).__name__}: {exc})"

    print("\n" + "=" * 66)
    if totals["shapes"] == totals["attempted"] and not broke:
        print("ALL FIVE STAGE 1 TICKET TYPES")
    else:
        # The banner counts what ran. A headline that says five over a table of
        # three is the one claim this footer exists to stop.
        print("STAGE 1 TICKET TYPES: %d of %d ran%s"
              % (totals["shapes"], totals["attempted"],
                 ", %d of those broke" % totals["failed"] if broke else ""))
    print("=" * 66)
    print("  %-8s %-30s %5s %5s %9s %9s  %s"
          % ("pnr", "ticket type", "turns", "tools", "in", "out", "stop_reason"))
    for r in rows:
        if r.get("error"):
            print("  %-8s %-30s %5d %5d %9s %9s  FAILED %s"
                  % (r["pnr"], r["shape"][:30], r["turns"], r["tool_calls"],
                     "{:,}".format(r["tokens_in"]), "{:,}".format(r["tokens_out"]),
                     r["error"].split(":")[0]))
            continue
        print("  %-8s %-30s %5d %5d %9s %9s  %s"
              % (r["pnr"], r["shape"][:30], r["turns"], r["tool_calls"],
                 "{:,}".format(r["tokens_in"]), "{:,}".format(r["tokens_out"]),
                 r["stop_reason"]))
    for t in unbuilt:
        print("  %-8s %-30s %5s %5s %9s %9s  DID NOT RUN"
              % (t["pnr"], t["shape"][:30], "-", "-", "-", "-"))
    print("  " + "-" * 64)
    print("  %-8s %-30s %5d %5d %9s %9s  %d/%d returned text"
          % ("TOTAL", "%d ticket types" % totals["shapes"], totals["turns"], totals["tool_calls"],
             "{:,}".format(totals["tokens_in"]), "{:,}".format(totals["tokens_out"]),
             totals["resolved"], totals["attempted"]))
    if totals["cache_read"]:
        print("  cache read: %s tokens" % "{:,}".format(totals["cache_read"]))
    if totals["unfinished"]:
        print("  ! ended on stop_reason=tool_use (the loop stopped mid-ask): %s"
              % ", ".join(totals["unfinished"]))
    for f in totals["errors"]:
        print("  ! %s (%s) raised: %s" % (f["pnr"], f["shape"], f["error"]))
    print("  totals → %s   (python3 readout.py puts these on the page)" % where)
    print("=" * 66)


def _offered():
    """(the list that goes out on every turn, names served over MCP, names of
    yours). tool_list() is what run_agent actually sends, so this cannot drift
    from the wire. It only reaches the MCP server if the agent's own tool_list()
    reaches it, which is why nothing here starts a server on day one."""
    given = agent.build_tools()
    extra = list(getattr(agent, "EXTRA_TOOLS", []) or [])
    assemble = getattr(agent, "tool_list", None)
    offered = list(assemble() if callable(assemble) else given + extra)
    # Asked of the client, not of the agent: the client's own record of what
    # came over the wire is the honest answer to "which of these are MCP".
    try:
        from support import mcp_client
        over_mcp = set(getattr(mcp_client, "tool_names", set()) or set())
    except Exception:  # noqa: BLE001 - no client in the clone is not an error
        over_mcp = set()
    return offered, over_mcp, {t["name"] for t in extra}


def _schema_lines(schema: dict) -> list:
    """The input_schema, as the fields Claude is actually told about. The
    description on a tool routes the choice; these route the arguments."""
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    lines = []
    for field, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        kind = spec.get("type", "?")
        if spec.get("enum"):
            kind = "%s, one of %s" % (kind, ", ".join(str(e) for e in spec["enum"]))
        mark = "required" if field in required else "optional"
        desc = spec.get("description")
        lines.append("    %-28s (%s, %s)%s"
                     % (field, kind, mark, "  %r" % desc if desc else ""))
    return lines


def show_tools() -> None:
    offered, over_mcp, extra_names = _offered()
    print("\nWHAT CLAUDE ACTUALLY RECEIVES ABOUT YOUR TOOLS")
    print("=" * 66)
    for t in offered:
        desc = t.get("description", "")
        if t["name"] in over_mcp:
            mine = "  via mcp"
        elif t["name"] in extra_names:
            mine = " + yours"
        else:
            mine = ""
        flag = f"  <-- {len(desc)} characters" if len(desc) < 40 else ""
        print(f"\n{t['name']}{mine}{flag}")
        print(f"  {desc!r}")
        for line in _schema_lines(t.get("input_schema")):
            print(line)
    print("\n" + "=" * 66)
    n_mcp = sum(1 for t in offered if t["name"] in over_mcp)
    n_extra = sum(1 for t in offered if t["name"] in extra_names)
    added = []
    if n_extra:
        added.append("%d of yours" % n_extra)
    if n_mcp:
        added.append("%d over MCP" % n_mcp)
    print("%d tool(s) offered on every call: the given nine%s."
          % (len(offered),
             " plus " + " and ".join(added) if added else ", none of yours yet"))
    if n_mcp:
        print("%d of those are served by support/mcp_server.py, a separate program.\n"
              "Claude cannot tell. Same name, same description, same tokens."
              % n_mcp)
    print("Read that back and ask: could you do this job from that briefing?\n"
          "The indented fields are part of it, and this is all of it. The tool's\n"
          "description decides whether it gets picked; a field's description\n"
          "decides what gets sent, so a field described wrong reads as a routing\n"
          "failure. A field with nothing quoted after it tells Claude its name\n"
          "and its type and nothing else, which is often fine and sometimes not.\n"
          "Say the answer out loud first, in your own words, then put it in\n"
          "build_tools() (or EXTRA_TOOLS, for the ones you added).")


def tool_tax() -> None:
    """What the schemas cost on every turn, per tool, counted rather than sampled.

    Tokens in on a live conversation moves with what the model chose to do.
    This does not move at all, which is why it is the number to put beside a
    claim about what a tool list costs.
    """
    from support import MODEL, get_client
    from support.trace import tool_schema_tokens

    offered, over_mcp, extra_names = _offered()
    if not offered:
        print("\nNo tools are being offered, so there is nothing to price.")
        return
    try:
        client = get_client()
    except Exception as exc:  # noqa: BLE001
        print("\nNo client, so these are estimates: %s: %s" % (type(exc).__name__, exc))
        client = None
    total, per, how = tool_schema_tokens(offered, client, MODEL if client else None,
                                         per_tool=True)

    print("\nWHAT YOUR TOOL LIST COSTS, ON EVERY TURN")
    print("=" * 66)
    print("  %-28s %9s  %s" % ("tool", "if dropped", "owner"))
    for t in offered:
        name = t["name"]
        owner = "mcp" if name in over_mcp else ("yours" if name in extra_names else "given")
        print("  %-28s %9s  %s" % (name, "{:,}".format(per.get(name, 0)), owner))
    print("  " + "-" * 64)
    print("  %-28s %9s  %d tool(s)" % ("WHOLE LIST", "{:,}".format(total), len(offered)))
    print("=" * 66)
    if how == "counted":
        print("Counted on the wire, not sampled: the same list prices the same every\n"
              "time. Every one of those tokens rides on every turn of every\n"
              "conversation, whether the tool fires or not.\n"
              "The per-tool column is what dropping that one tool would save,\n"
              "measured with the others still in the list. It does not add up to the\n"
              "whole-list figure, because a tokenizer does not decompose.")
    else:
        print("ESTIMATED from the JSON at four characters per token, because the token\n"
              "counter could not be reached. Label it an estimate wherever you use it.")
    n_mcp = sum(1 for t in offered if t["name"] in over_mcp)
    if n_mcp:
        print("%d of these are served by a separate program. Moving a tool onto a\n"
              "server changes who maintains it. It does not change this table."
              % n_mcp)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pnr", nargs="?", help="PNR to run, e.g. K7PQ2M")
    parser.add_argument("--last-name", help="required if pnr isn't one of the Stage 1 tasks")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print the whole of every tool result on the trace")
    parser.add_argument("--show-tools", action="store_true")
    parser.add_argument("--tool-tax", action="store_true",
                        help="token-count every schema on the wire, per tool")
    parser.add_argument("--all", action="store_true", help="run all five Stage 1 PNRs")
    parser.add_argument("--offline", action="store_true", help="(removed) there is no offline mode")
    args = parser.parse_args()

    if args.offline:
        print("There is no offline mode in this pack. No network means a raised hand")
        print("and a teammate's screen. Raise it in the room.")
        return 1

    if args.show_tools:
        show_tools()
        return 0

    if args.tool_tax:
        tool_tax()
        return 0

    if args.all:
        run_all(args.message, args.trace, args.verbose)
        return 0

    pnr = args.pnr or DEFAULT_PNR
    last_name = args.last_name or _last_name_for(pnr) or DEFAULT_LAST_NAME
    run_one(pnr, last_name, args.message, args.trace, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
