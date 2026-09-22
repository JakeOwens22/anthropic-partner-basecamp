"""Offline path: NOT YET BUILT.

A real offline path means a deterministic simulator: a fake Messages API, a
fixed jitter sequence, and a tool-selection cascade tuned against measured live
behaviour. Larkspur doesn't have one. This pack was built with live API access
throughout, so the offline path was never the bottleneck.

This stub exists only so `LARKSPUR_OFFLINE=1` fails with a clear message
instead of an ImportError stack trace. Before a cohort runs on a network you
don't trust, build the real thing first, tuned to check_policy's
resolution_order and the confirm_rebooking guardrail.
"""


class OfflineClient:
    class _Messages:
        def create(self, **kwargs):
            raise NotImplementedError(
                "LARKSPUR_OFFLINE isn't built yet. There's no deterministic simulator "
                "behind this flag. If you have a live Anthropic credential, don't set "
                "LARKSPUR_OFFLINE; if you don't and you're blocked, that's a real gap to "
                "raise with a facilitator, not something to work around here."
            )

        def stream(self, **kwargs):
            return self.create(**kwargs)

    def __init__(self):
        self.messages = self._Messages()
