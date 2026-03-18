"""
main.py — Robot runtime entry point.

Boots all services in dependency order, wires them together,
then blocks until shutdown is requested.

Usage:
    python main.py [--config config/local.yaml] [--debug]
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import signal
import sys
import time
import threading
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── Core imports ──────────────────────────────────────────────────────────────
from core.logger import setup_logging
from core.config import load_config
from core.bus    import EventBus, EventType
from core.modes  import Mode, ModeManager
from core.safety import SafetyMonitor, SafetyLimits

# ── Service imports ───────────────────────────────────────────────────────────
from services.sensor import SensorService
from services.motor  import MotorService
from services.memory import MemoryService
from services.alert  import AlertService, FusionConfig
from services.audio        import AudioService
from services.vision       import VisionService, FaceDatabase, set_ollama_lock
from services.mind         import MindService, OllamaBrain, get_ollama_lock
from services.choreography  import Choreography
from services.conscience    import Conscience
from services.dashboard     import DashboardService, shared
from services.night_watch    import NightWatchService
from services.alert_adapters import build_adapters
from services.learning       import LearningService
from services.experiment     import ExperimentEngine
from services.summarizer     import Summarizer, set_ollama_lock as set_summarizer_lock
from skills.track_face      import TrackFaceSkill
from skills.idle_behavior   import IdleBehaviorSkill
from skills.patrol          import PatrolSkill

log = logging.getLogger("main")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Spooky Robot Runtime")
    p.add_argument("--config", default="config/robot.yaml",
                   help="Path to YAML config file")
    p.add_argument("--local-config", default="config/local.yaml",
                   help="Optional local override config (gitignored)")
    p.add_argument("--debug", action="store_true",
                   help="Enable DEBUG logging")
    p.add_argument("--sim", action="store_true",
                   help="Force simulation mode (no hardware required)")
    p.add_argument("--mode", default=None,
                   help="Start in specific mode: companion_day / night_watch / etc.")
    return p.parse_args()


# ── Runtime ───────────────────────────────────────────────────────────────────

class RobotRuntime:
    """Owns all services and manages their lifecycle."""

    def __init__(self, cfg, sim: bool = False):
        self._cfg     = cfg
        self._sim     = sim
        self._active  = False

        # ── Event bus (first up, last down) ───────────────────────────────────
        self._bus = EventBus(max_queue=1024)

        # ── Mode manager ──────────────────────────────────────────────────────
        initial_mode_str = cfg.get("modes.initial", "companion_day")
        try:
            initial_mode = Mode(initial_mode_str)
        except ValueError:
            log.warning(f"Unknown initial mode '{initial_mode_str}' — using companion_day")
            initial_mode = Mode.COMPANION_DAY

        self._modes = ModeManager(self._bus, initial=initial_mode)

        # ── Safety monitor ────────────────────────────────────────────────────
        safety_cfg = cfg.get("safety", {})
        limits = SafetyLimits(
            max_speed          = safety_cfg.get("max_speed",           60),
            min_obstacle_cm    = safety_cfg.get("min_obstacle_cm",     15.0),
            pan_min            = safety_cfg.get("pan_min",             -60),
            pan_max            = safety_cfg.get("pan_max",              60),
            tilt_min           = safety_cfg.get("tilt_min",            -30),
            tilt_max           = safety_cfg.get("tilt_max",             20),
            max_cpu_temp_c     = safety_cfg.get("max_cpu_temp_c",      80.0),
            warn_cpu_temp_c    = safety_cfg.get("warn_cpu_temp_c",     70.0),
            min_ram_mb         = safety_cfg.get("min_ram_mb",          300),
            warn_ram_mb        = safety_cfg.get("warn_ram_mb",         600),
            max_actuator_errors= safety_cfg.get("max_actuator_errors",  50),
            watchdog_interval_s= safety_cfg.get("watchdog_interval_s",  2.0),
        )
        self._safety = SafetyMonitor(self._bus, limits)

        # ── Sensors ───────────────────────────────────────────────────────────
        sensor_cfg = cfg.get("sensors", {})
        self._sensors = SensorService(
            self._bus,
            poll_interval_s   = sensor_cfg.get("ultrasonic_poll_s", 0.10),
            system_interval_s = sensor_cfg.get("system_poll_s",     2.0),
        )
        # Wire sensor callbacks into safety monitor
        self._safety.register_distance_fn(self._sensors.get_distance_cm)
        self._safety.register_cpu_temp_fn(self._sensors.get_cpu_temp_c)
        self._safety.register_ram_fn(self._sensors.get_ram_free_mb)

        # ── Motor ─────────────────────────────────────────────────────────────
        self._motor = MotorService(self._safety, self._bus)

        # ── Memory ────────────────────────────────────────────────────────────
        mem_cfg = cfg.get("memory", {})
        db_path = ROOT / mem_cfg.get("db_path", "data/memory.db")
        self._memory = MemoryService(self._bus, db_path=db_path)

        # ── Audio ─────────────────────────────────────────────────────────────────
        self._audio = AudioService(self._bus, cfg)

        # ── Face database + Vision ────────────────────────────────────────────────
        face_db_path = ROOT / cfg.get("face.db_path", "data/faces/")
        self._face_db = FaceDatabase(face_db_path)
        self._vision  = VisionService(self._bus, self._safety, self._face_db, cfg)

        # ── Mind / LLM ────────────────────────────────────────────────────────────
        brain = OllamaBrain(cfg)
        self._mind = MindService(
            self._bus, self._modes, brain, self._audio, self._memory, cfg,
            vision=self._vision,
        )
        # Share the global ollama lock so vision + mind + summarizer never load two models at once
        _lock = get_ollama_lock()
        set_ollama_lock(_lock)
        set_summarizer_lock(_lock)

        # ── Learning service ──────────────────────────────────────────────────────
        self._learning = LearningService(self._bus, self._memory, cfg)

        # ── Summarizer ────────────────────────────────────────────────────────────
        self._summarizer = Summarizer(self._bus, self._memory, cfg, llm_model=brain._model)

        # ── Experiment engine ─────────────────────────────────────────────────────
        self._experiments = ExperimentEngine(
            self._bus, self._modes, self._memory, self._learning, cfg
        )

        # ── Choreography ──────────────────────────────────────────────────────────
        self._choreo = Choreography(self._motor)

        # ── Conscience (internal drives) ──────────────────────────────────────────
        self._conscience = Conscience(self._bus, self._modes)

        # ── Skills ────────────────────────────────────────────────────────────────
        self._skill_track = TrackFaceSkill(
            self._bus, self._modes, self._safety, self._motor, self._memory
        )
        self._skill_idle = IdleBehaviorSkill(
            self._bus, self._modes, self._safety, self._motor, self._choreo, self._conscience
        )
        self._skill_patrol = PatrolSkill(
            self._bus, self._modes, self._safety, self._motor, self._choreo, self._conscience
        )

        # ── Dashboard command queue ───────────────────────────────────────────────
        self._dash_cmd_q: queue.Queue = queue.Queue()

        # ── Alert service ─────────────────────────────────────────────────────
        nw_cfg = cfg.get("night_watch.fusion", {})
        fusion_cfg = FusionConfig(
            person_confidence_min = nw_cfg.get("person_confidence_min", 0.65),
            cooldown_l1           = nw_cfg.get("cooldown_l1_s",         30.0),
            cooldown_l2           = nw_cfg.get("cooldown_l2_s",         60.0),
            cooldown_l3           = nw_cfg.get("cooldown_l3_s",         300.0),
            burst_count           = nw_cfg.get("burst_count",           3),
            burst_window_s        = nw_cfg.get("burst_window_s",        20.0),
            fusion_window_s       = nw_cfg.get("fusion_window_s",       60.0),
        )
        self._alerts = AlertService(self._bus, lambda: self._modes.current, fusion_cfg)

        # Register external alert adapters (Telegram, webhook, HA…)
        for adapter in build_adapters(cfg):
            self._alerts.register_adapter(adapter)

        # ── Night Watch service ───────────────────────────────────────────────────
        self._night_watch = NightWatchService(
            self._bus, self._modes, self._motor, self._choreo,
            self._audio, self._memory, self._vision, self._safety, cfg,
        )

        # ── Dashboard ─────────────────────────────────────────────────────────────
        self._dashboard = DashboardService(
            self._bus, self._modes, self._memory, self._alerts,
            self._conscience, self._dash_cmd_q, cfg,
            night_watch  = self._night_watch,
            experiments  = self._experiments,
            summarizer   = self._summarizer,
            learning     = self._learning,
            vision       = self._vision,
            motor        = self._motor,
        )

        # ── Subscribe to lifecycle events ─────────────────────────────────────
        self._bus.subscribe(EventType.SHUTDOWN_REQUEST, self._on_shutdown_request)
        self._bus.subscribe(EventType.MODE_CHANGED,     self._on_mode_changed)
        self._bus.subscribe(EventType.HEARTBEAT,        self._on_heartbeat)
        self._bus.subscribe(EventType.LOW_MEMORY,       self._on_low_memory)

        # ── Night watch scheduler ─────────────────────────────────────────────
        self._nw_thread: threading.Thread | None = None
        self._schedule_night_watch()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        log.info("=" * 60)
        log.info(f"  {self._cfg.get('robot.name', 'Spooky')} — robot runtime starting")
        log.info("=" * 60)

        self._bus.start()
        self._safety.start()
        self._sensors.start()
        self._memory.start()
        self._audio.start()
        self._vision.start()
        self._conscience.start()
        self._mind.start()
        self._learning.apply_behavior_preferences()
        self._summarizer.start()
        self._experiments.start()
        self._night_watch.start()
        self._dashboard.start()
        # Start face tracking and idle behaviour immediately
        self._skill_track.start()
        self._skill_idle.start()
        self._active = True

        log.info(f"Mode: {self._modes.current.value}")
        log.info(f"Motor:      {self._motor}")
        log.info(f"Memory:     {self._memory}")
        log.info(f"Vision:     {self._vision}")
        log.info(f"Mind:       {self._mind}")
        log.info(f"Conscience: {self._conscience}")
        log.info("Runtime ready ✓")

    def stop(self) -> None:
        log.info("Shutting down...")
        self._active = False
        self._motor.stop()
        self._motor.look_center()
        self._skill_track.stop()
        self._skill_idle.stop()
        self._skill_patrol.stop()
        self._choreo.stop()
        self._experiments.stop()
        self._summarizer.stop()
        self._night_watch.stop()
        self._conscience.stop()
        self._mind.stop()
        self._audio.stop()
        self._vision.stop()
        self._sensors.stop()
        self._safety.stop()
        self._memory.stop()
        self._bus.stop()
        log.info("Runtime stopped.")

    def run_forever(self) -> None:
        """Block until shutdown (SIGINT/SIGTERM or SHUTDOWN_REQUEST event)."""
        self.start()
        try:
            while self._active:
                time.sleep(0.5)
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — shutting down")
        finally:
            self.stop()

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_shutdown_request(self, ev) -> None:
        reason = ev.get("reason", "requested")
        log.info(f"Shutdown requested: {reason}")
        self._active = False

    def _on_mode_changed(self, ev) -> None:
        to = ev.get("to", "")
        if to == Mode.SAFE_SHUTDOWN.value:
            log.critical("Entering SAFE_SHUTDOWN — stopping all motion")
            self._motor.stop()
            self._motor.look_center()

    def _on_heartbeat(self, ev) -> None:
        log.debug(
            f"HB dist={ev.get('distance_cm')}cm "
            f"temp={ev.get('cpu_temp_c')}°C "
            f"ram={ev.get('ram_free_mb')}MB"
        )

    def _on_low_memory(self, ev) -> None:
        ram = ev.get("ram_free_mb", 0)
        log.warning(f"LOW MEMORY: {ram} MB free — skipping non-essential inference")

    # ── Night watch scheduler ─────────────────────────────────────────────────

    def _schedule_night_watch(self) -> None:
        start_str = self._cfg.get("modes.night_watch_start")
        end_str   = self._cfg.get("modes.night_watch_end")
        if not start_str or not end_str:
            return

        def _scheduler():
            import datetime
            log.info(f"Night watch scheduler: {start_str} → {end_str}")
            while self._active:
                now = datetime.datetime.now()
                sh, sm = [int(x) for x in start_str.split(":")]
                eh, em = [int(x) for x in end_str.split(":")]
                start_t = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
                end_t   = now.replace(hour=eh, minute=em, second=0, microsecond=0)
                # Wrap past midnight
                if end_t <= start_t:
                    end_t += datetime.timedelta(days=1)

                in_window = start_t <= now <= end_t
                if in_window and self._modes.current is not Mode.NIGHT_WATCH:
                    self._modes.request_transition(Mode.NIGHT_WATCH, reason="schedule")
                elif not in_window and self._modes.current is Mode.NIGHT_WATCH:
                    self._modes.request_transition(Mode.COMPANION_DAY, reason="schedule_end")
                time.sleep(60)

        self._nw_thread = threading.Thread(
            target=_scheduler, daemon=True, name="NightWatchScheduler"
        )
        self._nw_thread.start()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # Load config
    config_paths = [ROOT / args.config]
    local = ROOT / args.local_config
    if local.exists():
        config_paths.append(local)
    cfg = load_config(*config_paths)

    # Logging
    log_level = "DEBUG" if args.debug else cfg.get("logging.level", "INFO")
    log_file  = ROOT / cfg.get("logging.file", "logs/spooky.log")
    setup_logging(level=log_level, log_file=log_file)

    # Override mode if requested via CLI
    if args.mode:
        try:
            _ = Mode(args.mode)
        except ValueError:
            print(f"Unknown mode: {args.mode}")
            sys.exit(1)

    # Signal handlers
    runtime = RobotRuntime(cfg, sim=args.sim)

    def _sig_handler(sig, frame):
        log.info(f"Signal {sig} received")
        runtime._active = False

    signal.signal(signal.SIGINT,  _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    # Override initial mode from CLI
    if args.mode:
        runtime._modes.request_transition(Mode(args.mode), reason="cli")

    runtime.run_forever()


if __name__ == "__main__":
    main()
