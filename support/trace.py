"""Wire tracer: see what your agent actually sent and got back.

This file is GIVEN, and it is the point of the whole exercise. A slide shows
you a printed answer. This shows you the *request/response cycle*: how many
turns the loop took, which parameters were on each call, which content blocks
came back, which tools it asked for, what those tools answered, and what it
cost.

Usage:
    from support.trace import Tracer, wrap
    tracer = Tracer()
    client = wrap(get_client(), tracer)
    ...
    print(tracer.render())
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

RULE = "─" * 74

# A tool RESULT is evidence, not a transcript, and the two readers of it want
# different lengths. So both are kept, and neither number governs the other.
#
#   RESULT_LINE    what fits under a call on the trace, so the page stays readable
#   RESULT_CHARS   what `run.py -v` shows when you want the whole answer
#   RESULT_GRADER  what the eval judge is allowed to read, in `result_full`
#
# The last one exists because a rendering constant should never decide whether a
# grader can see the policy row it was asked to check. check_policy returns the
# largest object in this pack.
RESULT_LINE = 100
RESULT_CHARS = 500
RESULT_GRADER = 4000


def _short(value: Any, limit: int = 88) -> str:
    text = json.dumps(value, default=str) if not isinstance(value, str) else value
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _via_mcp(name: str) -> bool:
    """True when this tool came over MCP rather than out of this process.

    The import is lazy and optional on purpose: trace.py has to keep working in
    a clone with no MCP client in it, and on day one there is nothing to ask.
    """
    try:
        from .mcp_client import tool_names
    except Exception:  # noqa: BLE001
        return False
    return name in tool_names


class Tracer:
    """Records every Messages API call made through a wrapped client."""

    def __init__(self) -> None:
        self.turns: List[Dict[str, Any]] = []
        self.deltas_seen = 0  # incremented when the learner actually iterates a stream
        self._t0 = time.time()

    # -- recording ---------------------------------------------------------
    def open_turn(self, kind: str, params: Dict[str, Any]) -> int:
        output_config = params.get("output_config") or {}
        thinking = params.get("thinking") or {}
        tool_choice = params.get("tool_choice") or {}
        self.turns.append({
            "kind": kind,
            "n_messages": len(params.get("messages") or []),
            "tools": [t.get("name", "?") for t in (params.get("tools") or [])],
            "thinking": thinking.get("type"),
            "effort": output_config.get("effort"),
            "format": bool(output_config.get("format")),
            "tool_choice": tool_choice.get("type"),
            "started": time.time(),
            "blocks": [],
            "tool_calls": [],
            "stop_reason": None,
            "usage": (0, 0),
            "cache_read": 0,
            "cache_write": 0,
            "elapsed": None,
            "error": None,
            "deltas": 0,   # stream events the learner actually consumed on THIS turn
        })
        return len(self.turns) - 1

    def close_turn(self, index: int, response: Any = None, error: Optional[str] = None) -> None:
        turn = self.turns[index]
        turn["elapsed"] = time.time() - turn["started"]
        if error is not None:
            turn["error"] = error
            return
        turn["stop_reason"] = getattr(response, "stop_reason", None)
        usage = getattr(response, "usage", None)
        turn["usage"] = (
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0,
        )
        # Cache accounting is reported separately from input_tokens, so a run
        # with a 90% hit rate looks identical to one with none unless you read
        # these two. bench.py needs them; nothing on day 1 does.
        turn["cache_read"] = getattr(usage, "cache_read_input_tokens", 0) or 0
        turn["cache_write"] = getattr(usage, "cache_creation_input_tokens", 0) or 0
        for block in getattr(response, "content", []) or []:
            btype = getattr(block, "type", "?")
            turn["blocks"].append(btype)
            if btype == "tool_use":
                turn["tool_calls"].append({
                    "name": getattr(block, "name", "?"),
                    "id": getattr(block, "id", None),
                    "input": getattr(block, "input", {}),
                    # Recorded here, when the tool list that went out is still
                    # the one in hand. save() carries it into the JSON, so
                    # readout.py and the eval judge see it too.
                    "via_mcp": _via_mcp(getattr(block, "name", "?")),
                    # Filled in later by record_result(), once the tool has
                    # actually run. The ask and the answer are different facts:
                    # a trace that records only the ask cannot tell you whether
                    # the agent was reading a real policy row or an error dict.
                    "result": None,
                    # The same answer, at grader length. Read by the eval
                    # judge, never printed.
                    "result_full": None,
                })

    def record_result(self, name: str, output: Any) -> bool:
        """Attach a compact result summary to the earliest call of `name` that
        has not been answered yet. Execution order matches block order, so the
        earliest-unanswered rule is correct even when one turn calls the same
        tool twice.

        Two lengths, recorded once. `result` is what gets rendered; `result_full`
        is what a grader is shown. Truncating to the display length here would
        make a page-layout number decide what an eval judge can read."""
        for turn in self.turns:
            for call in turn["tool_calls"]:
                if call.get("name") == name and call.get("result") is None:
                    call["result"] = _short(output, RESULT_CHARS)
                    call["result_full"] = _short(output, RESULT_GRADER)
                    return True
        return False

    # -- reading -----------------------------------------------------------
    @property
    def tool_calls(self) -> List[Dict[str, Any]]:
        return [call for turn in self.turns for call in turn["tool_calls"]]

    @property
    def tool_names(self) -> List[str]:
        return [call["name"] for call in self.tool_calls]

    @property
    def saw_thinking(self) -> bool:
        return any("thinking" in turn["blocks"] for turn in self.turns)

    @property
    def total_tokens(self) -> Dict[str, int]:
        return {
            "input": sum(t["usage"][0] for t in self.turns),
            "output": sum(t["usage"][1] for t in self.turns),
            "cache_read": sum(t.get("cache_read", 0) for t in self.turns),
            "cache_write": sum(t.get("cache_write", 0) for t in self.turns),
        }

    @property
    def cache_hit_ratio(self) -> Optional[float]:
        """Cached share of everything that was read as input. None when nothing
        was ever offered to the cache, which is not the same as a 0% hit rate:
        0% means you cached and it missed, None means you never cached.

        The denominator is every input token the run was billed for, and that is
        three counters, not two: the API reports `input_tokens` exclusive of
        both cache columns, so leaving `cache_write` out overstates the hit rate
        by exactly the tokens you paid a premium to store. 15,000 read, 5,000
        written and 1,000 fresh is 71%, not 94%."""
        tokens = self.total_tokens
        offered = tokens["cache_read"] + tokens["cache_write"]
        if not offered:
            return None
        billable = tokens["cache_read"] + tokens["cache_write"] + tokens["input"]
        return tokens["cache_read"] / (billable or 1)

    def summary(self) -> Dict[str, Any]:
        return {
            "turns": len(self.turns),
            "tool_calls": len(self.tool_calls),
            "tool_names": self.tool_names,
            "saw_thinking": self.saw_thinking,
            "used_format": any(t["format"] for t in self.turns),
            "used_tool_choice_none": any(t["tool_choice"] == "none" for t in self.turns),
            "streamed": bool(self.turns) and all(t["kind"] == "stream" for t in self.turns),
            "deltas_seen": self.deltas_seen,
            "tokens": self.total_tokens,
            "cache_hit_ratio": self.cache_hit_ratio,
            "elapsed": round(time.time() - self._t0, 1),
        }

    # -- rendering ---------------------------------------------------------
    def render(self, verbose: bool = False) -> str:
        """The wire, as a page. `verbose` is `run.py -v`: it widens what a tool
        answered from a headline to the whole recorded result."""
        result_chars = RESULT_CHARS if verbose else RESULT_LINE
        lines = [RULE, "WIRE TRACE  ·  %d API turn(s)" % len(self.turns), RULE]
        for i, turn in enumerate(self.turns, start=1):
            flags = []
            flags.append("tools=%d" % len(turn["tools"]))
            flags.append("thinking=%s" % (turn["thinking"] or "off"))
            if turn["effort"]:
                flags.append("effort=%s" % turn["effort"])
            # format= and the streamed line below print only when something
            # actually set them. A field that is structurally always off is
            # noise on the one surface this exercise asks people to read.
            if turn["format"]:
                flags.append("format=ON")
            if turn["tool_choice"]:
                flags.append("tool_choice=%s" % turn["tool_choice"])

            lines.append("")
            lines.append("TURN %d  →  messages.%s()   [%d msg in context]"
                         % (i, turn["kind"], turn["n_messages"]))
            lines.append("   sent: " + "  ".join(flags))
            if turn["error"]:
                lines.append("   ←  ERROR: %s" % _short(turn["error"], 120))
                continue
            cache = ""
            if turn.get("cache_read") or turn.get("cache_write"):
                cache = "  cache_r=%d cache_w=%d" % (turn.get("cache_read", 0),
                                                      turn.get("cache_write", 0))
            lines.append("   ←  stop_reason=%s  blocks=[%s]  in=%d out=%d%s  %.1fs"
                         % (turn["stop_reason"], ", ".join(turn["blocks"]) or "-",
                            turn["usage"][0], turn["usage"][1], cache,
                            turn["elapsed"] or 0.0))
            for call in turn["tool_calls"]:
                tag = " [mcp]" if call.get("via_mcp") else ""
                lines.append("      ⚙ %s%s(%s)"
                             % (call["name"], tag, _short(call["input"], 70)))
                # What the tool ANSWERED, under the call that asked for it. The
                # ask and the answer are different facts and three steps of this
                # build tell you to read the second one.
                if call.get("result") is not None:
                    lines.append("        ↩ %s" % _short(call["result"], result_chars))

        s = self.summary()
        lines += [
            "",
            RULE,
            "tool calls: %d  [%s]" % (s["tool_calls"], ", ".join(s["tool_names"]) or "none"),
        ]
        seen = "thinking blocks seen: %s" % s["saw_thinking"]
        if s["used_format"]:
            seen += "   structured format used: True"
        if s["streamed"]:
            seen += "   streamed: True (%d delta(s) read)" % s["deltas_seen"]
        lines.append(seen)
        lines.append("tokens: %d in / %d out    wall clock: %ss"
                     % (s["tokens"]["input"], s["tokens"]["output"], s["elapsed"]))
        if not verbose and any(c.get("result") for c in self.tool_calls):
            lines.append("(tool results shortened to %d characters. run.py -v prints %d.)"
                         % (RESULT_LINE, RESULT_CHARS))
        ratio = s["cache_hit_ratio"]
        if ratio is None:
            lines.append("cache: not used on any turn")
        else:
            lines.append("cache: %d read / %d written   hit ratio %.0f%%"
                         % (s["tokens"]["cache_read"], s["tokens"]["cache_write"], ratio * 100))
        lines.append(RULE)
        return "\n".join(lines)

    def save(self, path: str = ".workshop/last_trace.json") -> str:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        payload = {"summary": self.summary(), "turns": self.turns}
        with open(path, "w") as handle:
            json.dump(payload, handle, indent=2, default=str)
        return path


# ---------------------------------------------------------------------------
# Client wrapper
# ---------------------------------------------------------------------------

class _TracedStream:
    def __init__(self, manager: Any, tracer: Tracer, index: int) -> None:
        self._manager = manager
        self._tracer = tracer
        self._index = index
        self._stream = None

    def __enter__(self):
        self._stream = self._manager.__enter__()
        outer = self

        class _Proxy:
            def __iter__(self):
                for event in outer._stream:
                    outer._tracer.deltas_seen += 1
                    outer._tracer.turns[outer._index]["deltas"] += 1
                    yield event

            def __getattr__(self, item):
                return getattr(outer._stream, item)

            def get_final_message(self):
                message = outer._stream.get_final_message()
                outer._tracer.close_turn(outer._index, message)
                return message

        return _Proxy()

    def __exit__(self, *exc):
        return self._manager.__exit__(*exc)


class _TracedMessages:
    def __init__(self, inner: Any, tracer: Tracer) -> None:
        self._inner = inner
        self._tracer = tracer

    def create(self, **kwargs):
        index = self._tracer.open_turn("create", kwargs)
        try:
            response = self._inner.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced to the learner verbatim
            self._tracer.close_turn(index, error="%s: %s" % (type(exc).__name__, exc))
            raise
        self._tracer.close_turn(index, response)
        return response

    def stream(self, **kwargs):
        index = self._tracer.open_turn("stream", kwargs)
        return _TracedStream(self._inner.stream(**kwargs), self._tracer, index)

    def __getattr__(self, item):
        return getattr(self._inner, item)


class TracedClient:
    def __init__(self, client: Any, tracer: Tracer) -> None:
        self._client = client
        self.messages = _TracedMessages(client.messages, tracer)
        self.tracer = tracer

    def __getattr__(self, item):
        return getattr(self._client, item)


def wrap(client: Any, tracer: Tracer) -> TracedClient:
    """Wrap an Anthropic client so every call lands in `tracer`."""
    global _ACTIVE
    _ACTIVE = tracer
    return TracedClient(client, tracer)


# ---------------------------------------------------------------------------
# Tool results
#
# The tracer sees the request/response cycle, which is where a tool CALL shows
# up. What the tool ANSWERED happens outside that cycle (in support/tools.py
# for the given nine, in agent.py's LOCAL_TOOLS for the ones you add), so it
# has to be handed back in. wrap() marks the current conversation's tracer as
# the one to hand it to, and one conversation runs at a time.
#
# CONTRACT, not an observation: one conversation at a time. Everything in this
# pack that runs more than one (bench.py, eval_harness.py, the gates) runs them
# sequentially. Run two in parallel threads and results get attributed to the
# wrong tracer, silently, because attribution goes through the module global
# below rather than through the call.
# ---------------------------------------------------------------------------

_ACTIVE: Optional[Tracer] = None


def record_tool_result(name: str, output: Any) -> bool:
    """Attach what a tool returned to the call that asked for it. Never raises:
    a missing tracer means somebody ran a tool outside a traced conversation,
    which is not an error, it just has nowhere to be recorded."""
    tracer = _ACTIVE
    if tracer is None:
        return False
    try:
        return tracer.record_result(name, output)
    except Exception:  # noqa: BLE001 - recording evidence must never break a run
        return False


# ---------------------------------------------------------------------------
# What the tool list costs, before a single conversation runs
# ---------------------------------------------------------------------------
_TAX_PROBE = [{"role": "user", "content": "."}]


def tool_schema_tokens(tools, client=None, model=None, per_tool=False):
    """What the schemas on the wire cost, in total and optionally per tool.

    Returns (total, {name: tokens}, how). `how` is "counted" when the SDK's
    token counter answered (deterministic, no generation, and it prices the
    schema exactly as the API will) and "estimated" when it could not, in which
    case the numbers are len(json)/4 and must be labeled as estimates wherever
    they are printed.

    `total` is the whole list's tax: what having these tools costs above having
    none. The per-tool numbers are MARGINAL, measured leave-one-out, so each one
    answers "what would dropping this tool save me". They do not add up to the
    total exactly, because a tokenizer does not decompose. Marginal is the
    number that matters: the question at 2.1 is what the tenth tool cost.

    This is the honest instrument for that question. Tokens in on a live
    conversation moves with what the model chose to do; this does not move.
    """
    schemas = list(tools or [])
    if not schemas:
        return 0, {}, "counted"

    if client is None or model is None:
        return _estimated(schemas, per_tool)

    def count(subset):
        kwargs = {"model": model, "messages": _TAX_PROBE}
        if subset:
            kwargs["tools"] = subset
        return client.messages.count_tokens(**kwargs).input_tokens

    try:
        floor = count([])
        whole = count(schemas)
        total = whole - floor
        per = {}
        if per_tool:
            for i, schema in enumerate(schemas):
                rest = schemas[:i] + schemas[i + 1:]
                per[schema.get("name", "?")] = whole - (count(rest) if rest else floor)
    except Exception:  # noqa: BLE001 - an estimate that says so beats no number
        return _estimated(schemas, per_tool)
    return total, per, "counted"


def _estimated(schemas, per_tool=True):
    per = {s.get("name", "?"): len(json.dumps(s, default=str)) // 4 for s in schemas}
    return sum(per.values()), (per if per_tool else {}), "estimated"
