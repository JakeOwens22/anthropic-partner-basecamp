#!/usr/bin/env python3
"""eval_harness.py: run your eval cases against your agent. GIVEN.

    python3 eval_harness.py                  # run evals/cases.json
    python3 eval_harness.py --example        # run the 5 given examples instead
    python3 eval_harness.py --cases p.json   # run some other case file
    python3 eval_harness.py --case tone-0101 # one case
    python3 eval_harness.py --show           # what's in your case file, no API calls

You write the cases. This runs them, grades them, and applies the gates.

The contract is Larkspur's own, from the engagement (case study, beat 6):

    one call per transcript per suite
    evidence quoted per criterion BEFORE any verdict
    verdict is PASS | FAIL | UNKNOWN, schema-enforced
    UNKNOWN counts as FAIL and queues for the human panel
    one FAIL in a hard-gate suite blocks the release candidate

One thing is NOT in that contract, and this file holds the line on it: a judge
whose reply cannot be read is a grader failure, not an agent failure. When
the judge's reply does not parse as JSON, the judge is asked once more for JSON
only. If that reply does not parse either, the case is recorded as UNKNOWN,
printed as such, left out of the pass rate on both sides, and never counted as
a FAIL against the agent. A verdict of UNKNOWN that the judge itself chose (the
transcript was ambiguous) is a different thing and still counts as a failure,
exactly as the contract above says.

That last line is the whole point. A release does not ship on an average. It
ships when no hard gate failed.

Three grader types, and a case may carry more than one. ALL of a case's graders
must pass for the case to pass:

    rules    : deterministic, on the wire. must_call / must_not_call.
               Free, instant, and the right grader for an irreversible action.
    lexicon  : deterministic, on the text. must_contain / must_not_contain.
               Cheap. Brittle if you use it for anything subtle.
    judge    : a model call against the case's `expect` prose. It is shown the
               customer's message, every tool call WITH what that tool returned,
               and the agent's reply. The only grader that can read intent, and
               the only one that can itself be wrong. Version your rubric.

Larkspur's week 8: one suite fell to 37 of 48 overnight and nine of eleven
failures were the grader, not the agent. Fixed in 40 minutes, no rollback. That
is why the judge here reports its own evidence: so you can tell those apart.

THE JUDGE MODEL, and why it is not the agent's. Never let a model grade its own
output. A judge on the same model id as the thing it is grading shares the
blind spot that produced the answer: it reads its own phrasing as correct,
because that is the phrasing it would have chosen. So JUDGE_MODEL defaults to
claude-opus-4-8 while the agent runs on support/data.py's MODEL, a different
tier. Up rather than down, on purpose: a judge has to be able to catch a fluent
answer that contradicts a tool result, and a cheaper judge than the agent is a
grader you cannot appeal to. It costs more per case and it is slower, which is
the judge grader's charge and is worth naming out loud.

Swapping it is one environment variable, and doing so is the fastest way to see
what a rubric actually rests on:

    LARKSPUR_JUDGE_MODEL=claude-haiku-4-5 python3 eval_harness.py

Diff the verdicts against the default run and write one line on which cases
moved. A rubric that survives a judge swap is a rubric.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

WORKSHOP = os.path.join(HERE, ".workshop")
CASES_PATH = os.path.join(HERE, "evals", "cases.json")
EXAMPLE_PATH = os.path.join(HERE, "evals", "cases.example.json")

JUDGE_MODEL = os.environ.get("LARKSPUR_JUDGE_MODEL", "claude-opus-4-8")
RUBRIC_VERSION = "v2"   # v1 over-specified grnd-0101; see evals/GRADER-BUG.md

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Verbatim quotes from the transcript. Quote before you judge.",
        },
        "verdict": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
        "why": {"type": "string", "description": "One sentence, referring to the evidence."},
    },
    "required": ["evidence", "verdict", "why"],
    "additionalProperties": False,
}

JUDGE_SYSTEM = """You grade one transcript from an airline disruption-care agent
against one written expectation. You are strict and you are literal.

Rules you follow without exception:

1. Quote the evidence first. Every quote must be verbatim from the transcript.
2. Then give the verdict. PASS only if the expectation is met as written.
3. If the transcript is ambiguous, or the expectation does not clearly apply,
   return UNKNOWN. Do not guess. UNKNOWN is counted as a failure and sent to a
   human panel, which is the correct outcome for a case you cannot call.
4. You are grading against the expectation, not against your own taste. A reply
   you would have written differently still passes if it meets the expectation.
5. Absence matters. If the expectation says the agent must escalate and the
   transcript never escalates, that is a FAIL even if everything said was true.
6. You are shown what each tool RETURNED, not only what the agent asked for.
   Ground your verdict in those results. If the agent stated something the tool
   results do not support, that is a finding and you must quote the result you
   are contradicting. If a result is truncated or missing, say so and do not
   assume what it contained."""


# ---------------------------------------------------------------------------
# Graders
# ---------------------------------------------------------------------------
def grade_rules(spec, transcript) -> dict:
    called = transcript["tool_names"]
    problems = []
    for name in spec.get("must_call", []):
        if name not in called:
            problems.append("never called %s" % name)
    for name in spec.get("must_not_call", []):
        if name in called:
            problems.append("called %s, which this case forbids" % name)
    return {
        "grader": "rules",
        "verdict": "FAIL" if problems else "PASS",
        "why": "; ".join(problems) or "tool calls matched the rule",
        "evidence": ["tools called: %s" % (", ".join(called) or "none")],
    }


def grade_lexicon(spec, transcript) -> dict:
    text = (transcript["reply"] or "").lower()
    problems, hits = [], []
    for phrase in spec.get("must_contain", []):
        if phrase.lower() in text:
            hits.append("found '%s'" % phrase)
        else:
            problems.append("missing '%s'" % phrase)
    for phrase in spec.get("must_not_contain", []):
        if phrase.lower() in text:
            problems.append("contains '%s', which this case forbids" % phrase)
    return {
        "grader": "lexicon",
        "verdict": "FAIL" if problems else "PASS",
        "why": "; ".join(problems) or "; ".join(hits) or "no lexicon constraints",
        "evidence": hits,
    }


# The whole tool-evidence block, not per call. Two fences, and they are set in
# the right order now: support/trace.py caps each recorded result at grader
# length, and this caps the block. It was 4,000 while a single result could be
# 4,000, which meant one large policy row could push every later call off the
# end of the evidence the judge was shown.
EVIDENCE_CHAR_CAP = 12000


def tool_evidence(transcript, cap: int = EVIDENCE_CHAR_CAP) -> str:
    """What each tool was asked and what it answered, in order.

    A judge shown only the tool NAMES has to take the agent's word for what the
    policy row said. Shown the results, it can catch the case that matters most:
    a fluent reply that contradicts the data it was handed. Capped, because a
    judge prompt is a cost line too.

    `result_full` is the grader's copy, recorded at grader length by
    support/trace.py. `result` is what the trace prints, and a page-layout
    number has no business deciding whether the judge can see the entitlement it
    was asked to verify. Falls back to `result` for a trace recorded before the
    two lengths existed.
    """
    calls = transcript.get("tool_calls") or []
    if not calls:
        return "none"
    lines, used = [], 0
    for i, call in enumerate(calls, start=1):
        args = json.dumps(call.get("input") or {}, default=str)
        if len(args) > 200:
            args = args[:199] + "…"
        result = call.get("result_full") or call.get("result")
        if result is None:
            result = "(result not captured)"
        line = "%d. %s(%s)\n   -> %s" % (i, call.get("name", "?"), args, result)
        if used + len(line) > cap:
            lines.append("… %d more tool call(s), not shown (evidence cap)"
                         % (len(calls) - i + 1))
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


JUDGE_JSON_NUDGE = (
    "\n\nYour previous reply could not be read as JSON. Send the JSON object and "
    "nothing else: no sentence before it, no sentence after it, no code fence."
)


def grade_judge(spec, transcript, case, client) -> dict:
    """One judge call, and one retry if the reply will not parse.

    `unreadable` on the returned dict is the flag the rest of this file keys
    off: it says the grader could not read its own judge, which is a grader
    defect. A judge that answered and chose UNKNOWN is not unreadable, and it
    still counts as a failure under the contract in the module docstring.
    """
    prompt = (
        "EXPECTATION\n%s\n\n"
        "WHAT THE CUSTOMER SAID\n%s\n\n"
        "TOOLS THE AGENT CALLED, IN ORDER, AND WHAT EACH ONE RETURNED\n"
        "<<<TOOL_EVIDENCE\n%s\n>>>TOOL_EVIDENCE\n\n"
        "WHAT THE AGENT REPLIED\n%s\n"
        % (case.get("expect", "(no expectation written)"),
           case.get("message", ""),
           tool_evidence(transcript),
           transcript["reply"] or "(empty reply)")
    )
    unparsed = ""
    for attempt in (1, 2):
        ask = prompt if attempt == 1 else prompt + JUDGE_JSON_NUDGE
        try:
            response = client.messages.create(
                model=JUDGE_MODEL, max_tokens=1500, system=JUDGE_SYSTEM,
                output_config={"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
                messages=[{"role": "user", "content": ask}],
            )
        except Exception as exc:  # noqa: BLE001: a judge that errors is UNKNOWN, not PASS
            # A call that never returned is left on the contract's own terms:
            # UNKNOWN, counted as a failure, with the exception named so a
            # facilitator can tell a rate limit from a bad model id.
            return {"grader": "judge", "verdict": "UNKNOWN", "evidence": [],
                    "why": "judge call failed: %s: %s" % (type(exc).__name__, exc)}

        text = "".join(b.text for b in response.content
                       if getattr(b, "type", None) == "text")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            unparsed = text
            continue
        payload["grader"] = "judge"
        payload.setdefault("verdict", "UNKNOWN")
        if attempt == 2:
            payload["why"] = ("%s  (the judge was asked a second time for JSON only)"
                              % (payload.get("why") or "")).strip()
        return payload

    head = " ".join((unparsed or "").split())[:120] or "(empty reply)"
    return {"grader": "judge", "verdict": "UNKNOWN", "unreadable": True,
            "evidence": ["what came back instead of JSON: %s" % head],
            "why": "the judge's reply did not parse as JSON, and did not parse after "
                   "one retry asking for JSON only. That is this grader failing to "
                   "read its own judge, so this case is not scored either way"}


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
def load_agent():
    for name in list(sys.modules):
        if name == "agent" or name.startswith("support"):
            del sys.modules[name]
    import agent
    return agent


def run_case(agent, case) -> dict:
    t0 = time.time()
    try:
        reply = agent.run_agent(case["pnr"], case["last_name"], case["message"])
        error = None
    except Exception as exc:  # noqa: BLE001
        reply, error = "", "%s: %s" % (type(exc).__name__, exc)
    # support.LAST, put there by new_session(). Read out of sys.modules because
    # load_agent() above purges and re-imports the package.
    tracer = getattr(getattr(sys.modules.get("support"), "LAST", None), "tracer", None)
    return {
        "reply": reply,
        "error": error,
        "tool_names": tracer.tool_names if tracer else [],
        # name + input + a truncated result summary per call. The judge reads
        # this; grade_rules only ever needs the names above.
        "tool_calls": tracer.tool_calls if tracer else [],
        "turns": len(tracer.turns) if tracer else 0,
        "wall": round(time.time() - t0, 2),
    }


def grade_case(case, transcript, client) -> dict:
    graders = case.get("graders") or [{"type": "judge"}]
    results = []
    for spec in graders:
        kind = spec.get("type")
        if kind == "rules":
            results.append(grade_rules(spec, transcript))
        elif kind == "lexicon":
            results.append(grade_lexicon(spec, transcript))
        elif kind == "judge":
            results.append(grade_judge(spec, transcript, case, client))
        else:
            results.append({"grader": kind or "?", "verdict": "UNKNOWN", "evidence": [],
                            "why": "unknown grader type %r" % kind})

    # The agent raising is the agent's own failure, and it outranks everything
    # else: there is no transcript worth grading.
    if transcript["error"]:
        results.append({"grader": "run", "verdict": "FAIL", "evidence": [],
                        "why": "the agent raised: %s" % transcript["error"]})
        return {"status": "FAIL", "passed": False, "graders": results}

    # A grader that could not read its own judge does not get to call this
    # case. UNKNOWN, out of the pass rate on both sides, and never a FAIL
    # charged to the agent.
    if any(r.get("unreadable") for r in results):
        return {"status": "UNKNOWN", "passed": False, "graders": results}

    # UNKNOWN counts as FAIL, and every grader must pass.
    passed = all(r["verdict"] == "PASS" for r in results)
    return {"status": "PASS" if passed else "FAIL", "passed": passed, "graders": results}


def gate_report(cases, results) -> dict:
    """`cases` is how many RAN, including the ones whose grader could not be
    read. Those go in `unknown` and are out of `scored`, which is the pass
    rate's denominator on both sides."""
    suites = {}
    for case, res in zip(cases, results):
        s = suites.setdefault(case.get("suite", "unsuited"),
                              {"passed": 0, "failed": 0, "unknown": 0,
                               "hard_gate": False, "failures": [], "unreadable": []})
        s["hard_gate"] = s["hard_gate"] or bool(case.get("hard_gate"))
        status = res.get("status") or ("PASS" if res.get("passed") else "FAIL")
        if status == "UNKNOWN":
            s["unknown"] += 1
            s["unreadable"].append(case["id"])
        elif status == "PASS":
            s["passed"] += 1
        else:
            s["failed"] += 1
            s["failures"].append(case["id"])

    # A hard gate blocks on a FAIL. It does not block on a case nobody could
    # grade: that case is unresolved, which is said out loud instead.
    blocking = [name for name, s in suites.items() if s["hard_gate"] and s["failed"]]
    statuses = [r.get("status") or ("PASS" if r.get("passed") else "FAIL") for r in results]
    unknown_ids = [c["id"] for c, st in zip(cases, statuses) if st == "UNKNOWN"]
    scored = len(results) - len(unknown_ids)
    passed = sum(1 for st in statuses if st == "PASS")
    return {
        "rubric_version": RUBRIC_VERSION,
        "judge_model": JUDGE_MODEL,
        "cases": len(results),
        "scored": scored,
        "unknown": len(unknown_ids),
        "unknown_ids": unknown_ids,
        "passed": passed,
        "pass_rate": round(100.0 * passed / (scored or 1), 1),
        "suites": suites,
        "blocking_suites": blocking,
        "release": "BLOCKED" if blocking else "CLEAR",
    }


def render(cases, results, report) -> str:
    L = ["", "─" * 74, "EVALS   rubric %s   judge %s" % (report["rubric_version"], report["judge_model"]),
         "─" * 74]
    for case, res in zip(cases, results):
        status = res.get("status") or ("PASS" if res.get("passed") else "FAIL")
        mark = {"PASS": "PASS", "FAIL": "FAIL", "UNKNOWN": "UNKN"}[status]
        gate = " [hard gate]" if case.get("hard_gate") else ""
        L.append("  %-4s %-14s %-13s %s%s" % (mark, case["id"], case.get("suite", "-"),
                                               case.get("shape", ""), gate))
        if status != "PASS":
            for g in res["graders"]:
                if g["verdict"] != "PASS":
                    L.append("         %s → %s: %s" % (g["grader"], g["verdict"], g["why"]))
                    for quote in (g.get("evidence") or [])[:2]:
                        L.append("           \"%s\"" % str(quote)[:96])
    L += ["─" * 74]
    if report.get("unknown"):
        L.append("  THE GRADER COULD NOT READ ITS OWN JUDGE on %d case(s): %s"
                 % (report["unknown"], ", ".join(report["unknown_ids"])))
        L.append("  Marked UNKN, left out of the pass rate on both sides, and not counted")
        L.append("  against your agent. Re-run those cases. If it keeps happening, the")
        L.append("  suspect is the rubric and the judge, not the build: evals/GRADER-BUG.md.")
        L.append("")
    L.append("  %d/%d scored cases passed  (%.0f%%)%s"
             % (report["passed"], report.get("scored", report["cases"]), report["pass_rate"],
                "" if not report.get("unknown")
                else ", %d not scored" % report["unknown"]))
    for name, s in sorted(report["suites"].items()):
        flag = "  HARD GATE" if s["hard_gate"] else ""
        unk = "  %d not scored" % s["unknown"] if s.get("unknown") else ""
        L.append("  %-16s %d passed  %d failed%s%s"
                 % (name, s["passed"], s["failed"], unk, flag))
    L.append("")
    if report["blocking_suites"]:
        L.append("  RELEASE BLOCKED by: %s" % ", ".join(report["blocking_suites"]))
        L.append("  One failure in a hard-gate suite blocks a release. Not an average.")
    elif report.get("unknown"):
        L.append("  RELEASE CLEAR: no hard gate failed. %d case(s) are still unresolved,"
                 % report["unknown"])
        L.append("  because the grader could not read its own judge on them.")
    else:
        L.append("  RELEASE CLEAR: no hard gate failed.")
    L += ["─" * 74, ""]
    return "\n".join(L)


def _shown(path: str) -> str:
    """Relative inside the exercise, as given anywhere else. `--cases` can point
    at another team's clone, and eleven `../` are not a helpful error message."""
    rel = os.path.relpath(path, HERE)
    return path if rel.startswith("..") else rel


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--example", action="store_true", help="run the given examples")
    ap.add_argument("--cases", metavar="PATH",
                    help="run a different case file: Build 3's stretch ('Agent, or grader?': "
                         "run the same agent against the v1 and the v2 rubric and see which "
                         "one moved) needs this")
    ap.add_argument("--case", help="run one case by id")
    ap.add_argument("--show", action="store_true", help="list cases, make no API calls")
    args = ap.parse_args()

    path = args.cases or (EXAMPLE_PATH if args.example else CASES_PATH)
    if not os.path.exists(path):
        print("No case file at %s" % _shown(path))
        if not (args.example or args.cases):
            print("\nStart from the examples:")
            print("  cp evals/cases.example.json evals/cases.json")
            print("\nThen make them yours. Three cases, at least two hard gates.")
        return 1

    cases = json.load(open(path))["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print("No case with id %r" % args.case)
            return 1

    if args.show:
        print("\n%-14s %-14s %-7s %s" % ("ID", "SUITE", "GATE", "GRADERS"))
        for c in cases:
            print("%-14s %-14s %-7s %s" % (
                c["id"], c.get("suite", "-"), "hard" if c.get("hard_gate") else "soft",
                ", ".join(g.get("type", "?") for g in (c.get("graders") or [{"type": "judge"}]))))
        print("\n%d cases, %d hard gates.\n"
              % (len(cases), sum(1 for c in cases if c.get("hard_gate"))))
        return 0

    agent = load_agent()
    from support import get_client
    client = get_client()

    print("\nRunning %d case(s) against your agent.\n" % len(cases))
    results = []
    for case in cases:
        sys.stdout.write("  %-14s %-13s " % (case["id"], case.get("suite", "-")))
        sys.stdout.flush()
        transcript = run_case(agent, case)
        result = grade_case(case, transcript, client)
        result["transcript"] = transcript
        results.append(result)
        sys.stdout.write("%s  (%.1fs)\n"
                         % ({"PASS": "PASS", "FAIL": "FAIL",
                             "UNKNOWN": "UNKN  the grader could not read its own judge"}[
                                result.get("status", "FAIL")],
                            transcript["wall"]))

    report = gate_report(cases, results)
    print(render(cases, results, report))

    os.makedirs(WORKSHOP, exist_ok=True)
    out = os.path.join(WORKSHOP, "evals.json")
    with open(out, "w") as fh:
        json.dump({"report": report,
                   "case_file": _shown(path),
                   "cases": [{"case": c, "result": r} for c, r in zip(cases, results)]},
                  fh, indent=2, default=str)
    print("Saved %s" % os.path.relpath(out, HERE))
    return 0 if not report["blocking_suites"] else 2


if __name__ == "__main__":
    sys.exit(main())
