Get this machine ready to build, and do not stop until it is.

**Step 0, before you write any credential anywhere.** Run `git check-ignore -q .env`
and `git log --all --oneline -- .env`. If `.env` is not ignored, fix `.gitignore`
first and only then write the file. If the log shows `.env` has ever been
committed, stop: that is a key in the repo's history, it is on the remote, and
it needs the key rotated by whoever issued it, not a `git rm`. Say so plainly,
tell them to raise it in the room now, and stop there.

Then run `python3 setup.py --json` and read the result. Then loop:

1. Take the FIRST failing required check. They are in dependency order: this
   laptop, then git, then the credential. Fix that one thing, using the check's
   own `fix` text as the primary instruction. Typical fixes you may apply
   directly: create the venv (`python3 setup.py --fix`), write `.env` from
   `.env.example` (ask them to paste the key, never invent one, never echo it
   back), `git config` identity, a `gh auth login` walkthrough, clone or
   re-clone the team repo.
2. Re-run `python3 setup.py --json`. Repeat until `ready` is true or you hit
   something only a human can do.
3. Things you must HAND BACK, not do: pasting credentials, creating the GitHub
   repo under their account, anything needing their password or browser login.
   Set everything else up to the last keystroke and give them the exact
   commands for the rest.
4. Things you must NEVER do: edit `support/`, `verify.py`, `setup.py`,
   `readout.py` or `pod_sync.py` to make a check pass; weaken `.gitignore` or
   `.gitattributes`. If a check itself seems wrong, say so out loud. That is a
   workshop bug worth flagging.
5. Before ANY re-clone, copy the old `.workshop/` across to the new checkout.
   It holds their saved codes and their bench baselines, none of which exist
   on the remote and none of which can be reconstructed. Re-cloning over the
   top of it is how a team loses its before-measurement.
6. When it says READY, say so in one line and stop. There is nothing to stamp
   and nothing to push.

Narrate briefly as you go, one line per fix, so they learn what their machine
needed. End with the single next command to type, on its own line.
