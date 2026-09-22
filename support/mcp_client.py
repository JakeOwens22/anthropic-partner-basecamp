#!/usr/bin/env python3
"""The host side of MCP: launch a server, ask what it has, call it.

WHAT A HOST DOES

Four steps, and that is all there is to it:

    1. start the server as a subprocess and agree on a protocol version
    2. tools/list  : ask what it has, and get JSON schemas back
    3. hand those schemas to Claude alongside your own tools
    4. tools/call  : when Claude picks one of them, run it here and hand the
                     answer back into the loop

Step 3 is the whole trick. A tool that arrived over MCP and a tool you wrote in
agent.py look identical to the model: name, description, input schema. It costs
the same tokens. It routes the same way, well or badly, on the strength of the
same description. What changes is that somebody else can own it, version it,
and hand it to twelve other hosts.

WHAT THIS FILE IS NOT

Not a general MCP client. It speaks exactly enough to drive
`support/mcp_server.py`: one server, one request at a time, no resources, no
prompts, no sampling, no subscriptions. The official SDK does all of that and
needs Python 3.10, which is why this exists.

USE IT

    from support import mcp_client

    for schema in mcp_client.tools():          # Anthropic-shaped, ready to send
        print(schema["name"], schema["input_schema"])

    print(mcp_client.call("next_available_day",
                          {"origin": "DEN", "dest": "BOI", "date": "2025-05-06"}))

`tools()` is the one to reach for from a tool list: it asks the server once per
process and caches the answer. `call_remote()` is the one to reach for from a
tool loop: it always answers, and it records what came back on the trace.

Or hold the server yourself:

    with mcp_client.MCPServer(["python3", "support/mcp_server.py"], trace=True) as mcp:
        tools = mcp.discover()
        print(mcp.tool_names)
        print(mcp.call("fare_rules", {"section": "6"}))

`trace=True` turns on the server's own `--trace`, so you watch tools/list and
tools/call go past on stderr. That is the lesson: it is a wire, not a library
call.
"""

from __future__ import annotations

import atexit
import collections
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
EXERCISE_ROOT = HERE.parent

# The command the module-level helpers run, resolved against the exercise root
# so it works from any working directory.
DEFAULT_SERVER_CMD: List[str] = [sys.executable, str(EXERCISE_ROOT / "support" / "mcp_server.py")]

# Revisions this client speaks. Modern first: no handshake, per-request
# metadata. The legacy one is the `initialize` handshake older hosts and older
# servers still use.
PROTOCOL_MODERN = "2026-07-28"
PROTOCOL_LEGACY = "2025-11-25"

CLIENT_INFO = {"name": "larkspur-exercise", "version": "1.0.0"}
CLIENT_CAPABILITIES: Dict[str, Any] = {}

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"

UNSUPPORTED_PROTOCOL_VERSION = -32022

# The first request pays for process startup plus loading the fixture JSON.
# Later ones are milliseconds. Both are generous on purpose: a laptop under a
# room full of builds is slow, and a hang with no message is the worst failure
# mode in the pack.
FIRST_TIMEOUT = 30.0
CALL_TIMEOUT = 20.0
# How long to wait for the modern probe before deciding the server is a legacy
# one that never answers unknown pre-handshake methods.
PROBE_TIMEOUT = 8.0


class MCPTimeout(RuntimeError):
    """The server did not answer in time. Says which method and which command."""


class MCPError(RuntimeError):
    """The server answered with a JSON-RPC error. Carries the code."""

    def __init__(self, message: str, code: Optional[int] = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class MCPStdoutNoise(MCPError):
    """The server wrote something to stdout that is not a protocol message.

    Nearly always a print() that belonged on stderr. Kept as its own type so
    the handshake reports it instead of mistaking it for an old server.
    """


class MCPServer:
    """One MCP server, held open as a subprocess.

    Requests are sequential: send one, wait for the response with that id.
    Notifications the server sends in the meantime are skipped, not queued for
    anyone, because nothing in this exercise subscribes to them.
    """

    def __init__(self, cmd: List[str], cwd: Optional[str] = None, trace: bool = False,
                 prefer_era: str = "auto", forward_stderr: bool = True,
                 env: Optional[Dict[str, str]] = None) -> None:
        """`cmd` is the server command, e.g. [sys.executable, ".../mcp_server.py"].

        prefer_era: "auto" probes with server/discover and falls back to the
        `initialize` handshake, which is what the spec tells a dual-era client
        to do on stdio. "modern" or "legacy" skip the probe.
        """
        self.cmd = list(cmd)
        self.cwd = cwd or str(EXERCISE_ROOT)
        self.trace = trace
        self.prefer_era = prefer_era
        self.forward_stderr = forward_stderr
        self.env = env

        self.proc: Optional[subprocess.Popen] = None
        self.era: Optional[str] = None
        self.protocol_version: Optional[str] = None
        self.server_info: Dict[str, Any] = {}
        self.instructions: str = ""

        # Every tool name this client has seen from this server. agent.py reads
        # it to decide whether a tool_use block goes to MCP or to LOCAL_TOOLS,
        # and run.py reads it to tag a tool "via mcp".
        self._tool_names: List[str] = []
        self._tools_raw: List[Dict[str, Any]] = []

        self._next_id = 0
        # Kept so a server that dies on startup can be quoted back, instead of
        # "it is not running" with no reason attached.
        self._stderr_tail: "collections.deque" = collections.deque(maxlen=8)
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._first_request_done = False
        self.timings: Dict[str, float] = {}

    # -- lifecycle ---------------------------------------------------------
    @property
    def tool_names(self) -> List[str]:
        """Tool names discovered over MCP, in the order the server listed them."""
        return list(self._tool_names)

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> "MCPServer":
        """Launch the server and agree on a protocol version. Safe to call twice."""
        if self.running:
            return self

        cmd = list(self.cmd)
        if self.trace and "--trace" not in cmd:
            cmd.append("--trace")

        env = dict(os.environ)
        if self.env:
            env.update(self.env)

        started = time.time()
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=self.cwd, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
        except OSError as exc:
            raise MCPError("Could not start the MCP server.\n  command: %s\n  %s: %s"
                           % (" ".join(cmd), type(exc).__name__, exc)) from exc

        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

        self._handshake()
        self.timings["start"] = time.time() - started
        return self

    def close(self) -> None:
        """Shut the server down the way the spec says: close its stdin, wait,
        then stop being polite about it."""
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:  # noqa: BLE001 - closing must never raise
            pass
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._lines.put(None)

    def __enter__(self) -> "MCPServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- the wire ----------------------------------------------------------
    def _read_stdout(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                self._lines.put(line)
        except Exception:  # noqa: BLE001 - the pipe closed under us
            pass
        self._lines.put(None)

    def _read_stderr(self) -> None:
        """The server's stderr is where its trace lives. Pass it straight
        through so a participant sees `mcp →` and `mcp ←` on their terminal."""
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                self._stderr_tail.append(line.rstrip("\n"))
                if self.forward_stderr:
                    sys.stderr.write(line if line.endswith("\n") else line + "\n")
                    sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass

    def _why_dead(self) -> str:
        """The exit code and the last few stderr lines, for an error message."""
        code = self.proc.returncode if self.proc is not None else None
        parts = ["  command: %s" % " ".join(self.cmd)]
        if code is not None:
            parts.append("  exit code: %s" % code)
        # Give the reader thread a moment to drain what the process said.
        time.sleep(0.05)
        tail = [line for line in self._stderr_tail if line.strip()]
        if tail:
            parts.append("  its last words on stderr:")
            parts.extend("    %s" % line[:200] for line in tail[-5:])
        else:
            parts.append("  it said nothing on stderr. Run the command above by hand.")
        return "\n".join(parts)

    def _meta(self) -> Dict[str, Any]:
        """The per-request metadata the modern revision requires on everything.
        There is no session, so the version rides along every time."""
        return {
            META_VERSION: self.protocol_version or PROTOCOL_MODERN,
            META_CLIENT_INFO: CLIENT_INFO,
            META_CLIENT_CAPS: CLIENT_CAPABILITIES,
        }

    def _send(self, payload: Dict[str, Any]) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise MCPError("The MCP server is not running. Call start() first.")
        line = json.dumps(payload, separators=(",", ":"), default=str)
        try:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise MCPError("The MCP server closed its input.\n  command: %s\n  %s"
                           % (" ".join(self.cmd), exc)) from exc

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """A one-way message. No id, so no answer comes back, ever."""
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            payload["params"] = params
        self._send(payload)

    def request(self, method: str, params: Optional[Dict[str, Any]] = None,
                timeout: Optional[float] = None, with_meta: bool = True) -> Dict[str, Any]:
        """Send one request, wait for the response with the same id, return its
        result. Raises MCPError on a JSON-RPC error, MCPTimeout on silence."""
        if not self.running:
            raise MCPError("The MCP server is not running, so %s could not be sent.\n%s"
                           % (method, self._why_dead()))

        self._next_id += 1
        request_id = self._next_id
        body: Dict[str, Any] = dict(params or {})
        if with_meta and self.era == "modern":
            body["_meta"] = self._meta()
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if body:
            payload["params"] = body

        if timeout is None:
            timeout = CALL_TIMEOUT if self._first_request_done else FIRST_TIMEOUT

        started = time.time()
        self._send(payload)
        result = self._await(request_id, method, timeout)
        self._first_request_done = True
        self.timings[method] = time.time() - started
        return result

    def _await(self, request_id: int, method: str, timeout: float) -> Dict[str, Any]:
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise MCPTimeout(
                    "No response to %s after %.0fs.\n"
                    "  command: %s\n"
                    "  Check three things, in this order: the server prints nothing to "
                    "stdout except JSON (a stray print breaks the wire), the command "
                    "above runs on its own, and the server is not waiting on something "
                    "slow." % (method, timeout, " ".join(self.cmd)))
            try:
                line = self._lines.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if self.proc is not None and self.proc.poll() is not None:
                    raise MCPError("The MCP server exited before answering %s.\n%s"
                                   % (method, self._why_dead()))
                continue
            if line is None:
                raise MCPError("The MCP server closed its output before answering %s.\n%s"
                               % (method, self._why_dead()))
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                # Not JSON. Almost always a print() that should have gone to
                # stderr. Say so rather than swallowing it.
                raise MCPStdoutNoise(
                    "The MCP server wrote a non-JSON line to stdout, which breaks the "
                    "protocol:\n  %s\n  command: %s\n  Everything a stdio server says to "
                    "a human belongs on stderr." % (line[:200], " ".join(self.cmd)))
            if not isinstance(message, dict):
                continue
            if message.get("id") != request_id:
                # A notification, or an answer to something we gave up on.
                continue
            if "error" in message:
                error = message["error"] or {}
                raise MCPError("%s failed: %s (code %s)"
                               % (method, error.get("message", "unknown error"),
                                  error.get("code")),
                               code=error.get("code"), data=error.get("data"))
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    # -- lifecycle, on the wire -------------------------------------------
    def _handshake(self) -> None:
        """Find out which era the server speaks, then agree a version.

        The spec's own advice for a stdio client that supports both: send
        server/discover first. A modern server answers it. A legacy server
        errors or says nothing, and then you fall back to `initialize`.
        """
        if self.prefer_era == "legacy":
            self._initialize()
            return

        self.era = "modern"
        self.protocol_version = PROTOCOL_MODERN
        try:
            result = self.request("server/discover", timeout=PROBE_TIMEOUT)
        except MCPStdoutNoise:
            # Not an old server. A broken one. Say so.
            raise
        except MCPError as exc:
            if not self.running:
                # The process is gone. Falling back to `initialize` would only
                # replace a useful message with a confusing one.
                raise
            if exc.code == UNSUPPORTED_PROTOCOL_VERSION:
                supported = (exc.data or {}).get("supported") or []
                if supported:
                    self.protocol_version = supported[0]
                    result = self.request("server/discover", timeout=PROBE_TIMEOUT)
                else:
                    raise
            elif self.prefer_era == "modern":
                raise
            else:
                # Any other error means legacy. Do not guess at the code: the
                # spec says legacy servers answer unknown methods however they
                # like.
                self._initialize()
                return
        except MCPTimeout:
            if self.prefer_era == "modern":
                raise
            self._initialize()
            return

        versions = result.get("supportedVersions") or [PROTOCOL_MODERN]
        self.protocol_version = (PROTOCOL_MODERN if PROTOCOL_MODERN in versions
                                 else versions[0])
        self.server_info = ((result.get("_meta") or {})
                            .get("io.modelcontextprotocol/serverInfo") or {})
        self.instructions = result.get("instructions") or ""

    def _initialize(self) -> None:
        """The handshake older servers expect: initialize, then a notification
        saying the client is ready."""
        self.era = "legacy"
        self.protocol_version = PROTOCOL_LEGACY
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_LEGACY,
            "capabilities": CLIENT_CAPABILITIES,
            "clientInfo": CLIENT_INFO,
        }, with_meta=False)
        self.protocol_version = result.get("protocolVersion") or PROTOCOL_LEGACY
        self.server_info = result.get("serverInfo") or {}
        self.instructions = result.get("instructions") or ""
        # Required by the legacy lifecycle, and it is a notification, so
        # nothing comes back and nothing should be waited for.
        self.notify("notifications/initialized")

    # -- the two calls that matter ----------------------------------------
    def ping(self) -> bool:
        self.request("ping")
        return True

    def list_tools(self) -> List[Dict[str, Any]]:
        """Raw tools/list output, in the server's own shape: name, description,
        inputSchema."""
        if not self.running:
            self.start()
        result = self.request("tools/list")
        tools = result.get("tools") or []
        self._tools_raw = [t for t in tools if isinstance(t, dict) and t.get("name")]
        self._tool_names = [t["name"] for t in self._tools_raw]
        return list(self._tools_raw)

    def discover(self) -> List[Dict[str, Any]]:
        """tools/list, rewritten into the shape the Messages API wants.

        One rename and nothing else: MCP calls it `inputSchema`, the Anthropic
        tool block calls it `input_schema`. That is the entire adapter, and it
        is worth noticing how small it is. The two schema formats are the same
        idea, so the model cannot tell where a tool came from.
        """
        schemas = []
        for tool in self.list_tools():
            schemas.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool.get("inputSchema") or {"type": "object"},
            })
        return schemas

    def call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
        """Run one tool on the server and return its text.

        A tool that ran and did not like its arguments comes back as text
        prefixed with "error: ", the same way support/tools.py hands back an
        error dict: the model can read it and try again. A protocol failure (
        no such tool, malformed request) raises MCPError instead, because
        that is a bug in the integration, not something a model can fix.
        """
        if not self.running:
            self.start()
        result = self.request("tools/call", {"name": name, "arguments": dict(arguments or {})})
        parts = []
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
        text = "\n".join(p for p in parts if p)
        if not text:
            text = json.dumps(result.get("structuredContent") or {}, default=str)
        if result.get("isError"):
            return "error: " + text
        return text


# ---------------------------------------------------------------------------
# Module-level convenience: one shared server, started on first use.
#
# This is what agent.py uses. `tool_names` is a set, updated in place by
# discover(), so `if block.name in mcp_client.tool_names` reads the same way
# whether you hold a server yourself or use these helpers.
# ---------------------------------------------------------------------------
tool_names: set = set()

_shared: Optional[MCPServer] = None

# Filled by tools() on first discovery and read from there afterwards. Private
# on purpose: tools() is the only name worth reaching for, because the cache is
# empty until something has actually asked the server, so a tool list that read
# this directly would look correct, type-check, and hand back nothing.
_TOOLS_CACHE: List[Dict[str, Any]] = []


def server(cmd: Optional[List[str]] = None, trace: bool = False) -> MCPServer:
    """The shared server, started if it is not already running."""
    global _shared
    if _shared is None or not _shared.running:
        _shared = MCPServer(cmd or DEFAULT_SERVER_CMD,
                            trace=trace or os.environ.get("MCP_TRACE") == "1")
        _shared.start()
    return _shared


def discover(cmd: Optional[List[str]] = None, trace: bool = False) -> List[Dict[str, Any]]:
    """Anthropic-shaped schemas for every tool the server has. Also records the
    names in `tool_names`, so a dispatch can tell an MCP tool from a local one."""
    schemas = server(cmd, trace).discover()
    tool_names.clear()
    tool_names.update(s["name"] for s in schemas)
    return schemas


def tools(cmd: Optional[List[str]] = None, trace: bool = False) -> List[Dict[str, Any]]:
    """Every tool the server has, Anthropic-shaped, once per process, cached.

    Nothing starts the server until something calls this, so a file that has not
    been wired never spawns it. A server that will not start returns an empty
    list and says why, so a broken server costs you a message and two tools
    rather than a crash halfway through a customer conversation.
    """
    global _TOOLS_CACHE
    if _TOOLS_CACHE:
        return _TOOLS_CACHE
    try:
        _TOOLS_CACHE = discover(cmd, trace)
    except Exception as exc:  # noqa: BLE001 - a dead server is not a stack trace
        print("  [mcp] no tools discovered: %s: %s" % (type(exc).__name__, exc))
        print("  [mcp] python3 support/mcp_selftest.py says why on one line.")
        _TOOLS_CACHE = []
    return _TOOLS_CACHE


def call(name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    """Run one tool on the shared server and return its text."""
    return server().call(name, arguments)


def call_remote(name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
    """call(), packaged for the tool loop: it always answers, and it records.

    A protocol failure is an integration bug rather than something the model can
    fix, and call() raises for exactly that reason. It still has to come back as
    a tool result, or the loop stalls on an unanswered tool_use id, so the
    exception becomes an error dict here. The result is recorded either way, the
    same call support/tools.py makes for the given nine, so the trace shows what
    the server answered and not only what was asked of it.
    """
    # Imported here rather than at the top: this file also runs as a script
    # (python3 support/mcp_client.py), where a relative import has no package.
    from .trace import record_tool_result

    try:
        output: Any = call(name, arguments)
    except Exception as exc:  # noqa: BLE001 - it has to answer, whatever happened
        output = {"error": "mcp call failed for %s: %s: %s"
                           % (name, type(exc).__name__, exc)}
    record_tool_result(name, output)
    return output


def close() -> None:
    global _shared, _TOOLS_CACHE
    _TOOLS_CACHE = []
    if _shared is not None:
        _shared.close()
        _shared = None


atexit.register(close)


def _demo() -> int:
    """python3 support/mcp_client.py: start the server, list, call, print."""
    with MCPServer(DEFAULT_SERVER_CMD, trace=True) as mcp:
        print("era: %s   protocolVersion: %s   server: %s"
              % (mcp.era, mcp.protocol_version, mcp.server_info.get("name")))
        for schema in mcp.discover():
            print("\n%s\n  %s" % (schema["name"], schema["description"][:120]))
        print("\nnext_available_day DEN -> BOI after 2025-05-06:")
        print("  " + mcp.call("next_available_day",
                              {"origin": "DEN", "dest": "BOI", "date": "2025-05-06"}))
        print("\nfare_rules section 6, first line:")
        print("  " + mcp.call("fare_rules", {"section": "6"}).splitlines()[0])
    return 0


if __name__ == "__main__":
    sys.exit(_demo())
