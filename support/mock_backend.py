"""
Larkspur Airlines mock backend.

Stands in for the three live systems Larkspur's real agent would talk to:
OpsFeed (flight status), Altura (bookings + availability search) and CareDesk
policy. Nothing here calls a network. Every function reads one of the files
in ../data/americas/ so a room full of laptops gets identical, repeatable
answers. `tools.py` wraps these functions in the shape Claude's tool-use API
expects; this file owns the data and the policy-resolution math.

Run `python mock_backend.py --selftest` after any edit: it replays the seven
worked `examples[]` in disruption_policy.json through resolve_policy() and
walks the five Stage-1 PNRs through the read path, so a mistake in the
resolution algorithm fails loudly instead of quietly teaching the wrong thing.
"""

import csv
import json
import re
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "americas"

# ---------------------------------------------------------------------------
# Fixture clock: every "now" in this pack is this one frozen moment: 68
# minutes into the Denver convective-weather ground stop, Thursday 8 May 2025.
# ---------------------------------------------------------------------------
FIXTURE_CLOCK = datetime.fromisoformat("2025-05-08T14:10:00-06:00")
DATA_HORIZON_START = "2025-05-07"
DATA_HORIZON_END = "2025-05-09"

# OpsFeed is an ISO-8601 feed and it is strict about it. A date in any other
# shape is refused at the edge rather than quietly missed, because "you sent me
# the wrong shape" and "I have no record of that flight" are different facts and
# a tool that blurs them teaches its caller nothing.
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TIER_RANK = {"Member": 0, "Silver": 1, "Gold": 2, "Summit": 3}


class NotFound(Exception):
    """A lookup failed in a way a real system would surface as data, not a crash."""


# ---------------------------------------------------------------------------
# Loaders: cached at module scope, since a classroom re-runs cells a lot.
# ---------------------------------------------------------------------------
_cache = {}


def _load_json(name):
    if name not in _cache:
        with open(DATA_DIR / name, encoding="utf-8") as f:
            _cache[name] = json.load(f)
    return _cache[name]


def load_bookings():
    return _load_json("bookings.json")


def load_policy():
    return _load_json("disruption_policy.json")


def load_alternatives():
    return _load_json("alternatives_cache.json")


def load_flights():
    if "flights.csv" not in _cache:
        rows = []
        with open(DATA_DIR / "flights.csv", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                row["delay_min"] = int(row["delay_min"])
                row["seats_left_Y"] = int(row["seats_left_Y"])
                row["seats_left_J"] = int(row["seats_left_J"])
                rows.append(row)
        _cache["flights.csv"] = rows
    return _cache["flights.csv"]


# ---------------------------------------------------------------------------
# get_flight_status backend: OpsFeed stand-in
# ---------------------------------------------------------------------------
def get_flight_status_raw(flight_no, date):
    """Returns one flights.csv row, event-enriched, a NOT_IN_HORIZON marker, or
    an error if `date` is not ISO-8601."""
    if not ISO_DATE.match(str(date)):
        return {"error": "date must be YYYY-MM-DD, got %s" % date}
    if not (DATA_HORIZON_START <= date <= DATA_HORIZON_END):
        return {"flight_no": flight_no, "date": date, "status": "NOT_IN_HORIZON",
                "note": f"No OpsFeed record outside {DATA_HORIZON_START} to {DATA_HORIZON_END}."}
    for row in load_flights():
        if row["flight_no"] == flight_no and row["date"] == date:
            as_of = (FIXTURE_CLOCK - timedelta(seconds=20)).isoformat()
            enriched = dict(row)
            enriched["as_of"] = as_of
            enriched["source"] = "opsfeed-event"
            enriched["est_dep_local"] = _add_minutes(row["sched_dep_local"], row["delay_min"])
            enriched["est_arr_local"] = _add_minutes(row["sched_arr_local"], row["delay_min"])
            return enriched
    return {"flight_no": flight_no, "date": date, "status": "NOT_IN_HORIZON",
            "note": "No OpsFeed record for that flight number and date."}


def _add_minutes(hhmm, minutes):
    base = datetime.strptime(hhmm, "%H:%M")
    return (base + timedelta(minutes=minutes)).strftime("%H:%M")


# ---------------------------------------------------------------------------
# lookup_booking backend: Altura PNR retrieve
# ---------------------------------------------------------------------------
def get_booking_raw(pnr):
    bookings = load_bookings()["bookings"]
    if pnr not in bookings:
        raise NotFound(f"No PNR {pnr} in Altura.")
    return bookings[pnr]


def get_disrupted_segment(booking):
    """The segment to act on: not necessarily segment 1, and for a connection
    at risk, not necessarily the segment that's actually late."""
    actionable = {"cancelled_by_airline", "diverted", "missed_connection"}
    for seg in booking["segments"]:
        if seg.get("status") in actionable:
            return seg

    # Connection at risk (T9WN4C's shape): the inbound leg is delayed, but the
    # leg that needs an alternative is the downstream one it feeds into. A
    # search on the inbound's own origin/dest just returns "you, but later."
    # The cache is keyed on whichever leg the case authors built out, so trust
    # that: if the downstream segment's O-D is a cached search key and the
    # inbound is running behind, act on the downstream segment.
    if booking.get("connection") and len(booking["segments"]) >= 2:
        inbound, downstream = booking["segments"][0], booking["segments"][1]
        inbound_live = get_flight_status_raw(inbound["flight_no"], inbound["date"])
        cache_key = f"{downstream['origin']}-{downstream['dest']}|{downstream['date']}|{downstream['cabin']}"
        if inbound_live.get("status") not in ("ON_TIME", "NOT_IN_HORIZON") and cache_key in load_alternatives()["entries"]:
            return downstream

    # Nothing pre-flagged in the booking. Check whether OpsFeed shows the
    # segment running, delayed, cancelled or diverted right now.
    for seg in booking["segments"]:
        live = get_flight_status_raw(seg["flight_no"], seg["date"])
        if live.get("status") not in ("ON_TIME", "NOT_IN_HORIZON"):
            return seg
    return booking["segments"][0]


# ---------------------------------------------------------------------------
# search_alternatives backend: Altura availability search
# ---------------------------------------------------------------------------
def search_alternatives_raw(origin, dest, date, cabin, pax_count=1, exclude_flight_no=None):
    cache = load_alternatives()
    key = f"{origin}-{dest}|{date}|{cabin}"
    entries = cache["entries"]
    if key in entries:
        entry = entries[key]
        kept, excluded = [], list(entry.get("excluded", []))
        for opt in entry["options"]:
            if opt["seats_available"] < pax_count:
                excluded.append({"flight_no": "/".join(opt["flights"]), "date": opt["date"],
                                  "reason": "insufficient seats"})
            else:
                kept.append(opt)
        return {"options": kept[:7], "excluded": excluded,
                "current_booking_reference": entry.get("current_booking_reference"),
                "other_cabin_options": entry.get("other_cabin_options", [])}
    # Fallback: synthesize from flights.csv per lookup_rules #3 (nonstops only,
    # never the current booking, never a flight that's already departed).
    fixture_date, fixture_time = FIXTURE_CLOCK.strftime("%Y-%m-%d"), FIXTURE_CLOCK.strftime("%H:%M")
    options = []
    for row in load_flights():
        if row["origin"] != origin or row["dest"] != dest:
            continue
        if row["status"] in ("CANCELLED", "DIVERTED"):
            continue
        if exclude_flight_no and row["flight_no"] == exclude_flight_no and row["date"] == date:
            continue
        if row["date"] < fixture_date:
            continue
        if row["date"] == fixture_date and _add_minutes(row["sched_dep_local"], row["delay_min"]) < fixture_time:
            continue
        if row["seats_left_Y" if cabin == "Y" else "seats_left_J"] < pax_count:
            continue
        options.append({
            "option_id": f"OPT-{row['flight_no']}-{row['date'][5:7]}{row['date'][8:10]}-{cabin}",
            "flights": [row["flight_no"]], "date": row["date"],
            "dep_local": _add_minutes(row["sched_dep_local"], row["delay_min"]),
            "arr_local": _add_minutes(row["sched_arr_local"], row["delay_min"]),
            "stops": 0, "cabin": cabin,
            "seats_available": row["seats_left_Y" if cabin == "Y" else "seats_left_J"],
            "operated_by": "Larkspur Airlines" if not row["flight_no"].startswith("LK34") else "Larkspur Link",
        })
    return {"options": options[:7], "excluded": [], "current_booking_reference": None,
            "other_cabin_options": []}


def earliest_alternative_date(origin, dest, date, cabin, pax_count=1):
    result = search_alternatives_raw(origin, dest, date, cabin, pax_count)
    if not result["options"]:
        return None
    return result["options"][0]["date"]


# ---------------------------------------------------------------------------
# check_policy backend: the entitlements engine, resolution_order verbatim
# ---------------------------------------------------------------------------
def resolve_policy(cause_code, delay_minutes, status, fare_family, loyalty_tier,
                    overnight, wait_minutes_for_alternative=None, escalation_context=None):
    """Implements disruption_policy.json's resolution_order, steps 1-10."""
    policy = load_policy()
    escalation_context = escalation_context or {}

    # Step 1: cause_class
    cause_class = policy["cause_classes"][cause_code]

    # Step 2: band
    if status == "CANCELLED":
        band = "CXL"
    elif status == "DIVERTED":
        band = "DIV"
    else:
        band = None
        for b in policy["delay_bands"]:
            if b["band"] in ("CXL", "DIV"):
                continue
            lo, hi = b["min_minutes"], b["max_minutes"]
            if lo <= delay_minutes and (hi is None or delay_minutes <= hi):
                band = b["band"]
                break
        if band is None:
            raise ValueError(f"delay_minutes {delay_minutes} matches no band")

    # Step 3: select + copy the row
    row = next((r for r in policy["rows"]
                if r["cause_class"] == cause_class and r["band"] == band), None)
    if row is None:
        raise NotFound(f"No policy row for {cause_class}/{band}, a real gap, like the "
                        f"pre-14.3 DIVERTED fall-through this pack's changelog tells you about.")
    row = deepcopy(row)
    policy_row_id = row["policy_row_id"]

    # Step 4: resolve conditionals (meal / hotel / ground)
    meal = row["care"]["meal_usd"]
    if isinstance(meal, dict):
        threshold = meal["if_wait_minutes_for_alternative_gte"]
        if wait_minutes_for_alternative is None:
            meal_out = {"conditional": True, "threshold_minutes": threshold, "amount_usd": meal["amount"]}
        elif wait_minutes_for_alternative >= threshold:
            meal_out = {"amount_usd": meal["amount"]}
        else:
            meal_out = {"amount_usd": meal.get("else", 0)}
    else:
        meal_out = {"amount_usd": meal}

    hotel = dict(row["care"]["hotel"])
    if hotel.get("eligible") == "if_overnight":
        hotel_out = {"eligible": bool(overnight)}
        if overnight:
            hotel_out.update({"nightly_cap_usd": hotel["nightly_cap_usd"], "nights": hotel["nights"],
                               "approval": hotel["approval"]})
    else:
        hotel_out = {"eligible": False}
        if "instead" in hotel:
            hotel_out["instead"] = hotel["instead"]

    ground = row["care"]["ground_usd"]
    if isinstance(ground, dict) and "if_overnight" in ground:
        ground_out = {"amount_usd": ground["if_overnight"] if overnight else ground["else"]}
    elif isinstance(ground, dict) and "rule" in ground:
        # Diversion ground-transport rule: airline coach covers it; only a
        # gap in airline-arranged transport within 180 min opens a credit.
        ground_out = {"amount_usd": ground["amount"], "rule": ground["rule"], "conditional": True}
    else:
        ground_out = {"amount_usd": ground}

    # Step 5: fare_family_rules
    ffr = policy["fare_family_rules"][fare_family]
    rebooking_out = dict(row["rebooking"])
    if rebooking_out.get("waiver"):
        common = policy["rebooking_waiver_common"]
        window_key = rebooking_out.get("window", cause_class)
        rebooking_out["window_days_after_original"] = common["window_days_after_original"][window_key]
        rebooking_out["window_days_before_original"] = common["window_days_before_original"]
        rebooking_out["executes_via"] = common["executes_via"]
    if band == "B0" and ffr.get("same_day_confirmed_change"):
        rebooking_out["same_day_confirmed_change"] = ffr["same_day_confirmed_change"]
    refund_out = dict(row["refund"])
    refund_out["fare_refundable"] = ffr["refundable"]
    if not ffr["refundable"] and "non_refundable_gets" not in refund_out and refund_out.get("eligible_on_request"):
        pass  # rows that grant refund already state the non_refundable_gets fallback where relevant

    # Step 6: loyalty_rules (meal bonus, upgrade flag, Summit courtesy overrides)
    lr = policy["loyalty_rules"][loyalty_tier]
    goodwill_out = dict(row["goodwill"])
    if meal_out.get("amount_usd", 0) and lr.get("meal_bonus_usd"):
        bonus_cap = lr.get("meal_bonus_cap_usd", meal_out["amount_usd"] + lr["meal_bonus_usd"])
        meal_out["amount_usd"] = min(meal_out["amount_usd"] + lr["meal_bonus_usd"], bonus_cap)
    rebooking_out["upgrade_if_y_unavailable"] = lr.get("upgrade_if_y_unavailable", False)
    if loyalty_tier == "Summit" and "courtesy_overrides" in lr:
        for override_name, override in lr["courtesy_overrides"].items():
            if policy_row_id in override.get("applies_to_rows", []):
                if override_name == "uncontrollable_goodwill":
                    goodwill_out = {"eligible": True, "cap_usd": override["cap_usd"],
                                     "approval": override["approval"], "source": "Summit courtesy override"}
                elif override_name == "uncontrollable_overnight_hotel" and overnight:
                    hotel_out = {"eligible": True, "nightly_cap_usd": override["nightly_cap_usd"],
                                 "nights": override["nights"], "approval": override["approval"],
                                 "label": override["label"]}

    # Step 7: goodwill cap
    if goodwill_out.get("eligible") and "source" not in goodwill_out:
        if fare_family == "Basic" and TIER_RANK[loyalty_tier] < TIER_RANK[policy["goodwill_rules"]["basic_fare_minimum_tier"]]:
            goodwill_out = {"eligible": False, "cap_usd": 0}
        else:
            cap = (policy["goodwill_base_by_tier_usd"][loyalty_tier]
                   + policy["goodwill_rules"]["fare_family_adjustment_usd"][fare_family])
            if band == "B4":
                cap += policy["goodwill_rules"]["b4_extra_usd"]
            goodwill_out["cap_usd"] = cap

    # Step 8: approval tiers
    auto = policy["auto_approval"]
    if meal_out.get("amount_usd", 0) is not None and not meal_out.get("conditional"):
        meal_out["approval"] = "auto" if meal_out["amount_usd"] <= auto["meal"]["auto_max_usd"] else "human_approve"
    if isinstance(ground_out.get("amount_usd"), (int, float)):
        ground_out["approval"] = "auto" if ground_out["amount_usd"] <= auto["ground"]["auto_max_usd"] else "human_approve"
    if hotel_out.get("eligible"):
        hotel_out.setdefault("approval", auto["hotel"]["approval"])
    if goodwill_out.get("eligible"):
        cap = goodwill_out["cap_usd"]
        if goodwill_out.get("approval") != "human_approve":  # Summit override already pinned its own approval
            if cap <= auto["goodwill"]["auto_max_usd"]:
                goodwill_out["approval"] = "auto"
            elif cap <= auto["goodwill"]["human_approve_range_usd"][1]:
                goodwill_out["approval"] = "human_approve"
            else:
                goodwill_out["approval"] = "supervisor"
    else:
        goodwill_out.setdefault("approval", None)
    refund_out["executes"] = "human"  # policy["auto_approval"]["refund"]["agent_executes"] is always false

    # Step 9: escalation triggers (data-derivable subset, the rest are
    # conversational and owned by the system prompt, per the policy file's
    # own escalation_triggers.conversation_note).
    escalate = []
    for trig in policy["escalation_triggers"]["policy"]:
        tid = trig["id"]
        if tid == "ESC-04" and escalation_context.get("has_partner_segment"):
            escalate.append(tid)
        elif tid == "ESC-05" and escalation_context.get("is_group_booking"):
            escalate.append(tid)
        elif tid == "ESC-06" and escalation_context.get("unaccompanied_minor"):
            escalate.append(tid)
        elif tid == "ESC-08" and delay_minutes and delay_minutes >= 720:
            escalate.append(tid)
        elif tid == "ESC-09" and escalation_context.get("no_alternative_in_window"):
            escalate.append(tid)
        elif tid == "ESC-10" and escalation_context.get("ssr_inventory_required"):
            escalate.append(tid)

    # Step 10: assemble
    return {
        "policy_row_id": policy_row_id,
        "policy_version": policy["version"],
        "cause_class": cause_class,
        "band": band,
        "rebooking": rebooking_out,
        "refund": refund_out,
        "care": {"meal": meal_out, "hotel": hotel_out, "ground": ground_out},
        "goodwill": goodwill_out,
        "escalate": escalate,
        "explainer": row["explainer_en"],
        "wording": policy["customer_wording"]["never_say"],
    }


# ---------------------------------------------------------------------------
# Write tools: hold / confirm / voucher / escalate / send_confirmation.
# All state is in-memory only, per data/americas/README.md: they validate
# against the data files (option ids, voucher tiers, queues) but never write
# to them.
# ---------------------------------------------------------------------------
_holds = {}            # hold_id -> {option_id, pnr, expires_at}
_confirmation_tokens = {}   # hold_id -> token, minted only by the "customer click"
_confirmed = {}         # hold_id -> confirmation record
_vouchers = []
_escalations = []
_confirmations_sent = []

HOLD_TTL_MINUTES = 15


def hold_seat(option_id, pnr):
    hold_id = f"HOLD-{secrets.token_hex(3).upper()}"
    _holds[hold_id] = {"option_id": option_id, "pnr": pnr,
                        "expires_at": FIXTURE_CLOCK + timedelta(minutes=HOLD_TTL_MINUTES)}
    return {"hold_id": hold_id, "option_id": option_id, "expires_in_minutes": HOLD_TTL_MINUTES}


def simulate_customer_confirm_click(hold_id):
    """Not a model tool. Stands in for the UI button. The only place a
    confirmation_token is ever minted. A model that types 'yes, confirmed'
    in a message never reaches this function."""
    if hold_id not in _holds:
        raise NotFound(f"No hold {hold_id} to confirm.")
    token = secrets.token_urlsafe(8)
    _confirmation_tokens[hold_id] = token
    return token


def confirm_rebooking(hold_id, confirmation_token):
    if hold_id not in _holds:
        return {"status": "error", "reason": "unknown_hold"}
    if FIXTURE_CLOCK > _holds[hold_id]["expires_at"]:
        return {"status": "error", "reason": "hold_expired"}
    if _confirmation_tokens.get(hold_id) != confirmation_token:
        return {"status": "error", "reason": "invalid_or_missing_token",
                "note": "confirmation_token must come from the customer's Confirm click, never from chat text"}
    record = {"hold_id": hold_id, **_holds.pop(hold_id)}
    del _confirmation_tokens[hold_id]
    _confirmed[hold_id] = record
    return {"status": "confirmed", "hold_id": hold_id, "option_id": record["option_id"]}


def issue_voucher(voucher_type, amount_usd, pnr, policy_row_id):
    policy = load_policy()["auto_approval"]
    tier = policy.get(voucher_type)
    if tier is None:
        return {"status": "error", "reason": f"unknown voucher type {voucher_type}"}
    if voucher_type == "hotel":
        outcome = "pending_human_approval"
    elif voucher_type == "goodwill":
        if amount_usd <= tier["auto_max_usd"]:
            outcome = "issued"
        elif amount_usd <= tier["human_approve_range_usd"][1]:
            outcome = "pending_human_approval"
        else:
            outcome = "pending_supervisor_approval"
    else:  # meal, ground
        outcome = "issued" if amount_usd <= tier["auto_max_usd"] else "pending_human_approval"
    voucher_id = f"VCH-{voucher_type[:3].upper()}-{secrets.token_hex(3).upper()}"
    record = {"voucher_id": voucher_id, "type": voucher_type, "amount_usd": amount_usd,
              "pnr": pnr, "policy_row_id": policy_row_id, "status": outcome}
    _vouchers.append(record)
    return record


def escalate_to_human(pnr, reason, summary_for_human, queue=None):
    escalation_id = f"ESC-{secrets.token_hex(3).upper()}"
    record = {"escalation_id": escalation_id, "pnr": pnr, "reason": reason,
              "queue": queue, "summary_for_human": summary_for_human}
    _escalations.append(record)
    return record


def send_confirmation(pnr, message):
    record = {"pnr": pnr, "message": message, "sent_at": FIXTURE_CLOCK.isoformat()}
    _confirmations_sent.append(record)
    return {"status": "sent"}


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
def _selftest():
    policy = load_policy()
    print(f"Loaded policy v{policy['version']}, {len(policy['rows'])} rows, "
          f"{len(load_bookings()['bookings'])} bookings, {len(load_flights())} flight rows.\n")

    print("== Replaying disruption_policy.json examples[] ==")
    failures = 0
    for ex in policy["examples"]:
        inp = ex["input"]
        result = resolve_policy(
            cause_code=inp["cause_code"], delay_minutes=inp["delay_minutes"], status=inp["status"],
            fare_family=inp["fare_family"], loyalty_tier=inp["loyalty_tier"], overnight=inp["overnight"],
            wait_minutes_for_alternative=inp.get("wait_minutes_for_alternative"),
        )
        expected = ex["output"]
        ok = result["policy_row_id"] == expected["policy_row_id"]
        goodwill_ok = result["goodwill"]["eligible"] == expected["goodwill"]["eligible"] and (
            not expected["goodwill"]["eligible"] or result["goodwill"]["cap_usd"] == expected["goodwill"]["cap_usd"]
        )
        meal_ok = result["care"]["meal"]["amount_usd"] == expected["care"]["meal"].get("amount_usd", 0)
        status = "PASS" if (ok and goodwill_ok and meal_ok) else "FAIL"
        if status == "FAIL":
            failures += 1
        print(f"  [{status}] {ex['name']}")
        print(f"         got row={result['policy_row_id']}  goodwill={result['goodwill']}  meal={result['care']['meal']}")
        if status == "FAIL":
            print(f"         want row={expected['policy_row_id']}  goodwill={expected['goodwill']}  meal={expected['care']['meal']}")

    print(f"\n{len(policy['examples']) - failures}/{len(policy['examples'])} policy examples matched.\n")

    print("== Stage 1 PNRs through the read path ==")
    stage1 = ["K7PQ2M", "M3XR8T", "T9WN4C", "G2HL9V", "R8KD3F"]
    for pnr in stage1:
        booking = get_booking_raw(pnr)
        seg = get_disrupted_segment(booking)
        flight = get_flight_status_raw(seg["flight_no"], seg["date"])
        print(f"  {pnr}: {booking['passengers'][0]['last_name']}, {booking['fare_family']}/"
              f"{booking['loyalty']['tier']}, seg {seg['flight_no']} {seg['origin']}-{seg['dest']} "
              f"-> flight status {flight['status']} (delay {flight.get('delay_min', '?')}m, cause {flight.get('cause_code') or '-'})")
        alt = search_alternatives_raw(seg["origin"], seg["dest"], seg["date"], seg["cabin"],
                                        len(booking["passengers"]), exclude_flight_no=seg["flight_no"])
        print(f"         {len(alt['options'])} alternative(s) found, "
              f"top: {alt['options'][0]['option_id'] if alt['options'] else 'none'}")

    print("\n== Guardrail smoke test: confirm without a customer click ==")
    hold = hold_seat("OPT-LK417-0508-Y", "K7PQ2M")
    blocked = confirm_rebooking(hold["hold_id"], "made-up-token-from-chat-text")
    print(f"  hold {hold['hold_id']} + fabricated token -> {blocked}")
    token = simulate_customer_confirm_click(hold["hold_id"])
    real = confirm_rebooking(hold["hold_id"], token)
    print(f"  hold {hold['hold_id']} + real click token -> {real}")

    if failures:
        raise SystemExit(f"\n{failures} policy example(s) did not match. Fix resolve_policy before teaching from this.")
    print("\nAll self-tests passed.")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(__doc__)
