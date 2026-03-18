"""
core/safety.py — Safety monitor and constraint enforcer.

Responsibilities:
- Hard limits on motor speed and servo angles
- Obstacle stop (ultrasonic distance threshold)
- CPU temperature watchdog
- Low-memory watchdog
- Actuator error counting → safe_shutdown escalation

This module is the *first* to start and the *last* to stop.
It must never be blocked by other services.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from core.bus import EventBus, EventType
from core.modes import Mode

log = logging.getLogger(__name__)


# ── Safety limits (all tunable via config) ───────────────────────────────────

@dataclass
class SafetyLimits:
    # Motion
    max_speed: int          = 60    # % of full power (0-100)
    min_obstacle_cm: float  = 15.0  # stop if closer than this
    # Servos
    pan_min:  int           = -60   # degrees
    pan_max:  int           =  60
    tilt_min: int           = -30
    tilt_max: int           =  20
    # Thermal
    max_cpu_temp_c: float   = 80.0  # → OVERTEMP event
    warn_cpu_temp_c: float  = 70.0  # → log warning
    # Memory
    min_ram_mb: int         = 300   # → LOW_MEMORY event
    warn_ram_mb: int        = 600   # → log warning
    # Fault tolerance
    max_actuator_errors: int = 5    # → SAFE_SHUTDOWN
    watchdog_interval_s: float = 2.0


# ── SafetyMonitor ─────────────────────────────────────────────────────────────

class SafetyMonitor:
    """
    Periodic watchdog that reads system health and enforces hard motion limits.

    Starts a background thread. Publishes:
      - EventType.OBSTACLE_DETECTED / OBSTACLE_CLEARED
      - EventType.OVERTEMP
      - EventType.LOW_MEMORY
      - EventType.SAFETY_FAULT  (forces mode → safe_shutdown via ModeManager)
    """

    def __init__(self, bus: EventBus, limits: Optional[SafetyLimits] = None):
        self._bus    = bus
        self._limits = limits or SafetyLimits()
        self._lock   = threading.Lock()
        self._active = False
        self._thread: Optional[threading.Thread] = None

        # State
        self._obstacle_blocked    = False
        self._actuator_errors     = 0
        self._last_distance_cm    = 999.0
        self._current_speed_pct   = 0

        # Inject sensor callbacks — set by SensorService after init
        self._get_distance_cm: Optional[callable] = None
        self._get_cpu_temp_c:  Optional[callable] = None
        self._get_ram_free_mb: Optional[callable] = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._active = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="SafetyMonitor"
        )
        self._thread.start()
        log.info("SafetyMonitor started")

    def stop(self) -> None:
        self._active = False
        if self._thread:
            self._thread.join(timeout=5.0)
        log.info("SafetyMonitor stopped")

    # ── sensor injection ──────────────────────────────────────────────────────

    def register_distance_fn(self, fn: callable) -> None:
        self._get_distance_cm = fn

    def register_cpu_temp_fn(self, fn: callable) -> None:
        self._get_cpu_temp_c = fn

    def register_ram_fn(self, fn: callable) -> None:
        self._get_ram_free_mb = fn

    # ── hard limits (called synchronously by MotorService) ───────────────────

    def clamp_speed(self, speed: int) -> int:
        """Clamp speed to configured max. Call before every motor command."""
        clamped = max(0, min(abs(speed), self._limits.max_speed))
        return clamped if speed >= 0 else -clamped

    def clamp_pan(self, angle: int) -> int:
        return max(self._limits.pan_min, min(self._limits.pan_max, angle))

    def clamp_tilt(self, angle: int) -> int:
        return max(self._limits.tilt_min, min(self._limits.tilt_max, angle))

    @property
    def is_obstacle_blocked(self) -> bool:
        return self._obstacle_blocked

    @property
    def last_distance_cm(self) -> float:
        return self._last_distance_cm

    def record_actuator_error(self, description: str = "") -> None:
        """Call whenever a motor/servo command raises an exception."""
        with self._lock:
            self._actuator_errors += 1
            count = self._actuator_errors
        log.warning(f"Actuator error #{count}: {description}")
        if count >= self._limits.max_actuator_errors:
            self._bus.publish_sync(
                EventType.SAFETY_FAULT,
                {"reason": f"too many actuator errors ({count})", "description": description},
                source="SafetyMonitor",
            )

    # ── watchdog loop ─────────────────────────────────────────────────────────

    def _run(self) -> None:
        while self._active:
            try:
                self._check_distance()
                self._check_temperature()
                self._check_memory()
            except Exception as e:
                log.error(f"SafetyMonitor watchdog error: {e}")
            time.sleep(self._limits.watchdog_interval_s)

    def _check_distance(self) -> None:
        if self._get_distance_cm is None:
            return
        try:
            dist = float(self._get_distance_cm())
            self._last_distance_cm = dist
        except Exception:
            return

        blocked = dist < self._limits.min_obstacle_cm
        if blocked and not self._obstacle_blocked:
            self._obstacle_blocked = True
            log.warning(f"Obstacle! distance={dist:.1f} cm")
            self._bus.publish(
                EventType.OBSTACLE_DETECTED,
                {"distance_cm": dist},
                source="SafetyMonitor",
            )
        elif not blocked and self._obstacle_blocked:
            self._obstacle_blocked = False
            self._bus.publish(
                EventType.OBSTACLE_CLEARED,
                {"distance_cm": dist},
                source="SafetyMonitor",
            )

    def _check_temperature(self) -> None:
        if self._get_cpu_temp_c is None:
            return
        try:
            temp = float(self._get_cpu_temp_c())
        except Exception:
            return

        if temp >= self._limits.max_cpu_temp_c:
            log.error(f"CPU overtemp: {temp:.1f}°C")
            self._bus.publish_sync(
                EventType.OVERTEMP,
                {"temp_c": temp, "reason": f"CPU {temp:.1f}°C ≥ {self._limits.max_cpu_temp_c}°C"},
                source="SafetyMonitor",
            )
        elif temp >= self._limits.warn_cpu_temp_c:
            log.warning(f"CPU temp high: {temp:.1f}°C")

    def _check_memory(self) -> None:
        if self._get_ram_free_mb is None:
            return
        try:
            ram = int(self._get_ram_free_mb())
        except Exception:
            return

        if ram < self._limits.min_ram_mb:
            log.error(f"RAM critically low: {ram} MB free")
            self._bus.publish(
                EventType.LOW_MEMORY,
                {"ram_free_mb": ram},
                source="SafetyMonitor",
            )
        elif ram < self._limits.warn_ram_mb:
            log.warning(f"RAM low: {ram} MB free")

    def __repr__(self) -> str:
        return (
            f"<SafetyMonitor blocked={self._obstacle_blocked} "
            f"dist={self._last_distance_cm:.0f}cm "
            f"actuator_errors={self._actuator_errors}>"
        )
