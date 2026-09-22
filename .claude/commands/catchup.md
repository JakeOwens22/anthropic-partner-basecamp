The room has moved on and this person has not. Get them onto the room's current build fast,
without pretending they built what they missed. This is the one moment CLAUDE.md's "do not run
ahead" rule is switched off: the room set the pace, not them. The same thing starts when they
say "catch me up", "we're behind", "you're already on Build 3 and we never finished Build 2",
or anything like it.

There are two kinds of catch-up, and the first thing to do is work out which one this is.

1. Find out where they are and where the team is. Run `python3 verify.py` (the status board)
   and `python3 pod_sync.py --status`. Say both in one line: "You have 1.2 and 1.3 saved; the
   team's canon is at 2.2, pushed by Sam 40 minutes ago." Do not ask them which step they are
   on; read it.
2. Ask one question: which build is the room on right now? That is the target.
   - Room is on Build 2, Make the case, or Ship it: this is a **same-day catch-up**. Go to A.
   - Room is on the Day 2 re-open, Build 3, or Build 4: this is the **Day 2 catch-up**. Go to B.

## A. Same-day catch-up: walk them through what they missed, quickly

They still build it. The point is that they arrive at the room's build having written the
missed steps themselves, in ten minutes instead of forty-five. Coach harder than `/build`
does; do not hand over the file.

3. Take the missed steps in order, lowest unsaved first. For each one, in this rhythm:
   - Say the job in one sentence, in plain words: what this step makes the agent do.
   - Point at the `✏️` mark for it in `agent.py` (`grep -n '✏' agent.py`).
   - Ask them to say back what has to happen, in their words. One sentence, then move.
   - Describe the change in words up front (this is the shortcut `/build` does not take).
     Let them type it. If they ask you to write it, write it and walk the trace once.
   - `python3 verify.py <step>`. Paste the output verbatim as a code block. Green: next step.
4. Time-box it. Two real attempts at a gate, then stop: run `python3 pod_sync.py --take-canon`
   (their file is saved to `.workshop/mine/` first; say so before you run it) and
   `python3 pod_sync.py --history`, read the committer's note for that step aloud, and move
   on. Being present for the room's build matters more than owning every line of Build 1.
5. When the missed steps are green (or taken), hand over to `/build` for the step the room is
   on, with the single next command on its own line.

## B. Day 2 catch-up: make them fully ready for Day 2's learning

Day 2 (Build 3 and Build 4) is built on top of a working Day 1 agent, and the team shipped
one yesterday. Nobody rebuilds Day 1 this morning. The job is to put the team's shipped agent
on this laptop, prove it runs here, and re-establish what it does before the room starts
adding to it.

3. Take the canon: `python3 pod_sync.py --take-canon`. Their own `agent.py` is saved to
   `.workshop/mine/` first and the command prints the path; say that out loud before you run
   it. If it refuses because the canon is ahead of a gate they never saved, that is expected
   this morning; re-run with `--force`.
4. Prove it runs here. `python3 setup.py` until READY (a key that died overnight is the usual
   morning failure), then `python3 run.py K7PQ2M --trace`. Paste the trace verbatim. Ask what
   turn 2 shows and let the silence sit; that one question is most of the re-learning.
5. Read the team's history: `python3 pod_sync.py --history`. Every canon push carries the
   committer's note on what changed and why. Paste it as a code block, then translate the
   builds they missed into three sentences on the wire: what Claude is told about each tool,
   what the loop does after `stop_reason == "tool_use"`, and what the Build 2 tool the team
   added is for. If notes are thin, `diff .workshop/mine/<their file> agent.py` and narrate the
   difference in plain words, never line numbers.
6. Ask them to say back, in their words, what this agent does and what it still cannot do.
   Two sentences. If they cannot, that is the catch-up, and it is worth five minutes.
7. Check the board for Day 2's prerequisites: `python3 verify.py` with no step. Day 2 needs the
   Day 1 gates green on the canon (they are, or the team could not have shipped) and it does
   NOT need this person's own evidence codes for them. Evidence codes are personal; the canon
   does not save the gates they missed, and that is fine. Say so once so nobody goes hunting
   for a code they never earned.
8. Hand over to `/build` for the step the room is on (Build 3 starts at `3.1`), with the single
   next command on its own line.

What this is not: a fix for a broken environment (that is `python3 setup.py --fix` first, then
come back) or a way to skip a build the room is still on. If the room is still on their build,
it is `/build`, not this.
