"""
services/sensor.py — Sensor abstraction: ultrasonic, CPU temp, RAM.

Reads hardware periodically and publishes structured events.
Also exposes synchronous getters so SafetyMonitor can poll.

Simulation values are used when hardware is unavailable.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from core.bus import EventBus, EventType

log = logging.getLogger(__name__)


# ── Hardware import ───────────────────────────────────────────────────────────

try:
    from robot_hat import Ultrasonic, Pin
    _ULTRASONIC_AVAILABLE = True
except ImportError:
    _ULTRASONIC_AVAILABLE = False


# ── SensorService ─────────────────────────────────────────────────────────────

class SensorService:
    """
    Polls ultrasonic sensor, CPU temperature, and RAM.
    Publishes events on notable changes.
    Exposes simple getter methods for SafetyMonitor callbacks.

    Run interval: ~100 ms for ultrasonic, ~2 s for system metrics.
    """

    # PiCrawler ultrasonic wiring (robot_hat)
    _TRIG_PIN = "D2"
    _ECHO_PIN = "D3"

    def __init__(
        self,
        bus: EventBus,
        poll_interval_s: float = 0.10,
        system_interval_s: float = 2.0,
    ):
        self._bus       = bus
        self._poll_s    = poll_interval_s
        self._sys_s     = system_interval_s
        self._active    = False
        self._thread:   Optional[threading.Thread] = None
        self._sys_thread: Optional[threading.Thread] = None

        # Latest readings (thread-safe via GIL for simple float/int)
        self._distance_cm: float = 999.0
        self._cpu_temp_c:  float = 0.0
        self._ram_free_mb: int   = 8192

        # Hardware
        self._ultrasonic: Optional[object] = None
        if _ULTRASONIC_AVAILABLE:
            try:
                self._ultrasonic = Ultrasonic(Pin(self._TRIG_PIN), Pin(self._ECHO_PIN))
                log.info("SensorService: ultrasonic initialised")
            except Exception as e:
                log.warning(f"SensorService: ultrasonic init failed — {e}")

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._active = True
        self._thread = threading.Thread(
            target=self._run_ultrasonic, daemon=True, name="Sensor-US"
        )
        self._sys_thread = threading.Thread(
            target=self._run_system, daemon=True, name="Sensor-Sys"
        )
        self._thread.start()
        self._sys_thread.start()
        log.info("SensorService started")

    def stop(self) -> None:
        self._active = False
        log.info("SensorService stopped")

    # ── synchronous getters (for SafetyMonitor callbacks) ────────────────────

    def get_distance_cm(self) -> float:
        return self._distance_cm

    def get_cpu_temp_c(self) -> float:
        return self._cpu_temp_c

    def get_ram_free_mb(self) -> int:
        return self._ram_free_mb

    # ── poll loops ────────────────────────────────────────────────────────────

    def _run_ultrasonic(self) -> None:
        while self._active:
            try:
                dist = self._read_distance()
                self._distance_cm = dist
            except Exception as e:
                log.debug(f"SensorService ultrasonic read error: {e}")
            time.sleep(self._poll_s)

    def _run_system(self) -> None:
        while self._active:
            try:
                temp = self._read_cpu_temp()
                ram  = self._read_ram_free_mb()
                self._cpu_temp_c  = temp
                self._ram_free_mb = ram
                # Publish heartbeat with current system state
                self._bus.publish(
                    EventType.HEARTBEAT,
                    {
                        "distance_cm": round(self._distance_cm, 1),
                        "cpu_temp_c":  round(temp, 1),
                        "ram_free_mb": ram,
                    },
                    source="SensorService",
                )
            except Exception as e:
                log.error(f"SensorService system read error: {e}")
            time.sleep(self._sys_s)

    # ── hardware readers ──────────────────────────────────────────────────────

    def _read_distance(self) -> float:
        if self._ultrasonic is not None:
            val = self._ultrasonic.read()
            if val is not None and val > 0:
                return float(val)
        return 999.0   # simulation / no reading

    def _read_cpu_temp(self) -> float:
        # RPi thermal zone
        for path in (
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/devices/virtual/thermal/thermal_zone0/temp",
        ):
            try:
                return int(Path(path).read_text()) / 1000.0
            except Exception:
                continue
        # macOS fallback via powermetrics (won't be 100% accurate, just for dev)
        try:
            import subprocess
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.xcpm.cpu_thermal_level"], timeout=1
            )
            return float(out.strip()) * 0.5 + 30  # rough approximation
        except Exception:
            return 0.0

    def _read_ram_free_mb(self) -> int:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) // 1024
        except FileNotFoundError:
            pass
        # macOS / fallback via vm_stat
        try:
            import subprocess
            out = subprocess.check_output(["vm_stat"], timeout=2).decode()
            pages_free = 0
            page_size  = 4096
            for line in out.splitlines():
                if "Pages free" in line:
                    pages_free = int(line.split(":")[1].strip().rstrip("."))
                elif "page size of" in line:
                    page_size = int(line.split("page size of")[1].split()[0])
            return (pages_free * page_size) // (1024 * 1024)
        except Exception:
            return 4096   # assume plentiful if we can't read

    def __repr__(self) -> str:
        return (
            f"<SensorService dist={self._distance_cm:.1f}cm "
            f"temp={self._cpu_temp_c:.1f}°C ram={self._ram_free_mb}MB>"
        )
