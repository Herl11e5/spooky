"""
The brain's parts, for /api/state — each working or not, as the body's /health.

deploy/release_agent.py (in robot-emulator) notes which parts work before it installs
a release and wants all of them back after: a microphone that goes quiet with a
release is the release's fault. The language model is not a part: it runs on the
Mac, which may simply be asleep, and a release must never be rolled back for that.
It is reported next to the parts (`llm`), not among them.
"""

from __future__ import annotations

from typing import Optional


def components(audio=None, body=None) -> dict:
    parts: dict = {}
    if audio is not None:
        parts.update(audio.health())
    if body is not None:
        age: Optional[float] = body.state_age_s
        parts["body_link"] = {"ok": body.connected,
                              "detail": "never reached" if age == float("inf") else f"state {age:.1f} s old"}
    return parts
