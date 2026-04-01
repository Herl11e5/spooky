#!/usr/bin/env python3
"""
Quick integration test for Vector-like personality system.
Checks that all new services import and initialize correctly.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Test imports
try:
    from services.personality import PersonalityService, PersonalityTraits, Mood
    print("✓ PersonalityService imported")
    
    from services.emotion import EmotionService
    print("✓ EmotionService imported")
    
    from services.social_memory import SocialMemory
    print("✓ SocialMemory imported")
    
    from skills.play_skill import PlaySkill
    print("✓ PlaySkill imported")
    
    from skills.seek_attention_skill import SeekAttentionSkill
    print("✓ SeekAttentionSkill imported")
    
    from skills.explore_skill import ExploreSkill
    print("✓ ExploreSkill imported")
    
    from core.bus import EventBus, EventType
    print("✓ EventBus imported")
    
    # Verify new event types exist
    assert hasattr(EventType, 'PERSONALITY_MOOD_CHANGED')
    assert hasattr(EventType, 'EMOTION_EXPRESSED')
    assert hasattr(EventType, 'ATTENTION_SOUGHT')
    print("✓ New EventTypes registered")
    
    # Test PersonalityTraits initialization
    traits = PersonalityTraits(curiosity=0.8, friendliness=0.7, mischief=0.6, loyalty=0.9)
    print(f"✓ PersonalityTraits initialized: {traits}")
    
    # Test Moods enum
    mood_names = [m.value for m in Mood]
    print(f"✓ Mood enum: {mood_names}")
    
    print("\n" + "="*60)
    print("All imports and basic tests PASSED ✓")
    print("="*60)
    
except Exception as e:
    print(f"\n✗ ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
