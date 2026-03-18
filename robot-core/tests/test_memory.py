"""Tests for MemoryService (services/memory.py)."""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.bus import EventBus
from services.memory import MemoryService


def _make_memory():
    tmp = tempfile.mkdtemp()
    bus = EventBus()
    mem = MemoryService(bus, Path(tmp) / "test.db")
    mem.start()
    return mem, bus


def test_add_and_query_episode():
    mem, bus = _make_memory()
    try:
        mem.add_episode(what="hello world", who="test_user")
        time.sleep(0.15)
        rows = mem.recent_episodes(n=5)
        assert any("hello world" in r["what"] for r in rows)
    finally:
        mem.stop()


def test_upsert_and_get_fact():
    mem, bus = _make_memory()
    try:
        mem.upsert_fact("user_name", "Marco", confidence=1.0)
        time.sleep(0.15)
        val = mem.get_fact("user_name")
        assert val == "Marco"
    finally:
        mem.stop()


def test_all_facts():
    mem, bus = _make_memory()
    try:
        mem.upsert_fact("color", "blue")
        time.sleep(0.15)
        facts = mem.all_facts()
        assert isinstance(facts, list)
        keys = [f["key"] for f in facts]
        assert "color" in keys
    finally:
        mem.stop()


def test_param_roundtrip():
    mem, bus = _make_memory()
    try:
        mem.set_param("track_face", "gain", 0.2, confidence=0.8)
        time.sleep(0.15)
        val = mem.get_param("track_face", "gain")
        assert abs(float(val) - 0.2) < 0.001
    finally:
        mem.stop()


def test_param_default_when_missing():
    mem, bus = _make_memory()
    try:
        val = mem.get_param("track_face", "nonexistent_key", default=42)
        assert val == 42
    finally:
        mem.stop()


def test_upsert_person():
    mem, bus = _make_memory()
    try:
        mem.upsert_person("marco", "Marco Rossi", familiarity_delta=0.1)
        time.sleep(0.15)
        persons = mem.all_persons()
        ids = [p["person_id"] for p in persons]
        assert "marco" in ids
    finally:
        mem.stop()


def test_get_person():
    mem, bus = _make_memory()
    try:
        mem.upsert_person("alice", "Alice", familiarity_delta=0.0)
        time.sleep(0.15)
        p = mem.get_person("alice")
        assert p is not None
        assert p["display_name"] == "Alice"
    finally:
        mem.stop()


def test_summary_is_string():
    mem, bus = _make_memory()
    try:
        s = mem.summary()
        assert isinstance(s, str)
        assert len(s) > 0
    finally:
        mem.stop()


def test_summary_includes_facts():
    mem, bus = _make_memory()
    try:
        mem.upsert_fact("user_name", "Marco", confidence=1.0)
        time.sleep(0.15)
        s = mem.summary()
        assert "Marco" in s
    finally:
        mem.stop()


if __name__ == "__main__":
    test_add_and_query_episode()
    test_upsert_and_get_fact()
    test_all_facts()
    test_param_roundtrip()
    test_param_default_when_missing()
    test_upsert_person()
    test_get_person()
    test_summary_is_string()
    test_summary_includes_facts()
    print("✅  test_memory: all passed")
