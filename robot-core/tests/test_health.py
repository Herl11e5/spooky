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
