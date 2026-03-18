"""
skills/base.py — Abstract base class for all robot skills.

A Skill is a self-contained, reusable behaviour unit.
Skills are started/stopped by the MindService (deliberation layer).

Lifecycle:
    skill.can_run()  → check preconditions (mode, resources, conflicts)
    skill.start()    → begins execution in its own thread
    skill.stop()     → requests graceful cancellation
    skill.is_running → True while thread is alive

Skills publish SKILL_STARTED / SKILL_FINISHED events.
They must NOT block the event bus thread.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager
from core.safety import SafetyMonitor

log = logging.getLogger(__name__)


class Skill(ABC):
    """
    Abstract skill. Subclasses override `_run()`.

    Attributes:
        name          Unique skill identifier (used in events and logs)
        allowed_modes Set of modes in which this skill may run.
                      Empty set means "any mode".
    """

    name: str = "unnamed_skill"
    allowed_modes: frozenset[Mode] = frozenset()   # empty → unrestricted

    def __init__(
        self,
        bus: EventBus,
        mode_manager: ModeManager,
        safety: SafetyMonitor,
    ):
        self._bus      = bus
        self._mm       = mode_manager
        self._safety   = safety
        self._thread:  Optional[threading.Thread] = None
        self._stop_ev  = threading.Event()
        self._log      = logging.getLogger(f"skill.{self.name}")

    # ── public API ────────────────────────────────────────────────────────────

    def can_run(self) -> bool:
        """
        Override to add extra preconditions (e.g. RAM available, face present).
        Default: checks mode compatibility.
        """
        if self.allowed_modes and self._mm.current not in self.allowed_modes:
            return False
        if not self._mm.is_active():
            return False
        return True

    def start(self, *args, **kwargs) -> bool:
        if self.is_running:
            self._log.debug(f"{self.name} already running")
            return False
        if not self.can_run():
            self._log.debug(f"{self.name} cannot run in mode {self._mm.current.value}")
            return False
        self._stop_ev.clear()
        self._thread = threading.Thread(
            target=self._wrapper,
            args=args,
            kwargs=kwargs,
            daemon=True,
            name=f"Skill-{self.name}",
        )
        self._thread.start()
        self._bus.publish(
            EventType.SKILL_STARTED, {"skill": self.name}, source=self.name
        )
        return True

    def stop(self) -> None:
        self._stop_ev.set()

    def join(self, timeout: float = 5.0) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def should_stop(self) -> bool:
        return self._stop_ev.is_set()

    # ── internals ─────────────────────────────────────────────────────────────

    def _wrapper(self, *args, **kwargs) -> None:
        self._log.info(f"START")
        try:
            self._run(*args, **kwargs)
        except Exception as e:
            self._log.error(f"unhandled exception: {e}")
        finally:
            self._log.info(f"FINISH")
            self._bus.publish(
                EventType.SKILL_FINISHED, {"skill": self.name}, source=self.name
            )

    @abstractmethod
    def _run(self, *args, **kwargs) -> None:
        """Override with skill logic. Check self.should_stop in loops."""
        ...

    # ── helpers available to subclasses ──────────────────────────────────────

    def sleep(self, seconds: float) -> bool:
        """
        Interruptible sleep. Returns False if stop was requested.
        Use inside _run() loops instead of time.sleep().
        """
        return not self._stop_ev.wait(timeout=seconds)

    def __repr__(self) -> str:
        state = "running" if self.is_running else "idle"
        return f"<Skill {self.name} [{state}]>"
