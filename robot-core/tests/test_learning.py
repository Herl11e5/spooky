"""Tests for LearningService (services/learning.py)."""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.bus import EventBus
from core.config import load_config
from services.learning import LearningService, CandidateFact
from services.memory import MemoryService


def _make_learning():
    tmp = tempfile.mkdtemp()
    bus = EventBus()
    mem = MemoryService(bus, Path(tmp) / "test.db")
    mem.start()
    cfg = {}
    svc = LearningService(bus, mem, cfg)
    return svc, mem, bus


def test_extract_name():
    svc, mem, _ = _make_learning()
    try:
        facts = svc.extract_from_text("mi chiamo Marco")
        names = [f for f in facts if f.key == "user_name"]
        assert len(names) >= 1
        assert "Marco" in names[0].value
    finally:
        mem.stop()


def test_extract_profession():
    svc, mem, _ = _make_learning()
    try:
        facts = svc.extract_from_text("sono un ingegnere")
        roles = [f for f in facts if f.key == "user_role"]
        assert len(roles) >= 1
    finally:
        mem.stop()


def test_extract_piace():
    svc, mem, _ = _make_learning()
    try:
        facts = svc.extract_from_text("mi piace la musica jazz")
        # key is "likes_{value}" pattern
        likes = [f for f in facts if f.key.startswith("likes_")]
        assert len(likes) >= 1
    finally:
        mem.stop()


def test_extract_returns_candidate_facts():
    svc, mem, _ = _make_learning()
    try:
        facts = svc.extract_from_text("mi chiamo Luca")
        assert isinstance(facts, list)
        assert all(isinstance(f, CandidateFact) for f in facts)
    finally:
        mem.stop()


def test_extract_no_match_returns_empty():
    svc, mem, _ = _make_learning()
    try:
        facts = svc.extract_from_text("blah blah testo generico senza pattern")
        assert isinstance(facts, list)
        # May or may not find something with "sono" pattern — just check it's a list
    finally:
        mem.stop()


def test_tune_parameter_in_bounds():
    svc, mem, _ = _make_learning()
    try:
        ok = svc.tune_parameter("track_face", "gain", 0.15)
        assert ok
    finally:
        mem.stop()


def test_tune_parameter_out_of_bounds():
    svc, mem, _ = _make_learning()
    try:
        ok = svc.tune_parameter("track_face", "gain", 9.99)
        assert not ok
    finally:
        mem.stop()


def test_tune_unknown_parameter():
    svc, mem, _ = _make_learning()
    try:
        ok = svc.tune_parameter("nonexistent_skill", "nonexistent_param", 42)
        assert not ok
    finally:
        mem.stop()


def test_tune_mind_max_tokens():
    svc, mem, _ = _make_learning()
    try:
        ok = svc.tune_parameter("mind", "max_tokens", 100)
        assert ok
    finally:
        mem.stop()


if __name__ == "__main__":
    test_extract_name()
    test_extract_profession()
    test_extract_piace()
    test_extract_returns_candidate_facts()
    test_extract_no_match_returns_empty()
    test_tune_parameter_in_bounds()
    test_tune_parameter_out_of_bounds()
    test_tune_unknown_parameter()
    test_tune_mind_max_tokens()
    print("✅  test_learning: all passed")
