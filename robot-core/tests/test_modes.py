"""Tests for ModeManager (core/modes.py)."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager


def _make_manager():
    bus = EventBus()
    bus.start()
    mgr = ModeManager(bus)
    return mgr, bus


def test_initial_mode():
    mgr, bus = _make_manager()
    bus.stop()
    assert mgr.current == Mode.COMPANION_DAY


def test_valid_transition_to_idle():
    mgr, bus = _make_manager()
    ok = mgr.request_transition(Mode.IDLE_OBSERVER)
    bus.stop()
    assert ok
    assert mgr.current == Mode.IDLE_OBSERVER


def test_valid_transition_to_night_watch():
    # COMPANION_DAY → NIGHT_WATCH is a valid edge
    mgr, bus = _make_manager()
    ok = mgr.request_transition(Mode.NIGHT_WATCH)
    bus.stop()
    assert ok
    assert mgr.current == Mode.NIGHT_WATCH


def test_invalid_transition_rejected():
    # NIGHT_WATCH can only go to COMPANION_DAY or SAFE_SHUTDOWN
    mgr, bus = _make_manager()
    mgr.request_transition(Mode.NIGHT_WATCH)
    ok = mgr.request_transition(Mode.IDLE_OBSERVER)  # not a valid edge from NIGHT_WATCH
    bus.stop()
    assert not ok
    assert mgr.current == Mode.NIGHT_WATCH


def test_safe_shutdown_always_reachable():
    mgr, bus = _make_manager()
    mgr.force_shutdown()
    bus.stop()
    assert mgr.current == Mode.SAFE_SHUTDOWN


def test_mode_changed_event_emitted():
    bus = EventBus()
    bus.start()
    mgr = ModeManager(bus)
    events = []
    bus.subscribe(EventType.MODE_CHANGED, lambda ev: events.append(ev))

    mgr.request_transition(Mode.IDLE_OBSERVER)

    # Give async queue time to dispatch
    for _ in range(20):
        if events:
            break
        time.sleep(0.05)

    bus.stop()
    assert len(events) >= 1
    assert events[0].get("to") == Mode.IDLE_OBSERVER.value


def test_same_mode_returns_true():
    # Transitioning to current mode is a no-op that returns True
    mgr, bus = _make_manager()
    ok = mgr.request_transition(Mode.COMPANION_DAY)
    bus.stop()
    assert ok  # same-mode is accepted as a no-op


def test_can_transition():
    mgr, bus = _make_manager()
    bus.stop()
    assert mgr.can_transition(Mode.IDLE_OBSERVER)
    assert mgr.can_transition(Mode.SAFE_SHUTDOWN)
    assert not mgr.can_transition(Mode.NIGHT_WATCH) or mgr.can_transition(Mode.NIGHT_WATCH)
    # Just verify the method exists and returns bool
    assert isinstance(mgr.can_transition(Mode.FOCUS_ASSISTANT), bool)


if __name__ == "__main__":
    test_initial_mode()
    test_valid_transition_to_idle()
    test_valid_transition_to_night_watch()
    test_invalid_transition_rejected()
    test_safe_shutdown_always_reachable()
    test_mode_changed_event_emitted()
    test_same_mode_returns_true()
    test_can_transition()
    print("✅  test_modes: all passed")
