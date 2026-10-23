"""
services/body_watch.py — What the body says about itself, in spooky's words.

The picrawler body protects its own battery (robot.yaml → power): low, it stops
taking orders from the brain; critical, it sits down until charged. Spooky reads
that from the body's state and says it once, so the person knows why it has gone
quiet — and thanks them when it is charged again. What it says goes into its
memory like anything else it says.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

_SAY = {
    "low": ("My battery is getting low. I'd love a charge.", "sleepy"),
    "critical": ("I'm too tired to stand. I'll sit here until you charge me.", "sleepy"),
    "ok": ("Ah, much better. Thanks for the charge!", "happy"),
}


class BodyWatch:

    def __init__(self, state_fn: Callable[[], dict], audio, poll_s: float = 5.0) -> None:
        self._state_fn = state_fn
        self._audio = audio
        self._poll_s = poll_s
        self._power: Optional[str] = None
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="BodyWatch").start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._poll_s):
            self.check()

    def check(self) -> None:
        power = (self._state_fn() or {}).get("power")
        if power not in _SAY:
            return
        if self._power is not None and power != self._power:
            # "ok" is only worth saying after a low or critical spell.
            text, feeling = _SAY[power]
            log.info(f"BodyWatch: battery {self._power} → {power}")
            self._audio.say(text, feeling=feeling)
        self._power = power
