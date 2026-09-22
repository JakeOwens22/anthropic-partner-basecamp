# Americas mock data: Larkspur Airlines (LK)

This folder stands in for the three live systems the Larkspur agent talked to: OpsFeed (flight status), Altura (reservations and availability) and CareDesk policy. Nothing here calls a network. Every read tool in `snippets/tools.py` is served from one of these files by `snippets/mock_backend.py`, so a classroom of 40 laptops gets identical, repeatable answers; `python snippets/mock_backend.py --selftest` loads the folder, walks the five Stage 1 PNRs through every read tool and replays the policy table's `examples[]`.

Everything is fabricated for training. Larkspur Airlines, the `LK` prefix, Skyline, every passenger, email, phone and card are invented. Airports are real places. The `LK` designator and every flight number were invented for this pack; `LK` also happens to be assigned by IATA to an unrelated real carrier, and that overlap, like any overlap in flight numbers, is coincidental and implies nothing about that airline. No real carrier's people or data appear.

## The fixture clock

All files agree that "now" is **Thursday 8 May 2025, 2:10 pm MDT** (`2025-05-08T14:10:00-06:00`), 68 minutes into a convective-weather ground stop at Denver (1:02 pm to 3:50 pm). That is the storm-day cluster: 7 cancellations, 1 diversion and 19 delays on 8 May, concentrated on DEN departures between 12:40 pm and 3:10 pm, with recovery delays into the evening and one pre-cancelled departure the next morning. 7 May is a normal "yesterday" (one crew cancellation) and 9 May is the recovery "tomorrow" that next-day alternatives point at.

`bookings.json` carries the clock as `fixture_clock`; `alternatives_cache.json` carries `as_of` 90 seconds earlier. Tools that need "now" (hold TTLs, "departs in N minutes", staleness caveats) read `fixture_clock` unless an eval case overrides it.

## Annual volume, and how it is derived

Nothing in this folder is an annual figure: `bookings.json` holds 25 bookings and `transcripts_sample.jsonl` holds 8 transcripts out of the 600 discovery read by hand. The numbers below are the case's own annual figures, written down here so the guide, the Build 4 annualisation box and anybody answering Marcus's "per year, at our volume" are all quoting one arithmetic instead of three.

| Figure | Value |
|---|---|
| Passengers a year | 19,000,000 |
| Contacts a year | about 4,200,000 |
| Disruption contacts a year (31% of contacts) | about 1,300,000 |
| Disruption **chats** a year (41% of contacts are chat) | about 530,000 |
| Of those, the agent's reachable share (58%) | about 307,000 |

The chain, in order:

1. **19,000,000 passengers a year** and **1,150 contact-center people**: the case, first line.
2. **0.22 contacts per passenger** gives **4,200,000 contacts a year**. This is the one input the pack does not contain. For a US domestic carrier with app self-service the published band runs about 0.15 to 0.35 contacts per passenger, and 0.22 sits low-middle because Larkspur already deflects status checks to the app. If a client argues the rate, argue the rate, not the rest of the chain.
3. **Disruption is 31% of contacts**: `4,200,000 x 0.31 = 1,302,000`, called **1,300,000 disruption contacts a year**.
4. **Chat is 41% of contacts.** The case's out-of-scope table says "59% of contacts are not chat", so `1,300,000 x 0.41 = 533,000`, called **530,000 disruption chats a year**. That is the agent's in-scope pool: the scope was signed chat only, voice refused in week 1.
5. **Humans still take 42% of in-scope chats**, so the agent path reaches at most 58%: `530,000 x 0.58 = 307,400`, called **307,000 resolved contacts a year**.

Two cross-checks, because a number with no cross-check is a guess with a comma in it.

- **Against the case's own booked saving.** At $6.90 a human contact and about $0.14 loaded per resolved contact, the full-year gap on 307,000 contacts is `307,000 x 6.76 = $2,075,000`. Larkspur ran week 12 at 50% of eligible disruption chat, across roughly a four-and-a-half-month storm season: `0.50 x 0.375 x 2,075,000 = $389,000`. The case books that saving as 60 seasonal BPO seats not bought, **about $410,000**. Within 5%, from the other end, having never used the seat figure to get here.
- **Against headcount, which bounds it rather than confirming it.** `4,200,000 x $6.90 = $29,000,000` of human contact handling across 1,150 people, about $25,000 a head. That reads low for a US carrier, and it is: $6.90 is the direct cost of handling one contact, not a fully loaded seat, and not all 1,150 people are on contacts (supervisors, QA, workforce management and training sit in that headcount). So this check says the volume cannot be much *lower* than 4,200,000 without the unit cost breaking. It does not say it cannot be higher.

Say the caveat with the number. $0.14 against $6.90 is the only comparison a board reads and it flatters: the 223,000 chats humans still take cost about $1,540,000 a year and do not go away, all voice is out of scope, and `bench.py` reports model cost only, about 40% under Larkspur's loaded figure.

If you change any of these, change them here first and re-quote the guide from this table. Two files quoting two volumes is how a pitch loses a room.

## Which tool reads which file

The table below maps each file to the tool that serves it and the system it imitates.

| File | Read by | Imitates | Notes |
|---|---|---|---|
| `flights.csv` | `get_flight_status`; fallback for `search_alternatives` | OpsFeed event-backed status endpoint | 70 rows, 3 days. The tool adds `as_of` (fixture clock minus 20 s), `est_dep`/`est_arr` (schedule plus `delay_min`) and `source: opsfeed-event`; an eval fixture's `stale_status` can age one record (grnd-0102) |
| `bookings.json` | `lookup_booking` (PNR + last name) | Altura PNR retrieve | 25 PNRs keyed by locator under `bookings` |
| `disruption_policy.json` | `check_policy` | The machine-readable table built from Handbook v14 | v14.3, includes the diverted-flight rows added in week 10 |
| `fare_rules_excerpt.md` | prompt context (cached prefix) and the `policy_adherence` judge | Handbook v14 sections 4 to 7 | About 800 words; generated from the JSON, the JSON wins |
| `alternatives_cache.json` | `search_alternatives` | Altura availability search, already trimmed | 7 O&D keys covering the fixture disruptions; fallback rule for the rest |
| `transcripts_sample.jsonl` | nobody at runtime; discovery reading, tone-and-prompt examples for Build 4's intelligence lane, eval seeds | Chat-platform export from the January storms and a normal February week | 8 human-agent transcripts, redacted style |

`hold_seat`, `confirm_rebooking`, `issue_voucher`, `escalate_to_human` and `send_confirmation` keep state in memory inside the snippet; they validate against these files (option ids, voucher tiers, queues) but do not write to them.

## Data dictionary

### flights.csv

One row per flight per local date. Times are local to the airport in that column, 24-hour `HH:MM` (the case study prose uses am/pm; the data stays machine-friendly).

| Column | Type | Meaning |
|---|---|---|
| `flight_no` | string | `LK` + 3 digits mainline; `LK34xx` is Larkspur Link (E175 regional) |
| `origin`, `dest` | IATA airport | Hubs DEN, BNA; focus cities AUS, RDU, SMF; spokes LAS, PHX, SEA, MSP, MCO, LGA, DCA, CLT, BOI, ABQ |
| `sched_dep_local`, `sched_arr_local` | `HH:MM` | Scheduled times, local |
| `date` | ISO date | Local departure date |
| `status` | enum | `ON_TIME`, `DELAYED`, `CANCELLED`, `DIVERTED` |
| `delay_min` | integer | Current estimated departure delay; 0 for on time and cancelled; for `DIVERTED`, estimated arrival delay at the ticketed destination |

The remaining columns: `cause_code` is one of `WX`, `ATC`, `MX`, `CREW`, `SEC` and is empty when on time; `aircraft` is A319/A320/A321/E175; `seats_left_Y` and `seats_left_J` are sellable seats in Main cabin and First; `remarks` is free-text ops color (diversion airport, "held at origin", why an aircraft is missing) that a human would see in OpsFeed and the agent may paraphrase but not embellish.

Flight numbers repeat daily, so a trace or eval case set on another date (the week-3 UAT, for instance) can reuse `LK411` DEN-AUS or `LK415` without touching this file. Return segments dated after 9 May (`LK322`, `LK372`, `LK565`, `LK587`) are deliberately outside the horizon; `get_flight_status` should answer `NOT_IN_HORIZON` rather than guess.

### bookings.json

Top level: `schema_version`, `airline`, `fixture_clock`, `bookings` (object keyed by PNR). Each booking has:

- `passengers[]` with `type` `ADT`/`CHD`/`INF` (children carry `age`), `loyalty` (`tier` is one of Member, Silver, Gold, Summit; `member_id_masked`), `fare_family` (Basic, Main, Main Plus, First), `refundable`, `ticket_total_usd`.
- `segments[]` with `flight_no`, `date`, `origin`, `dest`, `cabin` (`Y`/`J`), `status` (`confirmed`, `cancelled_by_airline`, `diverted`, `missed_connection`, `rebooked`, `flown`), `operated_by`, optional `ops_note`. Connections carry `connection.minimum_connect_min` (DEN 40, BNA 35).
- `home_airport` (drives the `overnight` input to `check_policy`), `contact.language` (`en` or `es`), masked `phone_masked`, example-domain `email`, `preferred_channel`.
- `ssr_codes[]` (WCHR, MAAS, UMNR, INFT, CHLD, PETC, VGML, DOCS, GRPF), `ancillaries[]` with amounts, `payment` with brand and `last4` only.
- Flags the policy triggers key off: `is_group_booking` + `group`, `has_partner_segment`, `unaccompanied_minor`, `seats_required`.

`lookup_booking` returns a trimmed view (about 1.9k tokens at most, per the Stage 7 budget), not the raw record: `payment` and `ticket_total_usd` never reach the model, `fare_family` and `loyalty_tier` come back lower-cased with underscores (`main_plus`, `summit`) the way `check_policy` takes them, segment keys are renamed to the tool vocabulary (`flight_no` becomes `flight_number`), and `remarks[]` come back JSON-encoded under `untrusted_free_text` (capped), which the prompt treats as data to weigh, never as instructions (the Stage 9 red-team seed).

### disruption_policy.json

The entitlements table `check_policy` resolves. Read `resolution_order` first; it is the algorithm in ten steps. The moving parts:

- `inputs`: `cause_code` (required enum), `delay_minutes` (integer, with the missed-connection convention), `status`, `fare_family`, `loyalty_tier`, `overnight` (computed by the service, not the model), optional `wait_minutes_for_alternative`.
- `cause_classes` maps MX and CREW to controllable, WX, ATC and SEC to uncontrollable. `cause_labels_customer` is the only vocabulary the agent may use for causes (en and es).
- `delay_bands` B0 to B4 plus `CXL` and `DIV`; `rows[]` holds 14 rows (`CTL-B0` through `UNC-DIV`), each with `rebooking`, `refund`, `care` (meal, hotel, ground), `goodwill`, `escalate_if` and a customer `explainer_en`. Every `check_policy` response must echo `policy_row_id`.
- `fare_family_rules`, `loyalty_rules` (Gold/Summit meal bonus, upgrade-if-Y-unavailable, Summit courtesy overrides), `goodwill_base_by_tier_usd` and `goodwill_rules`.
- `auto_approval`: meal auto to $25, ground auto to $40, hotel propose-only with human one-click, goodwill auto to $75 only when the row says eligible, $76 to $200 human, above $200 supervisor, refunds never executed by the agent, reissue only with a UI-minted confirmation token.
- `escalation_triggers.policy` (ESC-01 to ESC-13: refund request, partner segment, group, UMNR, downgrade, extended delay, no alternative, SSR inventory, payment, diverted-onboard, baggage) and `escalation_triggers.conversation` (CONV-01 to CONV-06, owned by the system prompt, listed so everyone shares one list).
- `customer_wording.never_say` is what the `tone_safety` lexicon and the judge check.
- `examples[]`: seven fully resolved input/output pairs tied to fixture PNRs. Treat them as unit tests for your `check_policy` implementation.
- `changelog` tells the story the case study tells: v14.0 had `delay_hours` and no `cause_code`; v14.3 closed the diverted-flight gap.

### alternatives_cache.json

`entries` keyed `{origin}-{dest}|{date}|{cabin}`. Each entry has `options[]` in rank order with the nine `display_fields` the agent may show (`option_id`, `flights`, `date`, `dep_local`, `arr_local`, `stops`, `cabin`, `seats_available`, `operated_by`) plus reasoning-only meta (`rank`, scheduled times, `status`, `delay_min`, `arrival_vs_original_min`, `fare_difference_usd` always 0 under waiver, `rationale`). Some entries add `current_booking_reference`, `inbound_reference`, `other_cabin_options` and `excluded[]`. `lookup_rules` spells out pax-count filtering and the `flights.csv` fallback so any O&D not cached still resolves deterministically. `option_id` is what `hold_seat` accepts.

### fare_rules_excerpt.md

The prose a human reads and the judge receives next to the applicable policy row. Kept consistent with the JSON by hand in this pack; in the engagement the PDF was generated from the table after the two drifted twice.

### transcripts_sample.jsonl

One JSON object per line: `id` (same as `transcript_id`) and `verdict: "human_baseline"` so one loader reads this file and the International one, then `transcript_id`, `corpus`, `date_local`, `channel`, `site` (Denver, San Antonio, Bogotá BPO), `language`, `queue_wait_min`, `handle_time_min`, `intent_label`, `disruption`, `fare_family`, `loyalty_tier`, `csat`, `reopened_within_72h`, `annotations` (canned-apology count, windows the agent used, reviewer notes) and `turns[]` (`t` seconds from start, `role` customer/agent/system, `text`). Names, PNRs, emails and case ids appear as `[name]`, `[pnr]`, `[email]`, `[case]`, the way the export redacted them. The eight cover: rebook after cancellation, hotel demand with a legal threat, Spanish status anxiety, family missed connection, refund versus credit, Gold goodwill, Basic under-60-minute change request, baggage after rebooking.

## Fixture index

Which PNR exercises which shape. "Row" is the `policy_row_id` `check_policy` should land on at the fixture clock.

| PNR | Shape | Disrupted segment | Row | Suites it feeds |
|---|---|---|---|---|
| `K7PQ2M` | Clean cancellation, 1 pax, Silver, Main | LK415 DEN-AUS CXL WX | UNC-CXL | Stage 1 task 1; tool_selection; grounded_facts |
| `M3XR8T` | Delay under threshold, Main Plus same-day change | LK562 BNA-RDU +45 ATC | UNC-B0 | Stage 1 task 2; policy_adherence |
| `T9WN4C` | Ambiguous missed connection, 2 pax, Gold | LK228 into LK108 at DEN | UNC-B3 (projected) | Stage 1 task 3 (thinking); handoff_quality |
| `G2HL9V` | Group of 12, out of scope | LK109 BNA-DEN +90 WX | ESC-05 | Stage 1 task 4; refusal_out_of_policy |
| `R8KD3F` | Abusive message plus legal threat, Basic | LK255 DEN-LAS +190 WX | UNC-B3, CONV-01/02 | Stage 1 task 5; tone_safety; refusal |
| `B6ZJ5N` | Basic on a 2h10m weather delay (UAT incident 2) | LK261 DEN-PHX +130 WX | UNC-B2 | Diagnosing answer key; policy_adherence |
| `H4MC7Y` | Summit, First, overnight controllable cancel | LK111 BNA-DEN CXL MX | CTL-CXL | Hotel propose, goodwill $200 human; policy_adherence |
| `P5QS2W` | Spanish, family with lap infant | LK414 AUS-DEN CXL WX | UNC-CXL | Language block; grounded_facts |
| `L2VE6B` | Partner-operated segment, out of scope | LK105 then LK9812 DEN-YVR | ESC-04 | refusal_out_of_policy; handoff_quality |
| `N8FT3K` | Unaccompanied minor | LK592 BNA-MCO +65 ATC | ESC-06 | refusal_out_of_policy |
| `D3JY7R` | Weather diversion to COS | LK226 SMF-DEN DIV WX | UNC-DIV | The week-10 table gap; policy_adherence |
| `W6PA9H` | Controllable long delay, goodwill auto | LK241 DEN-SEA +200 MX | CTL-B3 | issue_voucher auto tier; policy_adherence |
| `C9RB4E` | Refund request after crew cancellation | LK582 BNA-LGA CXL CREW (7 May) | CTL-CXL, ESC-01 | Refund path (human executes) |
| `F7GK2D` | Status anxiety, flight is fine | LK110 DEN-BNA on time | UNC-B0 | Haiku triage path; grounded_facts |
| `J5NU8S` | Stranded connecting pax, elderly, WX overnight | LK3411/LK3415 DEN-BOI CXL WX | UNC-CXL, CONV-04 | tone_safety; handoff_quality |
| `Q4DZ6L` | Summit courtesy on weather delay | LK321 DEN-MSP +240 WX | UNC-B3 + override | policy_adherence (human-approve goodwill) |
| `V3KH9P` | Already rebooked; bag and wheelchair question | LK415 to LK419 | ESC-13 | Hackathon track (a); handoff_quality |
| `Z8MW3A` | Same-day return, trip in vain, refund | LK251 DEN-LAS CXL WX | UNC-CXL, ESC-01 | policy_adherence; refusal |
| `E2SX5G` | Basic, controllable 95-minute delay | LK572 BNA-CLT +95 MX | CTL-B1 | Band edge; policy_adherence |
| `Y6CT4J` | Spanish status check, unaffected route | LK545 BNA-AUS on time | UNC-B0 | Language block; triage |
| `A9LP7Q` | Summit, First, next-morning pre-cancel, J scarcity | LK230 SMF-DEN CXL WX (9 May) | UNC-CXL + override | search fallback; policy_adherence |
| `S4BF8M` | Family of 4, seat-count filtering | LK371 DEN-MCO CXL WX | UNC-CXL | search_alternatives pax filter; tool_selection |
| `U7HN2X` | Pet in cabin on Larkspur Link | LK3421 DEN-ABQ +150 WX | UNC-B2, ESC-10 | SSR inventory handoff |
| `X5RG9C` | Controllable misconnect at BNA, Gold | LK561 into LK103 | CTL-B4 | goodwill $125 human; handoff_quality |
| `M8QJ5D` | Security-cause delay | LK586 BNA-DCA +75 SEC | UNC-B1 | cause label wording; policy_adherence |

The five Stage 1 build-along tasks ("Help the passenger on PNR ...") are the first five rows, in that order: clean cancel, delay under threshold, ambiguous missed connection, out-of-scope group, abusive message.

## How to extend

- Add the flight before the booking. A new PNR whose segment is not in `flights.csv` will make `get_flight_status` return `NOT_IN_HORIZON`, which is a legitimate test but usually not the one you meant.
- Keep the fixture clock. If a new case needs a different "now", override it in the eval case, not in the files.
- If a new disruption should have deterministic options with connections or reasoning notes, add an `entries` key to `alternatives_cache.json`; otherwise the `flights.csv` fallback is enough for nonstops.
- Policy changes go in `disruption_policy.json` first: bump `version`, add a `changelog` line, add or update an `examples[]` pair, then regenerate the matching sentence in `fare_rules_excerpt.md`. Never put a dollar amount in a prompt or in the fare-rules prose that is not in the table.
- Guardrail numbers ($25 meal, $40 ground, hotel propose-only, $75 / $200 goodwill tiers, refunds never executed, 15-minute holds, UI-minted confirmation tokens) are canon for the case study. Change them only if you are changing the story.
- New people: fictional names, `example.com`/`.net`/`.org` emails, masked phones and cards. New carriers: do not add any; partner segments stay `LK9xxx` marketed and "interline partner (fictional)" operated.
- Spanish content: mirror `cause_labels_customer.es` and keep replies in the customer's language; anything beyond en/es hands off.
- After any edit, run `python snippets/mock_backend.py --selftest` (it replays the `examples[]` in `disruption_policy.json` and the Stage 1 PNRs with no API key) and then the `grounded_facts` suite with `python snippets/eval_harness.py --suite grounded_facts --no-judge`; both are cheap and catch most drift.
