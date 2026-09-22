"""Cartoon-animation prototype config. Test-phase only - not wired into any
scheduled/production workflow yet (see reels/cartoon/README-status below).

COST_MODE=FREE_ONLY is a hard switch, not a suggestion: every backend in this
package must check it and refuse to run rather than silently reaching for a
paid provider. There is currently only one backend (FreeTestBackend); this
switch exists so that adding a paid backend later can never become the
default by accident.
"""
from __future__ import annotations

import os

COST_MODE = os.environ.get("CARTOON_COST_MODE", "FREE_ONLY").strip().upper()

if COST_MODE not in {"FREE_ONLY"}:
    # Only one value is implemented on purpose - see module docstring. A future
    # paid backend must add itself here explicitly, never by default.
    raise RuntimeError(
        f"Unsupported CARTOON_COST_MODE={COST_MODE!r}. Only FREE_ONLY is implemented."
    )


class NoFreeBackendAvailable(RuntimeError):
    """Raised when COST_MODE=FREE_ONLY and no free backend can serve the request.

    Must never trigger a silent fallback to a paid provider - the caller is
    expected to surface this message as-is and stop.
    """


class QuotaExceeded(RuntimeError):
    """Raised when the free backend's own quota system rejects a request.

    Carries the provider's stated reset time (if any) so the caller can report
    it and stop, per the "do not keep retrying" rule.
    """

    def __init__(self, message: str, reset_hint: str = ""):
        super().__init__(message)
        self.reset_hint = reset_hint
