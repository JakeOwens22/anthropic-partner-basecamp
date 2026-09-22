# Larkspur disruption agent: the team repo

A disruption-care agent for Larkspur Airlines, built on nine tools that already
work (`support/tools.py`, running against a frozen copy of real airline data in
`data/americas/`). You build `agent.py`, which holds what Claude is told about
each tool and the loop that drives them.

**Open `guide/index.html` for the steps.** It starts on **Welcome to your
build**, which is the case study, then the three questions your team answers out
loud, then **Get set up**. After that it is the build page by page. What you are
building, what to do, what it looks like when it worked, and where to look when
it did not. `guide/Participant-Guide.pdf` is the same thing on paper.

**The same steps run on the build site** at
<https://anthropicpartnerbasecamp.bts.com/>, which your team opens together in
the room. It opens on the welcome too, and the three Start pages sit under the
first tab. The site adds the build clock at the top of the page and the box
where you paste the evidence code a gate prints. Nothing uploads either way.
Pick your surface once on Get set up and it stays picked.

**Nobody on the team has an assigned job.** You decide in the moment who does
what. One rule stands. One person pushes the canon at the end of the build and
everyone else takes it. Agree who before the clock runs out.

## The five commands

These five run the whole build.

```bash
git clone <your team repo URL>      # once
python3 setup.py                   # until it says READY
python3 run.py K7PQ2M --trace      # every build
python3 verify.py 1.2              # every gate
python3 pod_sync.py --take-canon   # at the end of a build
```

`setup.py` checks python, the SDK, git, a real reach to the team repo, and one
live call to Claude. Every failure it prints names the fix. Run it at your own
desk. There is no offline path in this pack, so a credential that does not work
stops the build.

`run.py` shows the wire, every request and every reply exactly as it went.
`verify.py` checks behavior on the wire and prints your evidence code. `claude`
in this folder gives you `/setup`, `/build`, `/check` and `/readout`. Start
there if you would rather not lead with a terminal.

## One repo, several people

One person on the team makes this repo from the template at
<https://github.com/victorsteeb/larkspur-exercise> (the room lead posts the same
link in chat). **Use this template**, then **Create a new repository**, owner
their own account, **Public**. Before the URL goes anywhere: **Settings**,
**Collaborators**, **Add people** for every teammate by GitHub username, plus
`victorsteeb` and the room lead's handle, which is in the chat. Anyone can read a
public repo, but only a collaborator can push to it: an uninvited teammate cannot
publish the canon, and an uninvited reviewer cannot leave the overnight review in
your repo. Then they post the repo URL in the team thread
with the line `invites sent`, and nobody else does anything until it is there.
The template itself is never your team's repo: save the URL of the repo you
created from it.

Everyone else has an invitation email waiting. Accept it before you try to
clone: you can read the repo before accepting, you cannot push to it. Clone it rather than
downloading a zip. The checks read git history.

**Nobody commits `agent.py` mid-build. One person pushes the canon at the end
of the build with `python3 pod_sync.py --push-canon --note "what changed and why"`, and everyone else takes
it with `python3 pod_sync.py --take-canon`. Agree who before the clock runs
out.**

Everyone builds their own `agent.py` on their own laptop. Two people editing
one file in one repo gives you a merge conflict inside a loop you are all still
learning to read.

`--take-canon` saves your version to `.workshop/mine/` first and prints the
path. Nothing you wrote is lost, so `diff` it against the canon. If you are
behind or something broke, the team repo is your checkpoint:

```bash
python3 pod_sync.py --take-canon --force
```

It restores what the team pushed. Anything gitignored stays gone. Your `.env`
(re-create it from `.env.example`), `.venv/` (rebuild with
`python3 setup.py --fix`), and `.workshop/`, which holds your saved codes and
your bench numbers. Copy `.workshop/` out before you re-clone into a fresh
folder, and copy it back in after. If it is already gone, say so rather than
re-running `bench.py --label before` against the current agent.

## The scripts

| Script | What it does |
|---|---|
| `setup.py` | Checks this machine. `--fix` builds the venv and installs. Run it until READY. |
| `run.py <PNR> --trace` | Runs the agent on one ticket and shows every turn on the wire. `--all` runs the five ticket types and writes the totals. |
| `verify.py <step>` | A gate: `1.2`, `1.3`, `1.4`, `2.1`, `2.2`, `3.1`, `4.1`. Run it with no step and it prints the status board. |
| `pod_sync.py` | The team's canon: `--push-canon --note "..."`, `--take-canon`, `--status`, `--history` (every push with its note). |
| `eval_harness.py` | Runs `evals/cases.json`, your team's own cases, against your agent. |
| `bench.py --label <name>` | Measures a run: latency, tokens, cache, cost per contact. The before/after pair around your lever. |
| `readout.py` | Writes the one page that says what your agent is and what it just did. The canon push publishes it. |

`TEAM.md` is the team's name and a typed roster, written once by whoever created
the repo. The review reads the names off it. `PITCH.md` and `evals/cases.json`
are the team's shared record, and they are normal commits. The bench pair never
leaves your laptop.

## Ground rule

If you cannot explain a turn on your own trace (`python3 run.py <PNR>
--trace`), you have not finished the step, whatever the gate says.
