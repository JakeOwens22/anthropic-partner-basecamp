#!/usr/bin/env python3
"""An MCP server for two Larkspur lookups, written in the standard library only.

WHAT THIS IS, IN PLAIN WORDS

An MCP server is a small program that answers questions over its own stdin and
stdout. It does not talk to Claude. It does not know what a model is. Something
else, called the host, launches it as a subprocess and does the talking. Claude
Code is a host. `support/mcp_client.py` is a host. Both drive this same file.

The wire is one JSON object per line, in both directions. That is the whole
transport. Requests go in on stdin, responses come out on stdout, and anything
this file wants to say to a human goes to stderr, because a stray print on
stdout is a protocol error, not a log line.

THE MESSAGES YOU WILL SEE GO PAST

Run the client with tracing on and you will watch these:

    server/discover     who are you, what versions do you speak, what can you do
    tools/list          give me every tool you have, with its JSON schema
    tools/call          run this tool with these arguments, here is the answer
    ping                still there?

Older hosts open with `initialize` and then a `notifications/initialized`
notification instead of `server/discover`. This file answers both, because the
protocol changed shape in the 2026-07-28 revision and real hosts are still
mixed. See ERAS below.

WHY THIS FILE EXISTS AT ALL

The official `mcp` Python SDK needs Python 3.10. The exercise floor is 3.9.
So this is written from the spec: no packages, no imports beyond the standard
library and the exercise's own mock backend. About four hundred lines, most of
them comments.

THE POINT OF THE EXERCISE

The two tools below are the SAME two functions the agent can already call in
process. Nothing about the model's job changes. What changes is who owns the
tool: a separate program, versioned on its own, reachable by any host. Watch
the token count before and after. It does not move. MCP fixes what you
maintain. It does nothing for routing.

ERAS

    modern (2026-07-28)   No handshake. Every request carries its protocol
                          version in params._meta. The server answers each
                          request on its own. `server/discover` is how a host
                          asks what the server supports.
    legacy (2025-11-25    An `initialize` request opens a session, the server
      and earlier)        answers with its protocolVersion, capabilities and
                          serverInfo, and the host follows up with a
                          `notifications/initialized` notification.

This file is dual-era: it serves whichever one the host opens with.

RUN IT

    python3 support/mcp_server.py --trace          # then type JSON at it
    python3 support/mcp_selftest.py                # the real check
    MCP_TRACE=1 python3 support/mcp_server.py

You will not normally run this by hand. A host runs it for you.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
EXERCISE_ROOT = HERE.parent
# Same resolution rule support/mock_backend.py uses: the data lives beside the
# code, two directories up from this file, so the server works from any cwd.
DATA_DIR = EXERCISE_ROOT / "data" / "americas"

if str(EXERCISE_ROOT) not in sys.path:
    sys.path.insert(0, str(EXERCISE_ROOT))

from support import mock_backend  # noqa: E402  (path has to be set first)

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------
SERVER_NAME = "larkspur-ops"
SERVER_VERSION = "1.0.0"

# The revision this server was written against. Checked 6 Sep 2026 at
# https://modelcontextprotocol.io/specification/latest
PROTOCOL_MODERN = "2026-07-28"

# Handshake-based revisions. A host that opens with `initialize` gets one of
# these back, newest first.
PROTOCOL_LEGACY = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

SUPPORTED_VERSIONS = [PROTOCOL_MODERN] + PROTOCOL_LEGACY

SERVER_INFO = {
    "name": SERVER_NAME,
    "title": "Larkspur Airlines ops lookups",
    "version": SERVER_VERSION,
}

CAPABILITIES = {"tools": {"listChanged": False}}

INSTRUCTIONS = (
    "Two read-only Larkspur Airlines lookups for disruption care. "
    "next_available_day answers questions about dates: the soonest day a "
    "stranded customer can actually fly. fare_rules returns the Handbook text "
    "behind an entitlement decision. Neither one holds, books, or pays for "
    "anything. Fixture data covers 7 to 9 May 2025 only."
)

# The per-request metadata keys the 2026-07-28 revision reserves.
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# JSON-RPC error codes. -32601 is the one a host uses to find out what a server
# cannot do, so it has to be exact.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022

TRACE = False
STRICT = False
TRACE_CHARS = 320
_WARNED_CAPS = False


# ---------------------------------------------------------------------------
# stderr only. Never stdout.
# ---------------------------------------------------------------------------
def _trace(arrow: str, payload: Any) -> None:
    """One compact line per message, so a human can watch the wire.

    `mcp →` is a line that came in on stdin. `mcp ←` is a line going out on
    stdout. Both go to stderr, which is the only channel a stdio server is
    allowed to talk to humans on.
    """
    if not TRACE:
        return
    text = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
    text = " ".join(text.split())
    if len(text) > TRACE_CHARS:
        text = text[: TRACE_CHARS - 1] + "…"
    sys.stderr.write("mcp %s %s\n" % (arrow, text))
    sys.stderr.flush()


def _note(text: str) -> None:
    """A remark for whoever is reading stderr. Always printed."""
    sys.stderr.write("mcp ! %s\n" % text)
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Tool 1: next_available_day
#
# Wraps mock_backend.earliest_alternative_date, which was always there. Nobody
# had given a caller a way to reach it.
# ---------------------------------------------------------------------------
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def tool_next_available_day(origin: str, dest: str, date: str, cabin: str = "Y") -> Tuple[str, bool]:
    """Returns (text, is_error). Argument problems come back as tool errors,
    not protocol errors, because a model can fix those itself on the next try."""
    origin = (origin or "").strip().upper()
    dest = (dest or "").strip().upper()
    date = (date or "").strip()
    cabin = (cabin or "Y").strip().upper()

    if len(origin) != 3 or len(dest) != 3:
        return ("origin and dest must each be a three-letter airport code, "
                "for example DEN and BOI. Got %r and %r." % (origin, dest), True)
    if not ISO_DATE.match(date):
        return ("date must be ISO format, YYYY-MM-DD, for example 2025-05-08. "
                "Got %r." % date, True)
    if cabin not in ("Y", "J"):
        return ("cabin must be Y (main) or J (first). Got %r." % cabin, True)

    found = mock_backend.earliest_alternative_date(origin, dest, date, cabin)
    if not found:
        return ("No open seat from %s to %s in cabin %s on or after %s in the "
                "schedule this server can see. The fixture horizon is %s to %s."
                % (origin, dest, cabin, date,
                   mock_backend.DATA_HORIZON_START, mock_backend.DATA_HORIZON_END), False)
    return ("Earliest date with an open %s seat from %s to %s, searching forward "
            "from %s: %s. This answers for one passenger."
            % (cabin, origin, dest, date, found), False)


# ---------------------------------------------------------------------------
# Tool 2: fare_rules
#
# The Handbook text behind a policy row. Read straight off disk, sliced on the
# markdown headings, so "why won't you give me a hotel?" has a quotable answer.
# ---------------------------------------------------------------------------
FARE_RULES_PATH = DATA_DIR / "fare_rules_excerpt.md"

_sections_cache: Optional[List[Dict[str, str]]] = None


def _fare_rules_sections() -> List[Dict[str, str]]:
    """Split the excerpt on its `### ` headings. Cached: a classroom re-reads
    this a lot and the file never changes mid-session."""
    global _sections_cache
    if _sections_cache is not None:
        return _sections_cache

    sections: List[Dict[str, str]] = []
    try:
        text = FARE_RULES_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        _note("cannot read %s: %s" % (FARE_RULES_PATH, exc))
        _sections_cache = []
        return _sections_cache

    current: Optional[Dict[str, Any]] = None
    for line in text.splitlines():
        if line.startswith("### "):
            heading = line[4:].strip()
            match = re.match(r"^(\d+)\.\s*(.*)$", heading)
            current = {
                "number": match.group(1) if match else "",
                "title": match.group(2) if match else heading,
                "heading": heading,
                "lines": [],
            }
            sections.append(current)  # type: ignore[arg-type]
        elif current is not None:
            current["lines"].append(line)

    for section in sections:
        body = "\n".join(section.pop("lines")).strip()  # type: ignore[arg-type]
        section["text"] = "### %s\n\n%s" % (section["heading"], body)

    _sections_cache = sections  # type: ignore[assignment]
    return _sections_cache


def _section_labels() -> str:
    return ", ".join("%s (%s)" % (s["number"], s["title"]) for s in _fare_rules_sections())


def tool_fare_rules(section: str) -> Tuple[str, bool]:
    """Returns (text, is_error). Matches on the section number or on any part
    of its title, because a caller asking about hotels will not know it is
    section 6."""
    query = (section or "").strip().lower()
    query = re.sub(r"^section\s+", "", query).rstrip(".")
    if not query:
        return ("section is required. Available sections: %s." % _section_labels(), True)

    sections = _fare_rules_sections()
    if not sections:
        return ("The fare rules excerpt is not readable on this machine. Expected "
                "it at %s." % FARE_RULES_PATH, True)

    for candidate in sections:
        if query == candidate["number"]:
            return (candidate["text"], False)
    for candidate in sections:
        if query in candidate["title"].lower():
            return (candidate["text"], False)

    return ("No section matches %r. Available sections: %s."
            % (section, _section_labels()), True)


# ---------------------------------------------------------------------------
# Tool schemas
#
# This is the part a host actually reads. The description is the routing
# surface: the model cannot see the code above and cannot ask what a function
# does. Same bar as the nine tools in agent.py: when to call it, what it
# needs, what comes back, and the field descriptions route too.
# ---------------------------------------------------------------------------
TOOLS: List[Dict[str, Any]] = [
    {
        "name": "next_available_day",
        "title": "Earliest open seat",
        "description": (
            "Answer the first question a cancelled or stranded Larkspur customer asks: "
            "what is the soonest day you can actually get me out? Call it for questions "
            "about DATES, when the customer wants to know how long they are stuck rather "
            "than which specific flight to take. It needs the departure and arrival "
            "airport codes, the date the customer was booked to travel, and the cabin. "
            "It searches Larkspur inventory forward from that date and returns the "
            "earliest date with an open seat as YYYY-MM-DD, or says plainly that there is "
            "no open seat in the schedule it can see. It holds nothing and books nothing, "
            "and it answers for a party of one."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "origin": {
                    "type": "string",
                    "description": "Departure airport, three-letter IATA code, e.g. DEN.",
                },
                "dest": {
                    "type": "string",
                    "description": "Arrival airport, three-letter IATA code, e.g. BOI.",
                },
                "date": {
                    "type": "string",
                    "description": ("The disrupted travel date in ISO format, YYYY-MM-DD, "
                                    "e.g. 2025-05-08. The search starts here and looks "
                                    "forward, never backward."),
                },
                "cabin": {
                    "type": "string",
                    "description": ("Cabin to search: Y for main, J for first. Use the "
                                    "cabin the customer is already ticketed in. Defaults "
                                    "to Y."),
                    "enum": ["Y", "J"],
                    "default": "Y",
                },
            },
            "required": ["origin", "dest", "date"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fare_rules",
        "title": "Handbook text",
        "description": (
            "Return the Larkspur Customer Commitment and fare rules text behind an "
            "entitlement decision, straight from the published Handbook excerpt. Call it "
            "when a customer challenges an answer and wants to know the rule, or asks "
            "why something is or is not covered, so the reply can quote the Handbook "
            "instead of paraphrasing it. It needs one section: a number, or any words "
            "from the section title such as 'care while you wait'. It returns that "
            "section's full text. It is reference reading, not an entitlements decision: "
            "the policy table is still the only source of truth for what is owed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": ("Which section to read. A number like '6', or words "
                                    "from its title like 'fare families', 'when we delay', "
                                    "'care while you wait', 'chat automation'."),
                },
            },
            "required": ["section"],
            "additionalProperties": False,
        },
    },
]

TOOL_IMPLS = {
    "next_available_day": tool_next_available_day,
    "fare_rules": tool_fare_rules,
}


# ---------------------------------------------------------------------------
# JSON-RPC plumbing
# ---------------------------------------------------------------------------
class RpcError(Exception):
    """A protocol-level failure: bad method, bad params, unsupported version.
    Distinct from a tool that ran and did not like its arguments. That comes
    back as a normal result with isError set."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _result(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Every result carries resultType and the server's identity. A host on an
    older revision ignores both; the 2026-07-28 revision expects them."""
    out = {"resultType": "complete"}
    out.update(payload)
    meta = dict(out.get("_meta") or {})
    meta[META_SERVER_INFO] = SERVER_INFO
    out["_meta"] = meta
    return out


def _check_modern_meta(meta: Dict[str, Any]) -> None:
    """The 2026-07-28 revision requires protocolVersion and clientCapabilities
    on every single request. There is no session to remember them in."""
    version = meta.get(META_VERSION)
    if version not in SUPPORTED_VERSIONS:
        raise RpcError(UNSUPPORTED_PROTOCOL_VERSION, "Unsupported protocol version",
                       {"supported": SUPPORTED_VERSIONS, "requested": version})
    if META_CLIENT_CAPS not in meta:
        if STRICT:
            raise RpcError(INVALID_PARAMS,
                           "Missing required _meta field %s" % META_CLIENT_CAPS)
        global _WARNED_CAPS
        if not _WARNED_CAPS:
            _WARNED_CAPS = True
            _note("this host sends no %s; the spec requires it. Serving anyway. "
                  "Run with --strict to refuse." % META_CLIENT_CAPS)


def dispatch(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """One method in, one result out. Raises RpcError for anything the
    protocol, rather than a tool, has an opinion about."""
    if method == "server/discover":
        # How a modern host asks a server to introduce itself.
        return _result({
            "supportedVersions": SUPPORTED_VERSIONS,
            "capabilities": CAPABILITIES,
            "instructions": INSTRUCTIONS,
        })

    if method == "initialize":
        # The handshake older hosts open with. Negotiation rule from the spec:
        # answer with the client's version if we speak it, otherwise with the
        # newest one we do.
        asked = params.get("protocolVersion")
        if asked in PROTOCOL_LEGACY or asked == PROTOCOL_MODERN:
            agreed = asked
        else:
            agreed = PROTOCOL_LEGACY[0]
            _note("client asked for protocolVersion %r; answering with %s"
                  % (asked, agreed))
        client = params.get("clientInfo") or {}
        if TRACE:
            _note("initialize from %s %s" % (client.get("name", "unknown host"),
                                             client.get("version", "")))
        return _result({
            "protocolVersion": agreed,
            "capabilities": CAPABILITIES,
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })

    if method == "ping":
        return _result({})

    if method == "tools/list":
        return _result({"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise RpcError(INVALID_PARAMS, "arguments must be an object")
        if name not in TOOL_IMPLS:
            # Unknown tool is a protocol error, not a tool error: the model
            # cannot fix a tool that does not exist.
            raise RpcError(INVALID_PARAMS, "Unknown tool: %s" % name,
                           {"available": sorted(TOOL_IMPLS)})
        try:
            text, is_error = TOOL_IMPLS[name](**arguments)
        except TypeError as exc:
            # Wrong or missing arguments. The model CAN fix this, so it goes
            # back as a tool error with the schema's own words in it.
            text, is_error = ("Bad arguments for %s: %s. Check the tool's "
                              "inputSchema." % (name, exc)), True
        except Exception as exc:  # noqa: BLE001 - a tool crash is a tool result
            text, is_error = "%s failed: %s: %s" % (name, type(exc).__name__, exc), True
        return _result({"content": [{"type": "text", "text": text}], "isError": is_error})

    raise RpcError(METHOD_NOT_FOUND, "Method not found: %s" % method)


def process(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Turn one parsed message into one response, or None for a notification.

    A notification has no id and never gets an answer. That is the whole rule,
    and it is why `notifications/initialized` produces silence rather than an
    empty result.
    """
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": INVALID_REQUEST, "message": "Not a JSON-RPC 2.0 message"}}

    method = message.get("method")
    params = message.get("params")
    if params is None:
        params = {}
    has_id = "id" in message and message["id"] is not None

    if not has_id:
        # Notifications. `notifications/initialized` closes the legacy
        # handshake; `notifications/cancelled` gives up on an in-flight call.
        # Neither gets a reply.
        if method == "notifications/initialized" and TRACE:
            _note("handshake complete")
        return None

    request_id = message["id"]
    try:
        if not isinstance(method, str):
            raise RpcError(INVALID_REQUEST, "method must be a string")
        meta = (params.get("_meta") or {}) if isinstance(params, dict) else {}
        if META_VERSION in meta:
            _check_modern_meta(meta)
        result = dispatch(method, params if isinstance(params, dict) else {})
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except RpcError as exc:
        error = {"code": exc.code, "message": exc.message}
        if exc.data is not None:
            error["data"] = exc.data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}
    except Exception as exc:  # noqa: BLE001 - a crash must not kill the server
        _note("internal error on %s: %s: %s" % (method, type(exc).__name__, exc))
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": INTERNAL_ERROR,
                          "message": "%s: %s" % (type(exc).__name__, exc)}}


def _write(obj: Dict[str, Any]) -> None:
    line = json.dumps(obj, separators=(",", ":"), default=str)
    _trace("←", line)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def serve(stdin=None, stdout=None) -> int:
    """Read one JSON object per line until stdin closes, answer each one.

    Closing stdin is how a host says goodbye, so end of file means exit, not
    error.
    """
    stream = stdin if stdin is not None else sys.stdin
    for raw in stream:
        line = raw.strip()
        if not line:
            continue
        _trace("→", line)
        try:
            message = json.loads(line)
        except ValueError as exc:
            _write({"jsonrpc": "2.0", "id": None,
                    "error": {"code": PARSE_ERROR, "message": "Parse error: %s" % exc}})
            continue
        response = process(message)
        if response is not None:
            _write(response)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    global TRACE, STRICT
    parser = argparse.ArgumentParser(
        description="Larkspur MCP server (stdio, standard library only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--trace", action="store_true",
                        help="echo every request and response line to stderr "
                             "(or set MCP_TRACE=1)")
    parser.add_argument("--strict", action="store_true",
                        help="reject requests missing a required _meta field, "
                             "as the 2026-07-28 revision says to (or MCP_STRICT=1)")
    parser.add_argument("--version", action="store_true",
                        help="print the protocol revisions this server speaks and exit")
    args = parser.parse_args(argv)

    TRACE = args.trace or os.environ.get("MCP_TRACE") == "1"
    STRICT = args.strict or os.environ.get("MCP_STRICT") == "1"

    if args.version:
        # stdout is fine here: --version is not a protocol session.
        print("%s %s  speaks: %s" % (SERVER_NAME, SERVER_VERSION,
                                     ", ".join(SUPPORTED_VERSIONS)))
        return 0

    if TRACE:
        _note("%s %s ready on stdio, %d tool(s): %s"
              % (SERVER_NAME, SERVER_VERSION, len(TOOLS),
                 ", ".join(t["name"] for t in TOOLS)))
    try:
        return serve()
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        # The host went away mid-write. Nothing to report to.
        return 0


if __name__ == "__main__":
    sys.exit(main())
