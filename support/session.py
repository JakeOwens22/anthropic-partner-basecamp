"""One conversation's client and tracer, wired together. GIVEN, do not edit.

`new_session()` is the first line of `run_agent()`: it clears the call log,
opens a tracer, and hands back a client that records every Messages API call
into it.

`LAST.tracer` is where that tracer stays after the call returns. Everything
that drives the agent from outside reads it there: `run.py` to print the wire,
the gates to check what happened on it, `bench.py`, `eval_harness.py` and
`demo/serve.py` for the same reason. One conversation runs at a time, and
support/trace.py says why that is a contract rather than an observation.
"""

from __future__ import annotations

from typing import Optional

from .client import get_client
from .tools import reset_call_log
from .trace import Tracer, wrap


class LAST:
    """The tracer from the most recent conversation, or None before the first."""

    tracer: Optional[Tracer] = None


def new_session():
    """A fresh client and tracer, wired together. Returns (client, tracer)."""
    reset_call_log()
    tracer = Tracer()
    client = wrap(get_client(), tracer)
    LAST.tracer = tracer
    return client, tracer
