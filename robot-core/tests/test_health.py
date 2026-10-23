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
