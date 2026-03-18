"""
services/experiment.py — Micro-experiment engine.

Allows the robot to safely vary one behavior parameter at a time,
measure the outcome, and either adopt or roll back the change.

Each experiment has:
  hypothesis    — what we expect to improve
  parameter     — (skill, key) to vary
  baseline      — current value (auto-read)
  test_value    — alternative value to try
  metric_fn     — callable() → float, measured before and after
  min_samples   — minimum observations before evaluating
  max_duration  — maximum wall-clock time before forced evaluation
  improve_threshold — how much better the metric must be to adopt

Built-in experiments (auto-proposed based on interaction history):
  1. response_verbosity   — shorter vs longer LLM responses
  2. greeting_enthusiasm  — formal vs playful greeting style
  3. tracking_sensitivity — faster vs slower face tracking
  4. thought_frequency    — more vs fewer autonomous thoughts

Only one experiment runs at a time. Results are stored in procedural memory
and episodic memory, and published as EXPERIMENT_STARTED / EXPERIMENT_FINISHED.

Never allowed:
  - Modifying safety parameters
  - Running experiments in night_watch or safe_shutdown mode
  - Disabling logging or safety checks
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager
from services.memory import MemoryService
from services.learning import LearningService, _PARAM_BOUNDS

log = logging.getLogger(__name__)


# ── Experiment status ─────────────────────────────────────────────────────────

class ExperimentStatus(str, Enum):
    PENDING    = "pending"
    BASELINE   = "baseline"    # measuring baseline metric
    RUNNING    = "running"     # test value active, measuring
    EVALUATING = "evaluating"
    ADOPTED    = "adopted"
    ROLLED_BACK= "rolled_back"
    CANCELLED  = "cancelled"


# ── Experiment definition ─────────────────────────────────────────────────────

@dataclass
class Experiment:
    id:                  str
    hypothesis:          str
    skill:               str
    param_key:           str
    test_value:          Any
    metric_name:         str
    min_samples:         int   = 10
    max_duration_s:      float = 3600.0    # 1 hour
    improve_threshold:   float = 0.05      # must improve by 5%

    # Runtime state (set by ExperimentEngine)
    status:              ExperimentStatus = ExperimentStatus.PENDING
    baseline_value:      Any              = None
    baseline_metric:     float            = 0.0
    test_metric:         float            = 0.0
    samples_collected:   int              = 0
    start_ts:            float            = 0.0
    end_ts:              Optional[float]  = None
    outcome:             str              = ""

    def elapsed(self) -> float:
        return time.time() - self.start_ts if self.start_ts else 0.0

    def is_timed_out(self) -> bool:
        return self.elapsed() > self.max_duration_s

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id":               self.id,
            "hypothesis":       self.hypothesis,
            "skill":            self.skill,
            "param":            self.param_key,
            "baseline_value":   self.baseline_value,
            "test_value":       self.test_value,
            "baseline_metric":  round(self.baseline_metric, 4),
            "test_metric":      round(self.test_metric, 4),
            "samples":          self.samples_collected,
            "status":           self.status.value,
            "outcome":          self.outcome,
            "elapsed_s":        round(self.elapsed()),
        }


# ── Built-in experiment catalog ───────────────────────────────────────────────
#
# Experiments are proposed automatically. Each entry is a factory lambda
# that receives the current parameter value and returns an Experiment.

def _catalog() -> List[Callable[[Any], Experiment]]:
    return [
        lambda cur: Experiment(
            id          = "response_verbosity",
            hypothesis  = "Risposte più brevi migliorano l'engagement",
            skill       = "mind",
            param_key   = "max_tokens",
            test_value  = max(60, int((cur or 180) * 0.55)),
            metric_name = "commands_per_hour",
            min_samples = 8,
            max_duration_s = 1800,
        ),
        lambda cur: Experiment(
            id          = "tracking_sensitivity",
            hypothesis  = "Tracking più lento è meno fastidioso",
            skill       = "track_face",
            param_key   = "gain",
            test_value  = round(max(0.04, (cur or 0.12) * 0.7), 3),
            metric_name = "face_presence_pct",
            min_samples = 12,
            max_duration_s = 2400,
        ),
        lambda cur: Experiment(
            id          = "tracking_sensitivity_up",
            hypothesis  = "Tracking più veloce mantiene la faccia centrata meglio",
            skill       = "track_face",
            param_key   = "gain",
            test_value  = round(min(0.40, (cur or 0.12) * 1.4), 3),
            metric_name = "face_presence_pct",
            min_samples = 12,
            max_duration_s = 2400,
        ),
        lambda cur: Experiment(
            id          = "llm_temperature_creative",
            hypothesis  = "Temperatura LLM più alta produce risposte più interessanti",
            skill       = "mind",
            param_key   = "temperature",
            test_value  = round(min(1.0, (cur or 0.75) + 0.15), 2),
            metric_name = "commands_per_hour",
            min_samples = 10,
            max_duration_s = 2700,
        ),
    ]


# ── ExperimentEngine ──────────────────────────────────────────────────────────

class ExperimentEngine:
    """
    Manages the lifecycle of one micro-experiment at a time.

    Automatically proposes experiments when:
      - The robot has been active for > MIN_SESSION_MINUTES
      - No experiment has run in the last COOLDOWN_S seconds
      - Current mode is companion_day or idle_observer

    Metric collection:
      - commands_per_hour: tracked via COMMAND_PARSED events
      - face_presence_pct: tracked via PERSON_DETECTED events
    """

    MIN_SESSION_MINUTES = 15
    COOLDOWN_S          = 7200   # 2 hours between experiments
    EVAL_TICK_S         = 60     # check experiment status every minute

    def __init__(
        self,
        bus: EventBus,
        mode_manager: ModeManager,
        memory: MemoryService,
        learning: LearningService,
        cfg,
    ):
        self._bus      = bus
        self._mm       = mode_manager
        self._memory   = memory
        self._learning = learning
        self._cfg      = cfg

        self._current:     Optional[Experiment] = None
        self._history:     List[Experiment]     = []
        self._last_exp_ts: float = 0.0
        self._active       = False
        self._lock         = threading.Lock()

        # Metric counters (reset at baseline/test phase start)
        self._metric_window_commands: int   = 0
        self._metric_window_faces:    int   = 0
        self._metric_window_start:    float = time.time()

        bus.subscribe(EventType.COMMAND_PARSED,   self._on_command)
        bus.subscribe(EventType.PERSON_DETECTED,  self._on_person_detected)
        bus.subscribe(EventType.MODE_CHANGED,     self._on_mode_changed)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._active = True
        t = threading.Thread(target=self._eval_loop, daemon=True, name="Experiment")
        t.start()
        log.info("ExperimentEngine started")

    def stop(self) -> None:
        self._active = False
        if self._current and self._current.status == ExperimentStatus.RUNNING:
            self._rollback(self._current, reason="shutdown")
        log.info("ExperimentEngine stopped")

    # ── manual control ────────────────────────────────────────────────────────

    def propose_experiment(self, experiment_id: Optional[str] = None) -> Optional[str]:
        """
        Manually trigger an experiment (called from dashboard or MindService).
        Returns experiment id if started, None if busy or no candidate.
        """
        with self._lock:
            if self._current and self._current.status in (
                ExperimentStatus.BASELINE, ExperimentStatus.RUNNING
            ):
                return None
        return self._try_start_experiment(force=True, specific_id=experiment_id)

    def cancel_current(self) -> bool:
        with self._lock:
            exp = self._current
        if exp is None:
            return False
        self._rollback(exp, reason="manual cancel")
        return True

    @property
    def current(self) -> Optional[Experiment]:
        return self._current

    def history(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._history]

    # ── evaluation loop ───────────────────────────────────────────────────────

    def _eval_loop(self) -> None:
        session_start = time.time()
        while self._active:
            time.sleep(self.EVAL_TICK_S)
            if not self._active:
                break

            # Only experiment in day modes
            if not self._mm.is_day_mode():
                continue

            with self._lock:
                exp = self._current

            if exp is None:
                session_min = (time.time() - session_start) / 60
                since_last  = time.time() - self._last_exp_ts
                if session_min >= self.MIN_SESSION_MINUTES and since_last >= self.COOLDOWN_S:
                    self._try_start_experiment()
            else:
                self._tick_experiment(exp)

    def _tick_experiment(self, exp: Experiment) -> None:
        if exp.status == ExperimentStatus.BASELINE:
            if exp.samples_collected >= exp.min_samples // 2 or exp.elapsed() > 900:
                # Baseline measurement done — switch to test value
                exp.baseline_metric = self._read_metric(exp.metric_name)
                self._reset_metric_window()
                log.info(
                    f"Experiment '{exp.id}': baseline {exp.metric_name}={exp.baseline_metric:.3f}, "
                    f"activating test_value={exp.test_value}"
                )
                self._learning.tune_parameter(exp.skill, exp.param_key, exp.test_value)
                exp.status    = ExperimentStatus.RUNNING
                exp.start_ts  = time.time()
                self._bus.publish(EventType.EXPERIMENT_STARTED,
                                  {"id": exp.id, "hypothesis": exp.hypothesis},
                                  source="ExperimentEngine")

        elif exp.status == ExperimentStatus.RUNNING:
            if exp.samples_collected >= exp.min_samples or exp.is_timed_out():
                self._evaluate(exp)

    def _try_start_experiment(
        self, force: bool = False, specific_id: Optional[str] = None
    ) -> Optional[str]:
        catalog = _catalog()
        random.shuffle(catalog)

        # Filter already-run experiments (avoid repeating within history)
        run_ids = {e.id for e in self._history}

        for factory in catalog:
            # Get current param value to compute test_value
            dummy = factory(None)
            if specific_id and dummy.id != specific_id:
                continue
            if not force and dummy.id in run_ids:
                continue
            bounds = _PARAM_BOUNDS.get((dummy.skill, dummy.param_key))
            if bounds is None:
                continue
            current_val = self._memory.get_param(dummy.skill, dummy.param_key)
            exp = factory(current_val)
            exp.baseline_value = current_val
            exp.status    = ExperimentStatus.BASELINE
            exp.start_ts  = time.time()
            exp.samples_collected = 0
            self._reset_metric_window()

            with self._lock:
                self._current = exp

            log.info(
                f"Experiment '{exp.id}' STARTED: '{exp.hypothesis}' "
                f"| baseline={exp.baseline_value} → test={exp.test_value}"
            )
            return exp.id

        log.debug("ExperimentEngine: no suitable experiment to propose")
        return None

    def _evaluate(self, exp: Experiment) -> None:
        exp.status      = ExperimentStatus.EVALUATING
        exp.test_metric = self._read_metric(exp.metric_name)
        exp.end_ts      = time.time()

        delta = exp.test_metric - exp.baseline_metric
        relative = (delta / exp.baseline_metric) if exp.baseline_metric > 0 else 0.0

        if relative >= exp.improve_threshold:
            self._adopt(exp, delta=relative)
        else:
            self._rollback(exp, reason=f"no improvement (Δ={relative:+.1%})")

    def _adopt(self, exp: Experiment, delta: float) -> None:
        exp.status  = ExperimentStatus.ADOPTED
        exp.outcome = f"adopted (Δ={delta:+.1%})"
        # Parameter already set to test_value — store with higher confidence
        self._learning.tune_parameter(exp.skill, exp.param_key, exp.test_value, confidence=0.85)
        log.info(f"Experiment '{exp.id}' ADOPTED: {exp.outcome}")
        self._finish(exp)

    def _rollback(self, exp: Experiment, reason: str) -> None:
        exp.status  = ExperimentStatus.ROLLED_BACK if reason != "manual cancel" \
                      else ExperimentStatus.CANCELLED
        exp.outcome = reason
        if exp.baseline_value is not None:
            self._learning.tune_parameter(
                exp.skill, exp.param_key, exp.baseline_value, confidence=0.7
            )
        log.info(f"Experiment '{exp.id}' ROLLED BACK: {reason}")
        self._finish(exp)

    def _finish(self, exp: Experiment) -> None:
        self._last_exp_ts = time.time()
        self._history.append(exp)
        with self._lock:
            self._current = None
        self._memory.add_episode(
            what=f"Esperimento '{exp.id}': {exp.outcome}",
            action="experiment",
            outcome=exp.status.value,
            confidence=0.9,
        )
        self._bus.publish(
            EventType.EXPERIMENT_FINISHED,
            exp.to_dict(),
            source="ExperimentEngine",
        )

    # ── metric tracking ───────────────────────────────────────────────────────

    def _read_metric(self, metric_name: str) -> float:
        elapsed = max(1.0, time.time() - self._metric_window_start)
        if metric_name == "commands_per_hour":
            return self._metric_window_commands / elapsed * 3600
        elif metric_name == "face_presence_pct":
            return self._metric_window_faces / max(1, self._metric_window_commands + 1)
        return 0.0

    def _reset_metric_window(self) -> None:
        self._metric_window_commands = 0
        self._metric_window_faces    = 0
        self._metric_window_start    = time.time()

    def _on_command(self, ev) -> None:
        self._metric_window_commands += 1
        if self._current:
            self._current.samples_collected += 1

    def _on_person_detected(self, ev) -> None:
        self._metric_window_faces += 1

    def _on_mode_changed(self, ev) -> None:
        to = ev.get("to", "")
        if to in (Mode.NIGHT_WATCH.value, Mode.SAFE_SHUTDOWN.value):
            exp = self._current
            if exp and exp.status in (ExperimentStatus.BASELINE, ExperimentStatus.RUNNING):
                self._rollback(exp, reason=f"mode changed to {to}")

    def __repr__(self) -> str:
        cur = self._current.id if self._current else "none"
        return f"<ExperimentEngine current={cur} history={len(self._history)}>"
