"""Larkspur agent tool implementations: GIVEN, do not edit.

Nine tools: four shared reads, five gated writes, the same split the case
study's own architecture uses (a client-owned MCP server for reads, a
decision path for writes). What Claude *sees* about each tool (the schema,
the description that drives selection) is your job, in agent.py's
build_tools(). What each tool actually *does* lives here, and doesn't change
no matter how you describe it.

Two guardrails worth knowing before you build on top of these. Both are the
same idea, made structural instead of written down in a prompt and hoped for:

  - check_policy and search_alternatives do NOT take fare_family, loyalty_tier
    or "is this overnight" as arguments. They re-derive those from the
    booking every call. A model convinced the customer is Summit tier can't
    talk its way into an entitlement the data doesn't support.
  - confirm_rebooking requires a confirmation_token that only
    simulate_customer_confirm_click() can mint. That function is not a tool
    and never will be. "The customer said yes" in a chat message can't
    produce a valid one.

next_available_day() sits at the bottom with them, and is deliberately NOT one
of the nine: nothing offers it to Claude, so nothing can call it. call_local()
is beside it, and it is what runs a tool you registered yourself.
"""

from __future__ import annotations

from . import mock_backend as backend
from .trace import record_tool_result

SSR_INVENTORY_CODES = {"PETC", "MEDA", "OXYG", "STCR", "ESAN"}

CALL_LOG: list = []


def reset_call_log() -> None:
    CALL_LOG.clear()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def get_flight_status(flight_no, date):
    return backend.get_flight_status_raw(flight_no, date)


def lookup_booking(pnr, last_name):
    """Trimmed, tool-vocabulary view: never the raw Altura record."""
    try:
        booking = backend.get_booking_raw(pnr)
    except backend.NotFound as e:
        return {"error": str(e)}
    if not any(p["last_name"].lower() == last_name.strip().lower() for p in booking["passengers"]):
        return {"error": "Name does not match this PNR."}

    seg = backend.get_disrupted_segment(booking)
    return {
        "pnr": booking["pnr"],
        "home_airport": booking["home_airport"],
        "fare_family": booking["fare_family"].lower().replace(" ", "_"),
        "loyalty_tier": booking["loyalty"]["tier"].lower(),
        "language": booking["contact"]["language"],
        "passenger_count": len(booking["passengers"]),
        "is_group_booking": booking.get("is_group_booking", False),
        "has_partner_segment": booking.get("has_partner_segment", False),
        "unaccompanied_minor": booking.get("unaccompanied_minor", False),
        "ssr_codes": [c["code"] for c in booking.get("ssr_codes", [])],
        "segment": {
            "flight_number": seg["flight_no"], "date": seg["date"],
            "origin": seg["origin"], "dest": seg["dest"], "cabin": seg["cabin"],
            "status": seg["status"],
        },
        "untrusted_free_text": {"ops_note": seg.get("ops_note"), "remarks": booking.get("remarks", [])},
    }


def search_alternatives(pnr):
    try:
        booking = backend.get_booking_raw(pnr)
    except backend.NotFound as e:
        return {"error": str(e)}
    seg = backend.get_disrupted_segment(booking)
    return backend.search_alternatives_raw(
        seg["origin"], seg["dest"], seg["date"], seg["cabin"],
        pax_count=len(booking["passengers"]), exclude_flight_no=seg["flight_no"],
    )


def check_policy(pnr, cause_code, delay_minutes, status, wait_minutes_for_alternative=None,
                  chosen_option_id=None):
    try:
        booking = backend.get_booking_raw(pnr)
    except backend.NotFound as e:
        return {"error": str(e)}
    seg = backend.get_disrupted_segment(booking)

    if chosen_option_id:
        alt_date = next((o["date"] for o in backend.search_alternatives_raw(
            seg["origin"], seg["dest"], seg["date"], seg["cabin"],
            exclude_flight_no=seg["flight_no"])["options"] if o["option_id"] == chosen_option_id), None)
    else:
        alt_date = backend.earliest_alternative_date(
            seg["origin"], seg["dest"], seg["date"], seg["cabin"], len(booking["passengers"]))
    overnight = bool(alt_date and alt_date > seg["date"] and seg["origin"] != booking["home_airport"])

    escalation_context = {
        "has_partner_segment": booking.get("has_partner_segment", False),
        "is_group_booking": booking.get("is_group_booking", False),
        "unaccompanied_minor": booking.get("unaccompanied_minor", False),
        "ssr_inventory_required": any(c["code"] in SSR_INVENTORY_CODES for c in booking.get("ssr_codes", [])),
    }
    return backend.resolve_policy(
        cause_code=cause_code, delay_minutes=delay_minutes, status=status,
        fare_family=booking["fare_family"], loyalty_tier=booking["loyalty"]["tier"],
        overnight=overnight, wait_minutes_for_alternative=wait_minutes_for_alternative,
        escalation_context=escalation_context,
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def hold_seat(option_id, pnr):
    return backend.hold_seat(option_id, pnr)


def confirm_rebooking(hold_id, confirmation_token):
    return backend.confirm_rebooking(hold_id, confirmation_token)


def issue_voucher(voucher_type, amount_usd, pnr, policy_row_id):
    return backend.issue_voucher(voucher_type, amount_usd, pnr, policy_row_id)


def escalate_to_human(pnr, reason, summary_for_human, queue=None):
    return backend.escalate_to_human(pnr, reason, summary_for_human, queue)


def send_confirmation(pnr, message):
    return backend.send_confirmation(pnr, message)


TOOL_FUNCTIONS = {
    "get_flight_status": get_flight_status,
    "lookup_booking": lookup_booking,
    "search_alternatives": search_alternatives,
    "check_policy": check_policy,
    "hold_seat": hold_seat,
    "confirm_rebooking": confirm_rebooking,
    "issue_voucher": issue_voucher,
    "escalate_to_human": escalate_to_human,
    "send_confirmation": send_confirmation,
}


def next_available_day(origin, dest, date, cabin="Y"):
    """The earliest date with an open seat. Not one of the nine, and not in
    TOOL_FUNCTIONS: the backend function existed all along and nothing had told
    Claude it was there.

    pax_count never crosses this function on purpose. The backend then answers
    for a party of one, and finding that gap is Build 3's capacity case.
    """
    return backend.earliest_alternative_date(origin, dest, date, cabin)


def call_local(fn, name, args):
    """Run one tool that lives in agent.py's LOCAL_TOOLS, and record what it
    answered so the trace (and the eval judge reading it) sees the answer and
    not only the ask.

    Bad arguments come back as an error dict rather than an exception, the same
    way execute_tool() answers for the given nine: the model can read that and
    try again, and the loop never stalls on an unanswered tool_use id.
    """
    try:
        output = fn(**dict(args or {}))
    except TypeError as exc:
        output = {"error": "Bad arguments for %s: %s" % (name, exc)}
    record_tool_result(name, output)
    return output


def execute_tool(name, tool_input):
    """Dispatch one tool call and log it: agent.py calls this, never the
    functions above directly, so every call is auditable the same way."""
    entry = {"name": name, "input": dict(tool_input), "result": None}
    CALL_LOG.append(entry)
    if name not in TOOL_FUNCTIONS:
        entry["result"] = {"error": f"Unknown tool {name}"}
        return entry["result"]
    try:
        entry["result"] = TOOL_FUNCTIONS[name](**tool_input)
    except TypeError as exc:
        # Claude sent arguments that don't match the schema you wrote in
        # agent.py. That's a schema bug to fix, not a crash to explain away.
        entry["result"] = {"error": f"Bad arguments for {name}: {exc}"}
    # The result is logged, not just the call. An audit trail that records the
    # ask and not the answer cannot tell you what a hold actually returned,
    # which is what demo/serve.py needs to offer a real Confirm button, and
    # what an eval judge needs before it can say the agent read the row right.
    record_tool_result(name, entry["result"])
    return entry["result"]
