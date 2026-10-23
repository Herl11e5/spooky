"""Night watch with the PiCrawler body: it sits and watches; it does not lean around all night."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bus import EventBus
from core.modes import ModeManager
from services.night_watch import NightWatchService


class Recorder:
    def __init__(self, sits=True):
        self.calls, self._sits = [], sits

    def __getattr__(self, name):
        def call(*a, **k):
            self.calls.append(name)
            return self._sits if name == "sit" else True
        return call


class Memory:
    def add_episode(self, **k):
        pass

    def upsert_fact(self, *a, **k):
        pass


def watch(tmp_path, seated, sits=True):
    motor, choreo = Recorder(sits), Recorder()
    cfg = {"night_watch.snapshot_dir": str(tmp_path / "snap")}
    nw = NightWatchService(EventBus(), ModeManager(EventBus()), motor, choreo, Recorder(), Memory(),
                           None, type("S", (), {"is_obstacle_blocked": False})(), cfg, seated_body=seated)
    return nw, motor, choreo


def test_with_a_body_it_sits_down_and_does_not_patrol(tmp_path):
    nw, motor, choreo = watch(tmp_path, seated=True)
    nw._active_nw = True
    nw._enter_night_watch()
    assert motor.calls == ["sit"] and choreo.calls == []
    assert nw._patrol_thread is None                    # no leaning around every ten minutes
    nw._exit_night_watch()
    assert motor.calls == ["sit"] and choreo.calls == []   # morning does not stand it up


def test_an_alert_is_logged_without_moving_the_body(tmp_path):
    nw, motor, choreo = watch(tmp_path, seated=True)
    nw._active_nw = True
    nw._respond(2, "unknown_person+event", {})
    assert choreo.calls == [] and motor.calls == []
    assert nw.night_log()[0]["level"] == 2
    assert list((tmp_path / "snap").rglob("*.json"))     # the event's metadata is kept


def test_without_a_body_the_head_robot_still_looks_around(tmp_path):
    nw, motor, choreo = watch(tmp_path, seated=False)
    nw._active_nw = True
    nw._respond(1, "motion_detected", {})
    assert "play" in choreo.calls
    nw._stop_patrol()
