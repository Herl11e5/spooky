"""Tests for the picrawler body bridge: PicrawlerMotorService and PicrawlerClient."""
import json
import math
import sys
import threading
import time
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
    faces: dict = {}
    refuse = False

    def do_GET(self):
        self._reply(_Handler.faces if self.path == "/faces" else _Handler.state)

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


def test_client_reads_faces(server):
    _Handler.faces = {"status": "ok", "frame_size": [320, 240], "age_s": 0.1,
                      "faces": [{"id": 3, "box": [0.1, 0.2, 0.15, 0.2], "embedding": [0.0] * 128}]}
    reply = PicrawlerClient(server).faces()
    assert reply["faces"][0]["id"] == 3 and len(reply["faces"][0]["embedding"]) == 128
    assert PicrawlerClient("http://127.0.0.1:1", timeout_s=0.2).faces() is None


# ── faces from the body ───────────────────────────────────────────────────────

import numpy as np

from core.config import ConfigNode
from services.vision import EmbeddingRecognizer, FaceDatabase, VisionService


class RecordingBus:
    def __init__(self):
        self.events = []

    def publish(self, event_type, payload=None, source=""):
        self.events.append((event_type, payload or {}))

    def of(self, event_type):
        return [p for t, p in self.events if t == event_type]


def unit(seed):
    v = np.random.default_rng(seed).normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


def near(v, seed, amount=0.4):
    """Another look at the same face: mostly v, a bit of noise (cosine ≈ 0.9)."""
    w = v + amount * unit(seed) * 0.6
    return w / np.linalg.norm(w)


def snap(*faces):
    return {"status": "ok", "frame_size": [320, 240], "faces": list(faces)}


def face(track, embedding=None, box=(0.6, 0.25, 0.15, 0.2)):
    return {"id": track, "box": list(box), "bearing_deg": -8.0, "distance_m": 0.8,
            "embedding": None if embedding is None else [float(x) for x in embedding]}


@pytest.fixture
def vision(tmp_path):
    bus = RecordingBus()
    body = {"snap": snap()}
    frames = []
    v = VisionService(bus, None, FaceDatabase(tmp_path / "faces"), ConfigNode({}),
                      frame_fn=lambda: frames.append(1) or np.zeros((240, 320, 3), np.uint8),
                      faces_fn=lambda: body["snap"])
    v._camera.open()                                  # what start() does, without its threads
    return v, bus, body, frames


def test_body_faces_become_the_same_events(vision):
    v, bus, _, _ = vision
    from core.bus import EventType
    v._process_body_faces(snap(face(5)))
    detected = bus.of(EventType.PERSON_DETECTED)[0]
    assert detected["bbox"] == [192, 60, 48, 48]
    assert detected["center_x"] == pytest.approx(0.35) and detected["center_y"] == pytest.approx(-0.3)
    assert detected["track_id"] == 5 and detected["person_id"] is None
    assert bus.of(EventType.PERSON_UNKNOWN) and not bus.of(EventType.PERSON_IDENTIFIED)


def test_lost_after_the_threshold(vision):
    v, bus, _, _ = vision
    from core.bus import EventType
    v._process_body_faces(snap(face(5)))
    for _ in range(v._face_loss_threshold):
        v._process_body_faces(snap())
    assert len(bus.of(EventType.PERSON_LOST)) == 1


def test_enrolled_face_is_identified_by_name(vision, monkeypatch):
    v, bus, body, _ = vision
    from core.bus import EventType
    import services.vision as sv
    me = unit(1)
    looks = iter([near(me, i) for i in range(100, 200)])

    def next_look():
        body["snap"] = snap(face(7, next(looks)))
        return body["snap"]
    v._faces_fn = next_look
    monkeypatch.setattr(sv.time, "sleep", lambda s: None)
    assert v.enroll_from_camera("p1", "Samuele", n_frames=6, timeout_s=5)
    assert (Path(v._db._root) / "p1" / "embeddings.npy").exists()

    v._process_body_faces(snap(face(9, near(me, 7))))
    identified = bus.of(EventType.PERSON_IDENTIFIED)
    assert identified and identified[-1]["display_name"] == "Samuele"


def test_a_stranger_stays_unknown(vision):
    v, bus, _, _ = vision
    from core.bus import EventType
    v._recognizer.enroll("p1", [unit(1)], Path(v._db._root))
    v._process_body_faces(snap(face(4, unit(2))))
    assert not bus.of(EventType.PERSON_IDENTIFIED) and bus.of(EventType.PERSON_UNKNOWN)


def test_identity_sticks_to_the_track(vision):
    v, bus, _, _ = vision
    from core.bus import EventType
    me = unit(1)
    v._recognizer.enroll("p1", [me], Path(v._db._root))
    v._process_body_faces(snap(face(4, near(me, 3))))
    v._process_body_faces(snap(face(4, unit(5))))       # a blurred look while turning
    assert len(bus.of(EventType.PERSON_IDENTIFIED)) == 2
    v._process_body_faces(snap(face(8, unit(5))))       # someone new on a new track
    assert bus.of(EventType.PERSON_UNKNOWN)


def test_confidence_scale():
    assert EmbeddingRecognizer.confidence(0.363) == pytest.approx(0.55)
    assert EmbeddingRecognizer.confidence(0.5) == pytest.approx(0.80)
    assert EmbeddingRecognizer.confidence(0.2) < 0.3


def test_no_frames_are_pulled_for_faces(vision):
    v, _, _, frames = vision
    for _ in range(10):
        v._process_body_faces(snap(face(5)))
    assert frames == []
    assert v.get_frame() is not None and v.get_frame() is not None and len(frames) == 1   # cached


def test_a_face_kept_by_the_body_is_not_in_view(vision):
    v, bus, _, _ = vision
    from core.bus import EventType
    me = unit(1)
    v._recognizer.enroll("p1", [me], Path(v._db._root))
    v._process_body_faces(snap(face(4, near(me, 3))))
    stale = {**face(4, unit(5)), "age_s": 1.5}                # looked down: the body still holds the id
    for _ in range(v._face_loss_threshold):
        v._process_body_faces(snap(stale))
    assert len(bus.of(EventType.PERSON_DETECTED)) == 1 and bus.of(EventType.PERSON_LOST)
    v._process_body_faces(snap({**face(4, unit(5)), "age_s": 0.0}))   # back, same track
    assert bus.of(EventType.PERSON_IDENTIFIED)[-1]["track_id"] == 4
    assert len(bus.of(EventType.PERSON_IDENTIFIED)) == 2      # still known, though this look is poor


def test_dashboard_sees_who_each_track_is(vision):
    v, _, _, _ = vision
    me = unit(1)
    v._db.enroll_person("p1", "Samuele", [me], v._recognizer)
    v._process_body_faces(snap(face(4, near(me, 3))))
    ids = v.track_identities()
    assert ids[4]["name"] == "Samuele" and ids[4]["known"]


def test_a_forgotten_person_is_a_stranger_again(vision):
    v, bus, _, _ = vision
    from core.bus import EventType
    me = unit(1)
    v._db.enroll_person("p1", "Samuele", [me], v._recognizer)
    v.forget_person("p1")
    assert "p1" not in v._db.all_persons() and not (Path(v._db._root) / "p1").exists()
    v._process_body_faces(snap(face(4, near(me, 3))))
    assert not bus.of(EventType.PERSON_IDENTIFIED)


def test_replies_are_plain_spoken_words():
    from services.mind import plain
    assert plain("Ciao! Sono Spooky ✨🕷️ e **sono** curioso 👀") == "Ciao! Sono Spooky e sono curioso"
    assert plain("I'm fine, thanks! 😊") == "I'm fine, thanks!"
    assert plain("Caffè, perché, città — tutto a posto.") == "Caffè, perché, città — tutto a posto."


def test_droid_language_is_a_language():
    from services.droid_voice import RATE, mood, render
    assert (render("Hello there") == render("Hello there")).all()        # same words, same sounds
    assert len(render("Hi")) < len(render("Hi, how are you doing today?"))
    assert mood("Watch out, too close!") == "alert" and mood("Where are you?") == "question"
    assert mood("I'm sorry.") == "sad" and mood("Good night.") == "sleepy"
    wav = render("Good evening!")
    assert wav.dtype.name == "int16" and 0.3 * RATE < len(wav) < 3 * RATE and abs(wav).max() > 20000


def test_faces_are_learnt_as_they_are_seen(tmp_path):
    rec = EmbeddingRecognizer()
    folder = tmp_path / "p1"
    folder.mkdir()
    me = unit(1)
    rec.enroll("p1", [me], folder)
    assert rec.reinforce("p1", near(me, 2, 0.8), now=100.0)              # a new angle: learnt
    assert not rec.reinforce("p1", near(me, 3, 0.8), now=110.0)          # too soon
    assert not rec.reinforce("p1", me, now=200.0)                        # nothing new to learn
    assert len(np.load(folder / "embeddings.npy")) == 2                  # saved
    for i in range(60):
        rec.reinforce("p1", near(me, 10 + i, 0.8), now=300.0 + 31 * i)
    assert len(rec._samples["p1"]) == EmbeddingRecognizer._MAX_LOOKS
    assert rec.predict(near(me, 999, 0.8))[0] == "p1"


@pytest.mark.parametrize("heard, rest", [
    # What Vosk's Italian model actually wrote for "Spooky, …" (three voices, on the Pi)
    ("sporchi come stai", "come stai"), ("sporchi cosa vedi", "cosa vedi"),
    ("e spot mi chiamo luca", "mi chiamo luca"), ("spot parla più piano", "parla più piano"),
    ("sfoghi cosa vedi", "cosa vedi"), ("sport chi parla più piano", "parla più piano"),
    ("spot chi come stai", "come stai"), ("espo chi mi chiamo luca", "mi chiamo luca"),
    ("spock chi parla più piano", "parla più piano"), ("ehi spooky dimmi", "dimmi"),
])
def test_the_name_is_heard_by_its_sound(heard, rest):
    from services.audio import wake_match
    assert wake_match(heard) == rest


@pytest.mark.parametrize("heard", [
    "che ore sono", "sono stanco oggi", "il piatto è sporco", "metti lo spot alla radio",
    "spesso arrivo tardi", "spot", "parlami di te", "scusa puoi ripetere",
])
def test_ordinary_sentences_do_not_wake_it(heard):
    from services.audio import wake_match
    assert wake_match(heard) is None


def test_volume_scales_and_mutes(tmp_path):
    import wave
    from services.audio import AudioOutput
    out = AudioOutput(RecordingBus(), method="silent")
    out._volume_file = tmp_path / "audio.json"
    wav = tmp_path / "a.wav"

    def write():
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes((np.ones(1600) * 20000).astype(np.int16).tobytes())

    def peak():
        with wave.open(str(wav)) as r:
            return int(np.abs(np.frombuffer(r.readframes(r.getnframes()), np.int16)).max())

    out.set_volume(50); write()
    assert out._apply_volume(str(wav)) and peak() == 5000                # square curve: 50 → a quarter
    assert out.set_volume(250) == 100 and out.set_volume(-5) == 0
    assert out._apply_volume(str(wav)) is False                          # muted: nothing played
    assert AudioOutput(RecordingBus(), method="silent")._volume_file      # saved for next time
    assert '"volume": 0' in (tmp_path / "audio.json").read_text()


# ── expressions ───────────────────────────────────────────────────────────────

def expressive(track, expression, conf=0.8, emb=None):
    return {**face(track, emb), "expression": expression, "expression_confidence": conf, "expression_age_s": 0.1}


def test_an_expression_counts_once_it_lasts(vision, monkeypatch):
    v, bus, _, _ = vision
    from core.bus import EventType
    import services.vision as sv
    t = [1000.0]
    monkeypatch.setattr(sv.time, "monotonic", lambda: t[0])
    for label in ("happy", "happy", "sad", "happy"):            # flickers: nothing yet
        v._process_body_faces(snap(expressive(3, label)))
        t[0] += 0.25
    assert not bus.of(EventType.EXPRESSION_CHANGED)
    for _ in range(6):                                          # 1.5 s of the same: announced once
        v._process_body_faces(snap(expressive(3, "happy")))
        t[0] += 0.25
    changed = bus.of(EventType.EXPRESSION_CHANGED)
    assert len(changed) == 1 and changed[0]["expression"] == "happy" and changed[0]["previous"] is None
    assert v.expression_now()["expression"] == "happy"
    v._process_body_faces(snap(expressive(3, "happy", conf=0.3)))  # unsure: ignored
    assert len(bus.of(EventType.EXPRESSION_CHANGED)) == 1


class FakeModes:
    def __init__(self, mode):
        from core.modes import Mode
        self.current, self.Mode, self.transitions = mode, Mode, []

    def request_transition(self, mode, reason=""):
        self.transitions.append((mode, reason))
        self.current = mode
        return True


class FakeAudio:
    def __init__(self):
        self.said = []

    def say(self, text, wait=False, feeling=None):
        self.said.append((text, feeling))


class FakeMotor:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append(name)


@pytest.fixture
def empathy():
    from core.modes import Mode
    from skills.empathy_skill import EmpathySkill
    bus, modes, audio, motor, t = Bus(), FakeModes(Mode.COMPANION_DAY), FakeAudio(), FakeMotor(), [0.0]
    skill = EmpathySkill(bus, modes, audio, motor, clock=lambda: t[0])
    skill.start()
    return skill, bus, modes, audio, motor, t


class Bus:
    def __init__(self):
        self.handlers = {}

    def subscribe(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)

    def publish(self, event, payload=None, source=""):
        class Ev(dict):
            def get(self, k, d=None):
                return dict.get(self, k, d)
        for fn in self.handlers.get(event, []):
            fn(Ev(payload or {}))


def wait_for(cond):
    import time as _t
    for _ in range(100):
        if cond():
            return True
        _t.sleep(0.01)
    return cond()


def test_a_smile_is_shared(empathy):
    skill, bus, modes, audio, motor, t = empathy
    from core.bus import EventType
    bus.publish(EventType.EXPRESSION_CHANGED, {"expression": "happy", "track_id": 1, "name": "Samuele", "person_id": "p1"})
    assert wait_for(lambda: audio.said and "wiggle_dance" in motor.calls)
    assert audio.said[0][1] == "happy"


def test_sadness_gets_a_gentle_word_and_gentler_replies(empathy):
    skill, bus, modes, audio, motor, t = empathy
    from core.bus import EventType
    bus.publish(EventType.EXPRESSION_CHANGED, {"expression": "sad", "track_id": 1})
    assert wait_for(lambda: audio.said) and audio.said[0][1] == "sad"
    assert skill.mood_of_company == "sad"


def test_anger_gets_space(empathy):
    skill, bus, modes, audio, motor, t = empathy
    from core.bus import EventType
    bus.publish(EventType.EXPRESSION_CHANGED, {"expression": "angry", "track_id": 1})
    assert wait_for(lambda: audio.said)
    assert "stop" in motor.calls and modes.current == modes.Mode.FOCUS_ASSISTANT
    modes.current = modes.Mode.COMPANION_DAY
    t[0] += 60
    bus.publish(EventType.EXPRESSION_CHANGED, {"expression": "happy", "track_id": 1})
    import time as _t; _t.sleep(0.05)
    assert len(audio.said) == 1                                  # still giving space


def test_it_does_not_nag(empathy):
    skill, bus, modes, audio, motor, t = empathy
    from core.bus import EventType
    ev = {"expression": "surprised", "track_id": 1}
    bus.publish(EventType.EXPRESSION_CHANGED, ev)
    assert wait_for(lambda: len(audio.said) == 1)
    t[0] += 10
    bus.publish(EventType.EXPRESSION_CHANGED, ev)                # within the gap
    t[0] += 30
    bus.publish(EventType.EXPRESSION_CHANGED, ev)                # within its cooldown
    t[0] += 120
    bus.publish(EventType.EXPRESSION_CHANGED, ev)
    assert wait_for(lambda: len(audio.said) == 2)


def test_quiet_modes_get_no_reactions(empathy):
    skill, bus, modes, audio, motor, t = empathy
    from core.bus import EventType
    modes.current = modes.Mode.NIGHT_WATCH
    bus.publish(EventType.EXPRESSION_CHANGED, {"expression": "happy", "track_id": 1})
    import time as _t; _t.sleep(0.05)
    assert audio.said == []


def test_social_memory_has_its_columns_on_an_old_database(tmp_path):
    import sqlite3
    from services.memory import MemoryService
    db = tmp_path / "memory.db"
    old = sqlite3.connect(str(db))      # a database from before: no attachment columns
    old.execute("CREATE TABLE social_profiles (person_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, "
                "familiarity REAL NOT NULL DEFAULT 0.0, greeting_style TEXT NOT NULL DEFAULT 'casual', "
                "interruption_tolerance REAL NOT NULL DEFAULT 0.5, usual_hours_start INTEGER NOT NULL DEFAULT 8, "
                "usual_hours_end INTEGER NOT NULL DEFAULT 22, interaction_count INTEGER NOT NULL DEFAULT 0, "
                "last_seen REAL, notes TEXT NOT NULL DEFAULT '')")
    old.execute("INSERT INTO social_profiles(person_id, display_name) VALUES ('padrone', 'Padrone')")
    old.commit(); old.close()
    mem = MemoryService(RecordingBus(), db_path=db)
    with mem._get_db() as conn:
        row = conn.execute("SELECT display_name, attachment_level, loyalty_boost FROM social_profiles").fetchone()
    assert row == ("Padrone", 0.3, 0)


def test_it_says_when_its_battery_runs_down_and_thanks_for_the_charge():
    from services.body_watch import BodyWatch
    audio, state = FakeAudio(), {"power": "ok"}
    watch = BodyWatch(lambda: state, audio)
    watch.check()
    assert audio.said == []                                   # nothing to say at first
    state["power"] = "low"; watch.check(); watch.check()
    state["power"] = "critical"; watch.check()
    state["power"] = "ok"; watch.check()
    assert [f for _, f in audio.said] == ["sleepy", "sleepy", "happy"]


def test_its_own_voice_does_not_wake_it():
    from services.audio import AudioInput
    from core.bus import EventType
    bus = Bus()
    heard = []
    bus.subscribe(EventType.SPEECH_TRANSCRIBED, lambda ev: heard.append(ev))
    bus.subscribe(EventType.WAKE_WORD_DETECTED, lambda ev: heard.append("woke"))
    mic = AudioInput(bus)
    bus.publish(EventType.TTS_STARTED, {"text": "I am spooky"})
    mic._handle_text("spooky come stai", final=True)
    assert "woke" not in heard and heard[-1]["own"] is True
    bus.publish(EventType.TTS_FINISHED, {})
    mic._own_until = 0.0                                      # the tail has passed
    mic._handle_text("spooky come stai", final=True)
    assert "woke" in heard


def test_engagement_keeps_the_enrolled_name(tmp_path):
    import time as _t
    from services.memory import MemoryService
    from services.learning import LearningService
    mem = MemoryService(RecordingBus(), db_path=tmp_path / "m.db")
    mem.start()
    mem.upsert_person("padrone", "Padrone")
    learning = LearningService.__new__(LearningService)
    learning._memory, learning._interaction_counts, learning._engagement_score = mem, {}, {}
    for _ in range(50):
        if mem.get_person("padrone"):
            break
        _t.sleep(0.02)
    learning.record_engagement("padrone", engaged=True)
    for _ in range(50):
        p = mem.get_person("padrone")
        if p and p["interaction_count"] >= 2:
            break
        _t.sleep(0.02)
    mem.stop()
    assert mem.get_person("padrone")["display_name"] == "Padrone"


def _w(word, start, end, conf=1.0):
    return {"word": word, "start": start, "end": end, "conf": conf}


def test_the_name_in_a_partial_takes_the_words_after_it_as_the_command():
    from services.audio import WakeSpotter
    spot = WakeSpotter()
    spot.partial([_w("spooky", 12.9, 13.3)])
    assert spot.poll(13.4) is None                                  # free recognition not done yet
    spot.heard([_w("che", 12.6, 12.8), _w("sporchi", 12.9, 13.3), _w("come", 13.4, 13.6), _w("stai", 13.6, 13.9)])
    assert spot.poll(14.0) == "come stai"
    assert spot.poll(14.5) is None                                  # once only
    spot.partial([_w("spooky", 12.9, 13.3)])                        # the same segment again: no
    assert spot.poll(20.0) is None


def test_a_late_final_still_wakes_it_with_the_right_command():
    from services.audio import WakeSpotter
    spot = WakeSpotter()
    spot.heard([_w("piacere", 0.9, 1.2), _w("un", 1.2, 1.3), _w("grande", 1.4, 1.8)])
    spot.heard([_w("bianco", 4.3, 4.7)])
    spot.spotted([_w("spooky", 0.0, 10.5)])                        # final after ten seconds, end smeared
    assert spot.poll(11.0) == "piacere un grande"


def test_a_lone_name_wakes_it_with_no_command_yet():
    from services.audio import WakeSpotter
    spot = WakeSpotter()
    spot.partial([_w("spooky", 0.0, 0.4)])
    assert spot.poll(1.0) is None
    assert spot.poll(3.0) == ""                                     # nothing followed: listen


def test_an_unsure_late_spot_is_ignored():
    from services.audio import WakeSpotter
    spot = WakeSpotter()
    spot.spotted([_w("spooky", 5.0, 5.3, conf=0.6), _w("[unk]", 5.3, 6.0)])
    assert spot.poll(10.0) is None


def test_a_command_ends_after_silence_without_more_speech():
    from services.audio import AudioInput
    from core.bus import EventType
    bus = Bus()
    commands = []
    bus.subscribe(EventType.COMMAND_PARSED, lambda ev: commands.append(ev["command"]))
    mic = AudioInput(bus, command_timeout_s=1.5)
    mic._wake("come stai", "(spotted) come stai")
    assert commands == []
    mic._last_speech_ts -= 2.0                                      # 2 s of silence
    if mic._state == mic._STATE_ACTIVE and time.time() - mic._last_speech_ts >= mic._command_timeout:
        mic._emit_command()
    assert commands == ["come stai"]
