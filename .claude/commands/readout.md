Produce the build's readout and hand it off. This is the submission: one page, nothing to upload.

1. Run `python3 readout.py`. If it says there is no trace yet, run `python3 run.py <PNR> --trace`
   first and then re-run it. Never invent numbers for a page they haven't produced.
2. Report what it wrote, in this order, in plain English:
   - **The architecture:** how many tools the agent carries and how many of those are theirs, and
     the loop ceiling.
   - **The last run:** turns, tool calls, tokens in and tokens out.
   - **Whether the loop actually finished.** Say it explicitly: if the trace ends on
     `stop_reason=tool_use`, the loop stopped with the model still asking for a tool, and that is
     an unfinished run, not a fast one. If it ends anywhere else, say the loop closed.
3. If a number looks wrong to them, do not edit the readout. Walk the trace instead. The readout
   only reports what the run did.
4. Then the handoff. `readout.html` and `readout-trace.json` are committed by ONE person per
   build, the one the team agreed on, who also publishes the team's canon:

       python3 pod_sync.py --push-canon --note "what changed and why, one line"

   Everyone else runs `python3 pod_sync.py --take-canon` and commits nothing.
5. Nobody has an assigned job and there is no turn order. One person pushes the canon at the
   end of the build, the team agrees who before the clock runs out, and everyone else takes it.
   If they are not that person, give them the `--take-canon` line. Two people pushing canon in
   the same build is the conflict the rule exists to prevent.

End with the single next command to type, on its own line: `--push-canon` or `--take-canon`,
whichever is theirs.
