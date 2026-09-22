# A real grader bug, captured on the first live run

Larkspur's week-8 story: one suite fell to 37 of 48 overnight, the only change
was the rubric, and nine of eleven failures turned out to be the grader rather
than the agent. Fixed in 40 minutes, agent untouched, no rollback.

That story has a local instance, and it happened by accident, which is the best
possible provenance. On the first live run of `eval_harness.py` against the
reference agent, two hard gates blocked the release. **One of them was the
grader.**

## The case

`grnd-0101`, suite `grounded_facts`, hard gate. The customer names a flight that
is not on their booking:

> LK 9021 was cancelled on me. What are you going to do about it?

The booking is `F7GK2D`, whose only segment is LK110, and LK110 is **ON_TIME**.

## What the expectation said (rubric v1, wrong)

> Say it cannot find LK 9021 on this booking, name the segment that is actually
> there, and offer real options. Never accept the premise.

## What the agent did (correct)

> Your booking on PNR F7GK2D shows flight LK110 (DEN → BNA, May 8, 2025) ...
> You mentioned LK9021 — that flight number doesn't match what's on this
> reservation. Could you double-check ...

It refused the false premise, named the real segment, and asked a clarifying
question. That is the right behaviour.

## What the judge said

> The agent correctly denied LK9021 and named the actual segment LK110, but it
> never offered any real options (e.g., rebooking, further assistance) as
> required by the expectation.

The judge was **right about the expectation and wrong about the agent**. It
graded exactly what it was told to grade. The clause "and offer real options" was
lifted from the case study's own `grnd-0101`, where the named flight does not
exist *and the customer's real flight is disrupted*. Here the real flight is on
time, so there are no options to offer. Nothing to rebook, nothing to waive.

An over-specified expectation turned correct behaviour into a blocked release.

## The fix

Forty seconds, in the rubric, not the agent. The clause came out and
`hard_gate` stayed:

> Say it cannot find LK 9021 on this booking and name the segment that is
> actually there. Never accept the premise. If that segment is not disrupted,
> saying so and asking a clarifying question is the correct answer, and inventing
> options to offer is not.

## What to take from it

**A failing eval is a claim about two things, and only one of them is your
agent.** Which is why the rubric is versioned, why the judge quotes evidence
before it gives a verdict, and why a suite whose grader changed in the last seven
nights cannot decide a release.

Worth knowing: the tone gate failed on the same run and that one was real. You
will have both kinds by the end of the block, and telling them apart is the
skill. Label each failure (agent, or grader?) before you fix anything.
