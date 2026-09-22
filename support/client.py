"""Connecting to Claude: one function, several credential paths, one failure mode.

This file is GIVEN. It exists so that "it doesn't work" is never ambiguous.

YOU DO NOT NECESSARILY NEED AN API KEY. The SDK resolves credentials in this
order, first match wins:

  1. LARKSPUR_OFFLINE=1         the offline simulator: NOT BUILT in this pack.
                                The flag is wired up so it fails with a sentence
                                instead of a stack trace; it is not a way to
                                work without a credential.
  2. ANTHROPIC_API_KEY          a key, from your environment or a local .env
  3. ANTHROPIC_AUTH_TOKEN       a bearer token
  4. an OAuth profile           from `ant auth login`, your normal Claude login,
                                stored in ~/.config/anthropic/. No key anywhere.
  5. Workload Identity Federation env vars
  6. AWS_BEARER_TOKEN_BEDROCK   Amazon Bedrock (+ AWS_REGION)

So this module deliberately does NOT pre-judge whether you have a credential.
An unset ANTHROPIC_API_KEY does not mean you have none. It builds a client and
lets the first real call be the verdict. That's the only check that can't be
wrong.

THE TRAP, since it will cost someone twenty minutes: a stale exported
ANTHROPIC_API_KEY silently outranks an OAuth profile, and an *empty* one still
outranks it while failing every request. `ant auth status` shows which source
actually won.

Never put a key in a file you'd commit, and never paste one into a chat window.
"""

from __future__ import annotations

import glob
import os
from typing import Optional, Tuple

_DOTENV_LOADED = False

# The exercise root: support/client.py's parent's parent. The default .env is
# resolved against THIS, not the current working directory, so `python3
# some/where/run.py` from a different folder still finds the pod's key file.
_EXERCISE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DOTENV = os.path.join(_EXERCISE_ROOT, ".env")


def load_dotenv(path: Optional[str] = None) -> None:
    """Minimal .env reader: deliberately not a dependency.

    python-dotenv is one more thing to install and one more thing to fail on a
    locked-down laptop. This handles the 99% case: KEY=value, one per line.

    With no argument it reads the exercise root's .env regardless of where you
    ran python3 from. Pass a path to read some other file.
    """
    global _DOTENV_LOADED
    path = path or DEFAULT_DOTENV
    if _DOTENV_LOADED or not os.path.exists(path):
        _DOTENV_LOADED = True
        return
    with open(path) as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    _DOTENV_LOADED = True


def oauth_profile() -> Optional[str]:
    """Name of an `ant auth login` profile on disk, if there is one."""
    config = os.environ.get("ANTHROPIC_CONFIG_DIR")
    if not config:
        if os.name == "nt":
            appdata = os.environ.get("APPDATA")
            config = os.path.join(appdata, "Anthropic") if appdata else ""
        else:
            config = os.path.expanduser("~/.config/anthropic")
    if not config or not os.path.isdir(config):
        return None
    found = sorted(glob.glob(os.path.join(config, "credentials", "*.json")))
    if not found:
        return None
    wanted = os.environ.get("ANTHROPIC_PROFILE")
    names = [os.path.splitext(os.path.basename(p))[0] for p in found]
    if wanted:
        return wanted if wanted in names else None
    return "default" if "default" in names else names[0]


def credential_status() -> Tuple[str, str]:
    """Return (mode, detail) without exposing the secret itself.

    Never returns "none". See the module docstring. The worst case is
    "unknown", meaning nothing was detected here and the live call decides.
    """
    load_dotenv()
    if os.environ.get("LARKSPUR_OFFLINE") == "1":
        return "offline", "LARKSPUR_OFFLINE=1"

    if "ANTHROPIC_API_KEY" in os.environ and not os.environ["ANTHROPIC_API_KEY"].strip():
        return "empty-key", (
            "ANTHROPIC_API_KEY is set but empty. It outranks every other credential "
            "and fails every request. Unset it: unset ANTHROPIC_API_KEY"
        )

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        if not key.startswith("sk-ant-"):
            return "anthropic", "key found, but it does not start with sk-ant- (check for a typo)"
        return "anthropic", "ANTHROPIC_API_KEY ending …%s" % key[-4:]

    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "token", "ANTHROPIC_AUTH_TOKEN is set"

    profile = oauth_profile()
    if profile:
        return "profile", "signed in via `ant auth login` (profile: %s), no API key needed" % profile

    if os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        region = os.environ.get("AWS_REGION")
        if not region:
            return "bedrock", "Bedrock token found but AWS_REGION is not set"
        return "bedrock", "Bedrock in %s" % region

    return "unknown", "nothing detected here. The live call below is the real check"


class MissingCredential(RuntimeError):
    pass


def get_client(offline: Optional[bool] = None):
    """Return a Claude client. Raises MissingCredential with a fix, never a stack trace."""
    load_dotenv()
    if offline is None:
        offline = os.environ.get("LARKSPUR_OFFLINE") == "1"
    if offline:
        from .offline import OfflineClient
        return OfflineClient()

    mode, detail = credential_status()
    if mode == "empty-key":
        raise MissingCredential(
            "ANTHROPIC_API_KEY is set but empty.\n"
            "  It outranks your OAuth profile and fails every request.\n"
            "  Fix: unset ANTHROPIC_API_KEY"
        )

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise MissingCredential(
            "The anthropic package isn't installed in this Python.\n"
            "  Fix: python3 setup.py --fix"
        ) from exc

    if mode == "bedrock":
        if "AWS_REGION" not in os.environ:
            raise MissingCredential(
                "Bedrock token found but AWS_REGION is not set.\n"
                "  Fix: export AWS_REGION=us-east-1  (use the region your models are enabled in)"
            )
        from anthropic import AnthropicBedrock
        return AnthropicBedrock(aws_region=os.environ["AWS_REGION"])

    # Everything else, including "unknown". A bare client resolves an OAuth
    # profile, a bearer token, or federated credentials on its own; if there is
    # genuinely nothing, the request fails with an auth error we can explain.
    return anthropic.Anthropic()
