#!/usr/bin/env python3
"""Prove the MCP server and client actually work, before anyone builds on them.

    python3 support/mcp_selftest.py
    python3 support/mcp_selftest.py --trace     # and watch the wire

It starts the server as a subprocess, runs the full lifecycle both ways it can
be opened, lists the tools, calls both of them with a question the fixtures
genuinely answer, and checks the shapes. Every line prints PASS or FAIL, and
the exit code is 0 only when every line says PASS.

No API key. No network. Nothing here talks to Claude.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXERCISE_ROOT = HERE.parent
if str(EXERCISE_ROOT) not in sys.path:
    sys.path.insert(0, str(EXERCISE_ROOT))

from support import mcp_client  # noqa: E402
from support.mcp_client import MCPError, MCPServer  # noqa: E402

PASSES = 0
FAILURES = 0


def check(ok: bool, label: str, detail: str = "") -> bool:
    global PASSES, FAILURES
    if ok:
        PASSES += 1
        print("PASS  %s%s" % (label, ("   %s" % detail) if detail else ""))
    else:
        FAILURES += 1
        print("FAIL  %s%s" % (label, ("   %s" % detail) if detail else ""))
    return ok


def run(trace: bool) -> int:
    timings = {}

    print("\nMCP SELFTEST")
    print("=" * 74)
    print("server command: %s" % " ".join(mcp_client.DEFAULT_SERVER_CMD))
    print("-" * 74)

    # -- 1. modern lifecycle: server/discover, no handshake ----------------
    t0 = time.time()
    mcp = MCPServer(mcp_client.DEFAULT_SERVER_CMD, trace=trace)
    try:
        mcp.start()
    except Exception as exc:  # noqa: BLE001
        check(False, "server starts", "%s: %s" % (type(exc).__name__, exc))
        print("\nNothing else can run until the server starts. Stopping.")
        return 1
    timings["start + discover"] = time.time() - t0

    check(mcp.running, "server starts", "pid %s" % (mcp.proc.pid if mcp.proc else "?"))
    check(mcp.era == "modern", "auto probe picked the modern era", "era=%s" % mcp.era)
    check(bool(mcp.protocol_version), "protocol version agreed",
          "protocolVersion=%s" % mcp.protocol_version)
    check(mcp.server_info.get("name") == "larkspur-ops",
          "server/discover returned serverInfo", "name=%r" % mcp.server_info.get("name"))
    check(len(mcp.instructions) > 40, "server/discover returned instructions",
          "%d chars" % len(mcp.instructions))

    # -- 2. ping -----------------------------------------------------------
    try:
        check(mcp.ping(), "ping answered")
    except Exception as exc:  # noqa: BLE001
        check(False, "ping answered", "%s: %s" % (type(exc).__name__, exc))

    # -- 3. tools/list -----------------------------------------------------
    t0 = time.time()
    raw = mcp.list_tools()
    timings["tools/list"] = time.time() - t0
    names = [t["name"] for t in raw]
    check(set(names) == {"next_available_day", "fare_rules"},
          "tools/list returned both tools", ", ".join(names))
    check(mcp.tool_names == names, "tool_names tracks what came over MCP",
          str(mcp.tool_names))

    for tool in raw:
        desc = tool.get("description", "")
        check(len(desc) >= 40, "%s description is a real briefing" % tool["name"],
              "%d chars" % len(desc))
        schema = tool.get("inputSchema") or {}
        check(schema.get("type") == "object" and bool(schema.get("properties")),
              "%s inputSchema is an object with properties" % tool["name"])
        undescribed = [k for k, v in (schema.get("properties") or {}).items()
                       if not (isinstance(v, dict) and v.get("description"))]
        check(not undescribed, "%s field descriptions are all present" % tool["name"],
              "missing: %s" % ", ".join(undescribed) if undescribed else "")

    # -- 4. discover() is Anthropic-shaped ---------------------------------
    schemas = mcp.discover()
    shaped = all(set(s) == {"name", "description", "input_schema"} for s in schemas)
    check(shaped, "discover() renames inputSchema to input_schema",
          "keys: %s" % ", ".join(sorted(schemas[0])) if schemas else "no tools")

    # -- 5. tools/call, the real question ---------------------------------
    # DEN to BOI is J5NU8S's shape: two passengers stranded at Denver on a
    # cancelled Boise flight. The fixtures answer it truthfully, so a wrong
    # answer here is a real failure and not a missing row.
    t0 = time.time()
    answer = mcp.call("next_available_day",
                      {"origin": "DEN", "dest": "BOI", "date": "2025-05-06", "cabin": "Y"})
    timings["tools/call"] = time.time() - t0
    check("2025-05-09" in answer, "next_available_day DEN to BOI after 2025-05-06",
          answer.strip()[:110])
    check(not answer.startswith("error:"), "that call was not a tool error")

    section = mcp.call("fare_rules", {"section": "6"})
    check("Care while you wait" in section, "fare_rules('6') returned the right section",
          section.splitlines()[0] if section else "empty")
    check("meal credit" in section, "fare_rules returned the section body, not just a heading")

    by_title = mcp.call("fare_rules", {"section": "care while you wait"})
    check(by_title == section, "fare_rules matches on title words too")

    # -- 6. errors: the two kinds, kept apart -----------------------------
    bad_date = mcp.call("next_available_day",
                        {"origin": "DEN", "dest": "BOI", "date": "05/06/2025"})
    check(bad_date.startswith("error:") and "YYYY-MM-DD" in bad_date,
          "a bad argument comes back as a tool error the model can fix",
          bad_date[:90])

    try:
        mcp.call("no_such_tool", {})
        check(False, "an unknown tool raises a protocol error")
    except MCPError as exc:
        check(exc.code == -32602, "an unknown tool raises a protocol error",
              "code %s" % exc.code)

    try:
        mcp.request("tools/nonexistent")
        check(False, "an unknown method returns -32601")
    except MCPError as exc:
        check(exc.code == -32601, "an unknown method returns -32601", "code %s" % exc.code)

    mcp.close()
    check(not mcp.running, "server shuts down when its stdin closes")

    # -- 7. legacy lifecycle: initialize + notifications/initialized ------
    t0 = time.time()
    with MCPServer(mcp_client.DEFAULT_SERVER_CMD, trace=trace, prefer_era="legacy") as old:
        timings["initialize handshake"] = time.time() - t0
        check(old.era == "legacy", "legacy handshake opened with initialize",
              "era=%s" % old.era)
        check(old.protocol_version == "2025-11-25",
              "initialize negotiated a protocol version",
              "protocolVersion=%s" % old.protocol_version)
        check(old.server_info.get("name") == "larkspur-ops",
              "initialize returned serverInfo")
        legacy_names = [t["name"] for t in old.list_tools()]
        check(set(legacy_names) == {"next_available_day", "fare_rules"},
              "tools/list works after the legacy handshake", ", ".join(legacy_names))

    # -- 8. module-level helpers, the ones agent.py uses ------------------
    t0 = time.time()
    module_schemas = mcp_client.discover()
    timings["module discover()"] = time.time() - t0
    check(len(module_schemas) == 2, "module-level discover() found both tools")
    check("next_available_day" in mcp_client.tool_names,
          "module-level tool_names is populated", str(sorted(mcp_client.tool_names)))
    t0 = time.time()
    module_answer = mcp_client.call("next_available_day",
                                    {"origin": "DEN", "dest": "BOI", "date": "2025-05-06"})
    timings["module call()"] = time.time() - t0
    check("2025-05-09" in module_answer, "module-level call() works")
    mcp_client.close()

    # -- results -----------------------------------------------------------
    print("-" * 74)
    for label, seconds in timings.items():
        print("      %-22s %.3fs" % (label, seconds))
    print("=" * 74)
    total = PASSES + FAILURES
    if FAILURES:
        print("%d FAIL / %d checks. The MCP server and client are not ready." % (FAILURES, total))
        return 1
    print("%d/%d PASS. The MCP server and client are ready." % (PASSES, total))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", action="store_true",
                        help="run the server with --trace so every line shows on stderr")
    args = parser.parse_args()
    return run(args.trace)


if __name__ == "__main__":
    sys.exit(main())
