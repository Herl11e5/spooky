"""
core/habituation.py — Getting used to things, as animals do.

Every time something of a kind happens it adds one to that kind's trace, and the
trace halves every `fade_s`; the response is 0.5 ** trace. Two at once: half as
strong. One a half-life ago: about 0.6. A few half-lives of quiet: fresh again.
A busy place stops being news, a quiet one leaves the robot as it was. Used by the
startle (bangs, looming, lights) and by the remarks on people's faces.
"""

from __future__ import annotations

from typing import Hashable


class Habituation:

    def __init__(self, fade_s: float = 900.0) -> None:
        self._fade = fade_s
        self._trace: dict = {}                             # kind → (trace, when)

    def _now(self, kind: Hashable, now: float) -> float:
        trace, when = self._trace.get(kind, (0.0, now))
        return trace * 0.5 ** ((now - when) / self._fade)

    def response(self, kind: Hashable, now: float) -> float:
        """How strong a response would be now, without counting this time."""
        return 0.5 ** self._now(kind, now)

    def note(self, kind: Hashable, now: float) -> None:
        self._trace[kind] = (self._now(kind, now) + 1.0, now)

    def strength(self, kind: Hashable, now: float) -> float:
        """The response to this time, which then counts."""
        s = self.response(kind, now)
        self.note(kind, now)
        return s
