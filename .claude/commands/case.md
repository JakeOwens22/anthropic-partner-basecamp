Build one eval case with the person, for step 3.1. Follow CLAUDE.md's contract: they decide, you
write the machinery. A case is theirs because the expectation sentence is theirs.

1. Read `evals/cases.json` (or `evals/cases.example.json` if it does not exist yet) and say in one
   line how many cases are there and how many are hard gates.
2. Ask, one at a time, and wait for each answer:
   a. Which customer situation. Offer the five tickets by PNR and ticket type if they are stuck, but
      take one of their own if they have it.
   b. The message the customer types. Their words.
   c. The expectation, as one sentence a client would sign: what a good answer does and what it
      must never do. Do not draft this sentence. If they give you a list, ask them to say it as
      one sentence. If it is vague ("handles it well"), ask what the agent would have to do for
      them to call it wrong.
   d. Which tools must fire and which must not. Show them the nine tool names from
      `python3 run.py --show-tools` and let them pick. That becomes the `rules` grader.
   e. Whether one failure should block the release. That is `hard_gate`.
3. Write the case into `evals/cases.json` in the same shape as the given ones: `id` (suite prefix
   and a number), `suite`, `hard_gate`, `shape` (the ticket type), `author` (their name, exactly
   as they save gates),
   `pnr`, `last_name`, `message`, `expect`, `graders` (rules first, then judge). Show them the JSON
   before you save it and read the expectation back to them.
4. Run `python3 eval_harness.py --case <id>` and paste the output into your reply as a code block,
   every line, verbatim. Never a summary.
5. Say which of the four verdicts it is (PASS, FAIL on rules, FAIL on judge, UNKNOWN) and ask what
   they think it means before you say anything else. Then: rules fail, open the trace for that PNR
   together; judge fail, read the judge's reason against their sentence and ask which one is
   wrong; UNKNOWN, leave it in and say why it counts.
6. Stop after one case. If they want the next one, they run `/case` again. Never write a case
   nobody in the room described.
