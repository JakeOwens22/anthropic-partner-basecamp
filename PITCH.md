# PITCH.md

Six lines and a lever. Your words. The last two are scored.

Built: `next_available_day` and `fare_rules`
Does: `next_available_day` takes into account the current flight plan and finds the next viable options && `fare_rules` Return the Larkspur Handbook text (fare rules and Customer Commitment, sections 4 to 7) behind an entitlement decision.
Number: $0.0527 / case
Safety check: Guardrails still need to be tested. There are a few tools in place (i.e. `check_policy` and `confirm_rebooking`) in addition to a max loop count that ensures that this model does not run unchecked and calls in a humna when needed
Next: This agent will be managed by your software dev team
Still broken: finishing deploying the rest of the tools
Lever: intelligence - the drawback with focusing on cost is that you will have a more expensive deployment and potentially increased latency, thus impacting the cost and speed sections.

## Priya asked

Costs: What does it cost per resolved contact, against the $6.90 a human contact costs them?
Wrong: What is the first untrue thing it says, and what happens after that?
Runs it: Who runs it in June, after your team has left?
Left out: What scope did you cut, and why did you cut it?


# Section 1.4
turn that took the most tokens in is turn 5. This had the most context to take into account. 