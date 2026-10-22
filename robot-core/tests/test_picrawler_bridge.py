"""Tests for the picrawler body bridge: PicrawlerMotorService and PicrawlerClient."""
import json
import math
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.bus import EventBus
from core.safety import SafetyLimits, SafetyMonitor
import services.picrawler_motor as pm
from services.picrawler_client import PicrawlerClient


# ── fakes ─────────────────────────────────────────────────────────────────────

class FakeTime:
    """Stands in for the time module inside picrawler_motor: sleeping advances the clock."""

    def __init__(self):
        self.t = 100.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


class FakeBody:
    """The body as the motor service sees it; turns its heading as commanded."""

    def __init__(self, clock, heading=0.0):
        self.clock = clock
        self.calls = []
        self._heading = heading
        self._vyaw = 0.0
        self._since = clock.t
        self.connected = True
        self.refuse = False

    def _settle(self):
        self._heading += math.degrees(self._vyaw) * (self.clock.t - self._since)
        self._since = self.clock.t

    def heading_deg(self):
        if self._heading is None:
            return None
        self._settle()
        return self._heading

    def move(self, vx=0.0, vy=0.0, vyaw=0.0):
        self.calls.append(("move", round(vyaw, 3)))
        if self._heading is not None:
            self._settle()
            self._vyaw = vyaw
        return not self.refuse

    def command(self, action, speed=0.5, duration=None):
        self.calls.append((action, round(speed, 3), None if duration is None else round(duration, 3)))
        if action == "STOP" and self._heading is not None:
            self._settle()
            self._vyaw = 0.0
        return not self.refuse

    def gesture(self, name):
        self.calls.append(("gesture", name))
        return not self.refuse

    def gestures(self):
        return {"wave": 2.75, "nod": 1.5, "shake": 1.25, "wiggle": 1.45, "bounce": 1.05,
                "look_around": 3.6, "pat_desk": 2.4, "stretch": 2.2}

    def look(self, yaw=0.0, pitch=0.0):
        self.calls.append(("look", round(yaw, 2), round(pitch, 2)))
        return not self.refuse

    def enable(self):
        self.calls.append(("enable",))
        return True

    def disable(self):
        self.calls.append(("disable",))
        return True

    def is_moving(self):
        return False

    def actions(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def clock(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(pm, "time", fake)
    return fake


def motor_with(clock, heading=0.0):
    body = FakeBody(clock, heading)
    safety = SafetyMonitor(EventBus(), SafetyLimits(max_speed=60))
    return pm.PicrawlerMotorService(safety, EventBus(), body), body, safety


# ── steps and distances ───────────────────────────────────────────────────────

def test_forward_is_one_timed_step_that_overlaps_the_next(clock):
    motor, body, _ = motor_with(clock)
    t0 = clock.t
    motor.forward(speed=50)
    assert body.calls == [("MOVE_FORWARD", 0.5, 1.1)]
    assert clock.t - t0 == pytest.approx(0.8)          # waits like do_action() did


def test_speed_goes_through_spooky_safety_first(clock):
    motor, body, _ = motor_with(clock)
    motor.turn_left(speed=100)
    assert body.calls[0][:2] == ("TURN_LEFT", 0.6)     # max_speed 60


def test_forward_suppressed_by_an_obstacle(clock):
    motor, body, safety = motor_with(clock)
    safety._obstacle_blocked = True
    motor.forward()
    motor.move_forward(distance=20)
    assert body.calls == []


def test_move_forward_converts_centimetres_to_time(clock):
    motor, body, _ = motor_with(clock)
    motor.move_forward(distance=12, speed=40)           # 0.12 m at 0.4 × 0.10 m/s
    assert body.calls == [("MOVE_FORWARD", 0.4, 3.0)]


def test_long_walks_are_capped_at_the_body_limit(clock):
    motor, body, _ = motor_with(clock)
    motor.move_backward(distance=100, speed=50)
    assert body.calls[0][2] == 5.0


# ── turning on the compass ────────────────────────────────────────────────────

def test_turn_right_closes_on_the_compass(clock):
    motor, body, _ = motor_with(clock, heading=10.0)
    motor.turn(90)
    assert body.heading_deg() == pytest.approx(10.0 - 90.0, abs=3.5)
    yaws = [c[1] for c in body.calls if c[0] == "move"]
    assert yaws and all(v < 0 for v in yaws)            # right = clockwise = negative yaw
    assert body.actions()[-1] == "STOP"


def test_turn_left_with_negative_degrees(clock):
    motor, body, _ = motor_with(clock, heading=0.0)
    motor.turn(-45)
    assert body.heading_deg() == pytest.approx(45.0, abs=3.5)


def test_turn_without_a_compass_is_timed(clock):
    motor, body, _ = motor_with(clock, heading=None)
    motor.turn(90, speed=50)
    action, speed, duration = body.calls[0]
    assert action == "TURN_RIGHT" and speed == 0.5
    assert duration == pytest.approx(math.radians(90) / (0.5 * 0.8), abs=0.01)


def test_turn_gives_up_when_the_body_refuses(clock):
    motor, body, _ = motor_with(clock, heading=0.0)
    body.refuse = True
    motor.turn(90)
    assert len([c for c in body.calls if c[0] == "move"]) == 1


# ── looking without a head ────────────────────────────────────────────────────

def test_head_looks_become_proportional_leans_and_never_turn(clock):
    motor, body, _ = motor_with(clock, heading=0.0)      # spooky's limits: pan ±60, tilt -30..20
    motor.look_at(pan=30, tilt=-15)
    assert body.calls == [("look", -9.0, -5.0)]          # 30/60 of 18° right, 15/30 of 10° down
    motor.look_at(pan=-60, tilt=20)
    assert body.calls[-1] == ("look", 18.0, 10.0)       # each side of the range on its own
    motor.look_center()
    assert body.calls[-1] == ("look", 0.0, 0.0)
    assert body.heading_deg() == pytest.approx(0.0)      # no turning at all
    assert motor.head_angles == (0, 0)


def test_a_quick_head_choreography_takes_no_steps(clock):
    motor, body, _ = motor_with(clock, heading=0.0)
    for pan in (30, -30, 30, -30, 0):                    # spooky's "excited"
        motor.look_at(pan=pan, tilt=0)
    assert set(body.actions()) == {"look"}


def test_tilt_head_tilts_the_body(clock):
    motor, body, _ = motor_with(clock)
    motor.tilt_head(20)
    assert body.calls == [("look", 0.0, 10.0)]


def test_nudge_toward_a_face_on_the_right_leans_right(clock):
    motor, body, _ = motor_with(clock)
    motor.nudge_toward(0.5, 0.0, gain=0.05)
    action, yaw, pitch = body.calls[0]
    assert action == "look" and yaw < 0


def test_nudge_beyond_the_lean_starts_turning(clock):
    motor, body, _ = motor_with(clock)
    for _ in range(20):
        motor.nudge_toward(1.0, 0.0, gain=0.05)
    assert any(c[0] == "move" and c[1] < 0 for c in body.calls)
    assert all(abs(c[1]) <= 18.0 for c in body.calls if c[0] == "look")


# ── the methods spooky's skills called but MotorService never had ─────────────

@pytest.mark.parametrize("call", [
    lambda m: m.wiggle_dance(), lambda m: m.spin_head(speed=80), lambda m: m.hop(),
    lambda m: m.pat_desk(), lambda m: m.climb(), lambda m: m.nudge(),
    lambda m: m.tilt_head(10), lambda m: m.pan_head(speed=20), lambda m: m.pan_head(0),
    lambda m: m.move_forward(distance=30, speed=100), lambda m: m.move_backward(distance=10, speed=30),
    lambda m: m.turn(45), lambda m: m.wave(), lambda m: m.shake_head(),
])
def test_every_skill_motor_call_exists(clock, call):
    motor, _, _ = motor_with(clock)
    call(motor)


@pytest.mark.parametrize("method, gesture", [
    ("wave", "wave"), ("shake_head", "shake"), ("nod", "nod"), ("wiggle_dance", "wiggle"),
    ("spin_head", "look_around"), ("pat_desk", "pat_desk"), ("hop", "bounce"), ("stretch", "stretch"),
])
def test_expressive_moves_are_body_gestures(clock, method, gesture):
    motor, body, _ = motor_with(clock)
    t0 = clock.t
    getattr(motor, method)()
    assert body.calls == [("gesture", gesture)]
    assert clock.t - t0 == pytest.approx(body.gestures()[gesture])   # waits like do_action()


def test_stand_and_sit_enable_and_disable_the_body(clock):
    motor, body, _ = motor_with(clock)
    motor.stand()
    motor.sit()
    assert body.actions() == ["enable", "disable"]


def test_scan_turns_a_full_circle(clock):
    motor, body, _ = motor_with(clock, heading=0.0)
    readings = motor.scan_environment(lambda: 42.0, n_steps=4)
    assert [r["angle"] for r in readings] == [0, 90, 180, 270]
    assert all(r["dist"] == 42.0 for r in readings)
    assert body.heading_deg() == pytest.approx(-360.0, abs=3.5)   # errors do not accumulate


# ── the HTTP client ───────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    posts: list = []
    state: dict = {}
    refuse = False

    def do_GET(self):
        self._reply(_Handler.state)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        _Handler.posts.append((self.path, body))
        if _Handler.refuse:
            self._reply({"ok": False, "mode": "active", "authority": "dashboard"})
        else:
            self._reply({"ok": True, "mode": "active"})

    def _reply(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Handler.posts, _Handler.refuse = [], False
    _Handler.state = {"mode": "active", "heading_deg": 12.5, "is_moving": True,
                      "sensors": {"distance_m": 0.42, "distance_age_s": 0.05}}
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_client_commands_carry_the_brain_source(server):
    body = PicrawlerClient(server)
    assert body.command("MOVE_FORWARD", speed=0.5, duration=1.1)
    assert body.move(vyaw=-0.3)
    assert body.enable()
    paths = [p for p, _ in _Handler.posts]
    assert paths == ["/command", "/move", "/enable"]
    assert all(b.get("source") == "brain" for _, b in _Handler.posts)
    assert _Handler.posts[0][1] == {"action": "MOVE_FORWARD", "speed": 0.5, "source": "brain", "duration": 1.1}


def test_client_reports_refusal(server):
    _Handler.refuse = True
    assert PicrawlerClient(server).command("STOP") is False


def test_client_reads_state(server):
    body = PicrawlerClient(server, poll_s=0.02)
    body.start()
    import time
    for _ in range(50):
        if body.connected:
            break
        time.sleep(0.02)
    body.stop()
    assert body.distance_cm() == pytest.approx(42.0)
    assert body.heading_deg() == 12.5 and body.mode() == "active"


def test_stale_distance_reads_as_nothing_there(server):
    _Handler.state["sensors"]["distance_age_s"] = 2.0
    body = PicrawlerClient(server, poll_s=0.02)
    body.start()
    import time
    for _ in range(50):
        if body.connected:
            break
        time.sleep(0.02)
    body.stop()
    assert body.distance_cm() == 999.0


def test_unreachable_body(server):
    body = PicrawlerClient("http://127.0.0.1:1", timeout_s=0.2)
    assert body.command("STOP") is False
    assert body.distance_cm() == 999.0 and body.heading_deg() is None and not body.connected
