"""The brain's parts: what the release agent compares before and after an install."""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bus import EventBus
from services.audio import AudioService
from services.health import components


def audio(tmp_path):
    return AudioService(EventBus(), {"audio": {"tts_method": "droid", "vosk_model": str(tmp_path / "none")}})


def test_a_listening_brain_reports_its_parts_working(tmp_path):
    a = audio(tmp_path)
    a._inp.model_loaded, a._inp.heard_at = True, time.time() - 0.5
    body = SimpleNamespace(connected=True, state_age_s=0.2)
    parts = components(a, body)
    assert {n for n, p in parts.items() if p["ok"]} == {"microphone", "speech", "voice", "body_link"}


def test_a_missing_model_is_said_plainly(tmp_path):
    a = audio(tmp_path)
    assert a._inp.start() is False
    h = a.health()
    assert h["speech"]["ok"] is False and h["microphone"]["ok"] is False
    assert "no Vosk model" in h["speech"]["detail"]


def test_a_microphone_gone_quiet_is_broken(tmp_path):
    a = audio(tmp_path)
    a._inp.model_loaded, a._inp.heard_at = True, time.time() - 30
    assert a.health()["microphone"]["ok"] is False


def test_an_unreachable_body_is_reported_and_the_llm_is_not_a_part(tmp_path):
    parts = components(audio(tmp_path), SimpleNamespace(connected=False, state_age_s=float("inf")))
    assert parts["body_link"] == {"ok": False, "detail": "never reached"} and "llm" not in parts


def test_a_failing_microphone_stream_is_opened_again(tmp_path, monkeypatch):
    """A USB hiccup used to end the listening thread: spooky stayed deaf until a restart."""
    import types
    from services.audio import AudioInput
    opened = []

    class Stream:
        def __init__(self, **kw):
            opened.append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            if len(opened) == 1:
                raise OSError("Unanticipated host error")
            inp._active = False                      # second stream works: one block, then stop
            return b"\0\0" * n, False

    class Rec:
        def __init__(self, *a):
            pass

        def SetWords(self, on):
            pass

        def AcceptWaveform(self, raw):
            return False

        def PartialResult(self):
            return '{"partial": ""}'

    sd = types.SimpleNamespace(RawInputStream=Stream, query_devices=lambda: [], default=None,
                               check_input_settings=lambda **kw: None,
                               _terminate=lambda: None, _initialize=lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setitem(sys.modules, "vosk", types.SimpleNamespace(
        SetLogLevel=lambda n: None, Model=lambda p: object(), KaldiRecognizer=Rec))
    monkeypatch.setattr(AudioInput, "_REOPEN_S", 0.0)
    inp = AudioInput(EventBus(), vosk_model_path=str(tmp_path))
    inp._active = True
    inp._run()
    assert len(opened) == 2 and inp.heard_at > 0


def test_a_stream_that_stops_without_an_error_is_aborted_and_reopened(tmp_path, monkeypatch):
    """The battery sagged and the microphone's read() never returned: no error, deaf for 30 min."""
    import threading
    import types
    from services.audio import AudioInput
    opened, released = [], threading.Event()

    class Stream:
        def __init__(self, **kw):
            opened.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def abort(self):
            released.set()

        def read(self, n):
            if len(opened) == 1:
                if len(getattr(self, "reads", [])) == 0:
                    self.reads = [1]
                    return b"\0\0" * n, False             # one block, then it hangs
                released.wait(10)                          # stuck — until the watchdog aborts it
                raise OSError("aborted")
            inp._active = False
            return b"\0\0" * n, False

    class Rec:
        def __init__(self, *a):
            pass

        def SetWords(self, on):
            pass

        def AcceptWaveform(self, raw):
            return False

        def PartialResult(self):
            return '{"partial": ""}'

    sd = types.SimpleNamespace(RawInputStream=Stream, query_devices=lambda: [], default=None,
                               check_input_settings=lambda **kw: None,
                               _terminate=lambda: None, _initialize=lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setitem(sys.modules, "vosk", types.SimpleNamespace(
        SetLogLevel=lambda n: None, Model=lambda p: object(), KaldiRecognizer=Rec))
    monkeypatch.setattr(AudioInput, "_REOPEN_S", 0.0)
    monkeypatch.setattr(AudioInput, "_STALL_S", 0.3)
    monkeypatch.setattr(AudioInput, "_WATCH_EVERY_S", 0.1)
    inp = AudioInput(EventBus(), vosk_model_path=str(tmp_path))
    inp._active = True
    threading.Thread(target=inp._watchdog, daemon=True).start()
    inp._run()
    assert released.is_set() and len(opened) == 2


def test_stopping_does_not_wait_forever_on_a_microphone_that_hangs(monkeypatch, tmp_path):
    """A restart while the microphone hung: the listener ends, so the room windows get written."""
    import threading
    import types
    from services.audio import AudioInput
    aborted = threading.Event()

    class Stream:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def abort(self):
            aborted.set()

        def read(self, n):
            aborted.wait(10)                               # hangs, until aborted
            raise OSError("aborted")

    class Rec:
        def __init__(self, *a):
            pass

        def SetWords(self, on):
            pass

    sd = types.SimpleNamespace(RawInputStream=Stream, query_devices=lambda: [], default=None,
                               check_input_settings=lambda **kw: None,
                               _terminate=lambda: None, _initialize=lambda: None)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setitem(sys.modules, "vosk", types.SimpleNamespace(
        SetLogLevel=lambda n: None, Model=lambda p: object(), KaldiRecognizer=Rec))
    inp = AudioInput(EventBus(), vosk_model_path=str(tmp_path))
    inp._active = True
    inp._thread = threading.Thread(target=inp._run, daemon=True)
    inp._thread.start()
    while inp._stream is None:
        time.sleep(0.01)
    inp.stop()
    assert aborted.is_set() and not inp._thread.is_alive()


def _stuck_input(monkeypatch, tmp_path, give_up_s):
    """A microphone whose read() hangs, abort or not (16:50 on 2026-10-26)."""
    import threading
    import types
    from services.audio import AudioInput
    sd = types.SimpleNamespace(query_devices=lambda: [], default=None)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setattr(AudioInput, "_STALL_S", 0.2)
    monkeypatch.setattr(AudioInput, "_GIVE_UP_S", give_up_s)
    monkeypatch.setattr(AudioInput, "_WATCH_EVERY_S", 0.05)
    asked = []
    bus = types.SimpleNamespace(publish=lambda kind, data=None, **kw: asked.append((kind, data)),
                                subscribe=lambda *a, **kw: None)
    inp = AudioInput(bus, vosk_model_path=str(tmp_path))
    inp._active = True
    inp._stream = types.SimpleNamespace(abort=lambda: None)     # aborting changes nothing
    return inp, asked, threading


def test_a_microphone_stuck_through_the_abort_restarts_the_brain(monkeypatch, tmp_path):
    from core.bus import EventType
    inp, asked, threading = _stuck_input(monkeypatch, tmp_path, give_up_s=0.5)
    inp.heard_at = time.time()                                  # it did hear, then nothing
    watch = threading.Thread(target=inp._watchdog, daemon=True)
    watch.start(); watch.join(3)
    assert asked == [(EventType.SHUTDOWN_REQUEST, {"reason": "microphone hung", "restart": True})]


def test_a_microphone_that_never_worked_does_not_restart_it_again_and_again(monkeypatch, tmp_path):
    inp, asked, threading = _stuck_input(monkeypatch, tmp_path, give_up_s=0.2)
    threading.Thread(target=inp._watchdog, daemon=True).start()
    time.sleep(0.6)
    inp._active = False
    assert asked == []


def test_a_restart_request_ends_the_brain_with_a_failure_for_systemd():
    from main import RobotRuntime
    runtime = SimpleNamespace(_active=True, exit_code=0)
    RobotRuntime._on_shutdown_request(runtime, {"reason": "microphone hung", "restart": True})
    assert runtime._active is False and runtime.exit_code == 75
    runtime = SimpleNamespace(_active=True, exit_code=0)
    RobotRuntime._on_shutdown_request(runtime, {"reason": "requested"})
    assert runtime.exit_code == 0
