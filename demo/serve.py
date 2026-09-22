#!/usr/bin/env python3
"""demo/serve.py: the client-facing surface. GIVEN.

    python3 demo/serve.py            # then open http://localhost:4390

Two panes. On the left, the chat a customer would see. On the right, the evidence
a sponsor would ask for: your bench numbers, your eval gates, your safety check.

Open this FIRST, before you change anything. On a fresh clone the agent behind it
does not answer yet, and the card that comes back instead is the first thing the
build asks you to read. Leave it running in its own terminal tab: every step ends
by sending the same message again and watching the answer change.

Why this is given rather than built: a terminal is not a demo, and writing a web
app is not what this half-day is about. The evidence is the work. Making the
surface yours is the stretch, not the task.

It calls YOUR run_agent(pnr, last_name, message). Nothing here knows or cares how
you implemented it, so it works with whatever you built.

Endpoints, all stdlib, no dependencies:

    POST /api/chat      {pnr, last_name, message} -> reply + this turn's trace
    GET  /api/evidence  reads .workshop/ and tells the panel what is true
    POST /api/confirm   {hold_id} -> the customer's own click, and a real token

That last one is the safety check, live in a browser. hold_seat is reversible so the
agent may call it. confirm_rebooking is not, so it needs a token only the
customer's click can produce, and this endpoint is that click. Try it with a
made-up token and watch it refuse.

NOTHING here ever answers with a traceback. A half-built agent is the normal
state of this repo for most of two sessions, so the three things that can go
wrong are told apart and each one comes back as a card the room can read:

    api       the Messages API refused the conversation your loop sent
    backend   the Larkspur mock said no (an expired hold, an unknown one)
    bug       something in this file broke, which is not a workshop step

Only the third one carries a traceback, and the page keeps it folded away.
"""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4390
WORKSHOP = os.path.join(ROOT, ".workshop")


def load_agent():
    """Re-imported per request so an edit to agent.py shows up without a restart.

    ONLY agent.py is dropped. `support/` is given, never edited, and it holds
    live state: mock_backend._holds is where a seat hold lives between the
    message that created it and the customer's Confirm click. Purging support
    here threw that dictionary away on every message, so a Confirm card from one
    message was already dead by the next one. It also holds support.LAST, which
    is where the tracer from the conversation that just ran waits to be read.
    """
    sys.modules.pop("agent", None)
    import agent
    return agent


def _last_tracer():
    """The tracer from the conversation that just ran. It lives on support.LAST,
    put there by new_session(), and support is never purged here."""
    return getattr(getattr(sys.modules.get("support"), "LAST", None), "tracer", None)


def read_json(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------------------
# Errors, told apart
# ---------------------------------------------------------------------------
def _api_error_types():
    """The SDK's own exception base, if the SDK is importable at all."""
    try:
        import anthropic
    except Exception:  # noqa: BLE001
        return ()
    base = getattr(anthropic, "APIError", None)
    return (base,) if isinstance(base, type) else ()


def _api_message(exc) -> str:
    """The API's own sentence, whole. Never shortened: the clause that names
    what the API would not accept is usually the last one."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])
        if body.get("message"):
            return str(body["message"])
    return getattr(exc, "message", None) or str(exc)


def _raised_in_agent(exc) -> bool:
    """True when the deepest frame of the traceback is in the file the
    participant edits. Mid-edit Python errors belong to them, not to us."""
    target = os.path.join(ROOT, "agent.py")
    tb = getattr(exc, "__traceback__", None)
    frame_file = None
    while tb is not None:
        frame_file = tb.tb_frame.f_code.co_filename
        tb = tb.tb_next
    return bool(frame_file) and os.path.abspath(frame_file) == os.path.abspath(target)


def _kind_of(exc) -> str:
    if _api_error_types() and isinstance(exc, _api_error_types()):
        return "api"
    if type(exc).__module__.startswith("support"):
        return "backend"
    return "bug"


def error_card(exc, tracer=None, pnr="") -> dict:
    """One error, as a card. `detail` is the source's own words for api and
    backend; only a bug in this file gets a traceback, and the page folds it."""
    kind = _kind_of(exc)

    if kind == "api":
        turn = len(getattr(tracer, "turns", []) or []) or 1
        run = "python3 run.py %s --trace" % (pnr or "<PNR>")
        return {
            "kind": "api",
            "title": "The API refused turn %d of this conversation" % turn,
            "detail": _api_message(exc),
            "hint": "Your agent stopped on turn %d. Before step 1.2 this is "
                    "expected. Read it in the terminal: %s" % (turn, run),
        }

    if kind == "backend":
        return {
            "kind": "backend",
            "title": "The Larkspur backend refused that call",
            "detail": "%s: %s" % (type(exc).__name__, exc),
            "hint": "That is the mock backend answering, not Claude. Read what "
                    "the tool was asked for on the trace.",
        }

    # Everything else is a Python exception, and there are two of those. One
    # came out of the file the participant is editing, which is a normal thing
    # to see mid-edit. The other came out of this file, which is not a step.
    trace = traceback.format_exc()
    if _raised_in_agent(exc):
        return {
            "kind": "bug",
            "title": "agent.py raised %s before the loop finished" % type(exc).__name__,
            "detail": trace,
            "hint": "That is Python in the file you are editing, not the wire. "
                    "Read the same run in the terminal: python3 run.py %s --trace"
                    % (pnr or "<PNR>"),
        }
    return {
        "kind": "bug",
        "title": "The demo surface itself broke",
        "detail": trace,
        "hint": "This is not a workshop step; say so in the room.",
    }


EMPTY_CARD = {
    "kind": "empty",
    "title": "The agent finished and said nothing",
    "detail": "The trace ends on end_turn.",
    "hint": "Which response is run_agent returning?",
}


# ---------------------------------------------------------------------------
# The hold, read off what the tool ANSWERED
# ---------------------------------------------------------------------------
def _parse_result(call) -> dict:
    """A tool result, as a dict, whichever surface recorded it.

    The tracer keeps a result as a shortened JSON *string* (`result_full` at
    grader length, `result` at display length), so it has to be parsed. CALL_LOG
    keeps the object itself. Try the longest recorded form first: a truncated
    string will not parse, and the shorter one is no better.
    """
    for key in ("result_full", "result"):
        value = call.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip().startswith("{"):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def held_from_run(tracer) -> dict | None:
    """What hold_seat answered on this run, or None if it never ran.

    The hold_id is in the RESULT, never in the input. The input carries the
    option_id, which is a different identifier for a different thing: showing it
    on the Confirm card names a flight option the backend has never heard of,
    and clicking Confirm with it cannot resolve to a hold.
    """
    calls = []
    tracer_calls = getattr(tracer, "tool_calls", None) or []
    for call in tracer_calls:
        if call.get("name") == "hold_seat":
            calls.append({"input": call.get("input") or {}, "result": _parse_result(call)})
    if not calls:
        # A participant loop that dispatches its own tools may never touch the
        # tracer's result field. CALL_LOG records the object either way.
        try:
            from support import CALL_LOG
        except Exception:  # noqa: BLE001
            CALL_LOG = []
        for entry in CALL_LOG:
            if entry.get("name") == "hold_seat":
                result = entry.get("result")
                calls.append({"input": entry.get("input") or {},
                              "result": result if isinstance(result, dict) else {}})
    if not calls:
        return None

    last = calls[-1]
    hold_id = last["result"].get("hold_id")
    option_id = last["result"].get("option_id") or last["input"].get("option_id")
    card = {
        "hold_id": hold_id,
        "option_id": option_id,
        "expires_in_minutes": last["result"].get("expires_in_minutes"),
        "live": False,
        "expired": False,
        "why": None,
    }
    if not hold_id:
        card["why"] = ("hold_seat ran and its answer carried no hold id, so there "
                       "is nothing for a Confirm click to resolve.")
        return card

    state = hold_state(hold_id)
    card["live"] = state == "live"
    card["expired"] = state == "expired"
    if state == "missing":
        card["why"] = "The backend has no hold under that id any more."
    elif state == "expired":
        card["why"] = ("the 15 minute hold expired; ask the agent to hold it "
                       "again")
    return card


def hold_state(hold_id: str) -> str:
    """live / expired / missing, read off the backend the same way it reads
    itself: FIXTURE_CLOCK against the hold's own expires_at."""
    try:
        from support import mock_backend as backend
    except Exception:  # noqa: BLE001
        return "missing"
    record = getattr(backend, "_holds", {}).get(hold_id)
    if not record:
        return "missing"
    if backend.FIXTURE_CLOCK > record["expires_at"]:
        return "expired"
    return "live"


def evidence() -> dict:
    """Everything the panel shows, assembled from artifacts the pod actually
    produced. Anything missing is reported as missing rather than faked, because
    a panel with invented numbers on it is worse than an empty one."""
    labels = {}
    if os.path.isdir(WORKSHOP):
        for name in sorted(os.listdir(WORKSHOP)):
            if name.startswith("bench-") and name.endswith(".json"):
                doc = read_json(os.path.join(WORKSHOP, name))
                if doc:
                    labels[doc.get("label") or name[6:-5]] = doc.get("summary")

    evals = read_json(os.path.join(WORKSHOP, "evals.json"))
    profile = read_json(os.path.join(WORKSHOP, "profile.json"), {}) or {}

    pitch_path = os.path.join(ROOT, "PITCH.md")
    pitch = ""
    if os.path.exists(pitch_path):
        with open(pitch_path) as fh:
            pitch = fh.read()

    before = labels.get("before")
    after = labels.get("after")
    deltas = []
    if before and after:
        for key, name, unit, lower_better in (
            ("model_cost_per_contact", "Model cost per resolved contact", "$", True),
            ("p95_s", "p95 latency", "s", True),
            ("input_per_contact", "Input tokens per contact", "", True),
            ("cache_hit_pct", "Cache hit rate", "%", False),
            ("resolved_pct", "Ticket types resolved", "%", False),
        ):
            a, b = before.get(key), after.get(key)
            if a is None and b is None:
                continue
            improved = None
            if a not in (None, 0) and b is not None:
                delta = (b - a) / abs(a)
                # A metric that did not move is neither better nor worse. Left as
                # None so the panel shows a neutral dash rather than calling an
                # unchanged 100% resolution rate a regression.
                if abs(delta) > 1e-9:
                    improved = (delta < 0) if lower_better else (delta > 0)
            deltas.append({"key": key, "name": name, "unit": unit,
                           "before": a, "after": b, "improved": improved})

    return {
        "labels": sorted(labels),
        "before": before,
        "after": after,
        "deltas": deltas,
        "evals": (evals or {}).get("report"),
        "eval_cases": [
            {"id": e["case"]["id"], "suite": e["case"].get("suite"),
             "hard_gate": bool(e["case"].get("hard_gate")), "passed": e["result"]["passed"],
             # PASS / FAIL / UNKNOWN. UNKNOWN means the grader could not read
             # its own judge, so the panel shows it as not scored rather than
             # as a failure of the agent.
             "status": e["result"].get("status")
                       or ("PASS" if e["result"]["passed"] else "FAIL")}
            for e in (evals or {}).get("cases", [])
        ],
        "gates_banked": sorted((profile.get("banked") or {}).keys()),
        "name": profile.get("name"),
        "pitch": pitch,
    }


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def _send(self, payload, code=200):
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}

    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] == "/api/evidence":
            try:
                return self._send(evidence())
            except Exception as exc:  # noqa: BLE001
                return self._send({"error": error_card(exc)}, 200)
        return super().do_GET()

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        body = self._body()

        if path == "/api/chat":
            return self._chat(body)
        if path == "/api/confirm":
            return self._confirm(body)
        return self._send({"error": {
            "kind": "bug", "title": "No such endpoint: %s" % path,
            "detail": path, "hint": "This is not a workshop step; say so in the room."}}, 404)

    # -- chat ---------------------------------------------------------------
    def _chat(self, body):
        t0 = time.time()
        pnr = body.get("pnr", "")
        agent = tracer = None
        try:
            agent = load_agent()
            reply = agent.run_agent(pnr, body.get("last_name", ""), body.get("message", ""))
        except Exception as exc:  # noqa: BLE001
            tracer = _last_tracer()
            return self._send({"error": error_card(exc, tracer, pnr),
                               "wall": round(time.time() - t0, 2)})

        tracer = _last_tracer()
        summary = tracer.summary() if tracer else {}
        payload = {
            "reply": reply,
            "wall": round(time.time() - t0, 2),
            "turns": summary.get("turns"),
            "tool_names": summary.get("tool_names") or [],
            "tokens": summary.get("tokens") or {},
            "cache_hit_ratio": summary.get("cache_hit_ratio"),
            # Surfaced so the browser can offer a real Confirm button, off what
            # hold_seat ANSWERED rather than off what Claude asked for.
            "held": held_from_run(tracer),
        }
        # A loop that holds and returns nothing is the second fault of step 1.2,
        # and it is a different symptom from an error. Named as one, never fixed
        # here: the card asks the question and stops.
        if not (reply or "").strip():
            payload["error"] = dict(EMPTY_CARD)
        return self._send(payload)

    # -- the customer's own click -------------------------------------------
    def _confirm(self, body):
        # This is the ONLY thing that mints a confirmation token, which is the
        # whole point of the safety check.
        try:
            from support import mock_backend as backend
            from support import tools

            hold_id = body.get("hold_id")
            if not hold_id:
                return self._send({"error": {
                    "kind": "backend",
                    "title": "That click carried no hold id",
                    "detail": "POST /api/confirm needs the hold_id hold_seat answered with.",
                    "hint": "Ask the agent to hold a seat, then use the card it puts in the chat.",
                }})

            state = hold_state(hold_id)
            if state == "missing":
                return self._send({"error": {
                    "kind": "backend",
                    "title": "The backend has no hold under %s" % hold_id,
                    "detail": "Holds live in memory for the length of a conversation.",
                    "hint": "Ask the agent to hold a seat again, then use the new card.",
                }})
            if state == "expired":
                return self._send({"error": {
                    "kind": "backend",
                    "title": "That hold is no longer good",
                    "detail": "confirm_rebooking checks expires_at before it checks the token.",
                    "hint": "the 15 minute hold expired; ask the agent to hold it again",
                }})

            if body.get("forge"):
                result = tools.confirm_rebooking(hold_id, "not-a-real-token")
                return self._send({"forged": True, "result": result})
            token = backend.simulate_customer_confirm_click(hold_id)
            result = tools.confirm_rebooking(hold_id, token)
            return self._send({"forged": False, "token": token, "result": result})
        except Exception as exc:  # noqa: BLE001
            return self._send({"error": error_card(exc)})

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            sys.stderr.write("  %s %s\n" % (self.command, self.path))


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print("\n  Larkspur demo  ->  http://localhost:%d" % PORT)
        print("  Ctrl-C stops it. Leave it running in its own terminal tab.")
        print("  chat on the left, your evidence on the right")
        print("  serving %s, calling agent.run_agent from %s\n"
              % (os.path.relpath(HERE, ROOT), os.path.basename(ROOT)))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  stopped")
