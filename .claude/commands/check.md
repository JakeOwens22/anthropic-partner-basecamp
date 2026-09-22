Work out which step this repo is on, run its gate, and translate what it printed into plain
English. Assume nothing has been said yet: this may be the first thing typed in the session, so
do not ask which step they are on and do not ask them to run anything first. Read it yourself,
say where they are in one line, then run the gate.

This is the command to lead with for anyone who would rather not lead with a terminal.

1. Run `python3 verify.py` to see the status board. Take the lowest step that has not passed as
   their current step, and run `python3 verify.py <step>` for it. The steps are `1.2`, `1.3`,
   `1.4` (Build 1), `2.1`, `2.2` (Build 2), `3.1` (Build 3), `4.1` (Build 4). If the board is
   empty, or nothing imports, they are at `1.2` and the first thing to run is
   `python3 run.py K7PQ2M --trace`.
2. If it passes: tell them plainly, and give them the evidence code it printed. That is their
   receipt for the step. There is nothing to upload and nothing to push.
3. Then point them at `python3 readout.py`. It is the one-page submission for the build: what the
   agent IS and what it just DID, no upload, nothing to write up.
4. Paste what it printed into your reply as a code block, every line, verbatim, whether it
   passed or failed. Never a summary, never "see above": the tool card collapses, your reply
   does not. Then ask what turn 2 shows.
   Translate the ✗ lines and hints after the output, not instead of it, and don't just repeat the
   tool's own text.
5. If it won't import at all, that's a typo in their edit, not a misunderstanding. Find it, show
   them the one-character fix, move on. Don't turn a stray comma into a teaching moment.
6. If they're stuck on the same step twice in a row, raise it in the room and walk the trace with a teammate.
   Two traces of the same step side by side is the fastest diagnosis available in the room. Do not
   paste a teammate's `agent.py` in as the fix. That clears the gate and teaches nothing, and the
   next gate will find them out.
7. End every run with the single next command to type, on its own line.
