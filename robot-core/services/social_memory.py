"""
services/social_memory.py — Track relationships with recognized people.

Extends MemoryService's social_profiles table with dynamic relationship tracking.

Per-person tracking:
  - familiarity_level   (0.0–1.0): how well Spooky knows this person
  - attachment_level    (0.0–1.0): emotional bond / loyalty score
  - favorite_greeting   (str):      personalized greeting
  - interaction_count   (int):      total #interactions
  - last_seen          (float):     timestamp
  - mood_around_person (str):       what mood person brings
  - loyalty_boost      (bool):      if True, treat as "favorite"

Interactions:
  - Positive interaction (person laughs, responds well) → +attachment
  - Negative interaction (person dismisses) → -attachment
  - Time decay: attachment slowly decreases if person not seen
  - Personality modulation: high "loyalty" trait amplifies attachment growth
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Dict, List, Optional

from core.bus import EventBus, EventType

log = logging.getLogger(__name__)


class SocialMemory:
    """
    Thread-safe management of Spooky's relationships with recognized people.

    Integrated with MemoryService but focused on emotional/social tracking.
    """

    _ATTACHMENT_GROWTH_POSITIVE = 0.15  # per positive interaction
    _ATTACHMENT_DECAY = 0.001  # per second (slow, loyalty persists)
    _DECAY_THRESHOLD_DAYS = 30  # if not seen in 30 days, attachment halves

    def __init__(self, bus: EventBus, memory_service: object):
        self._bus = bus
        self._memory = memory_service  # Reference to MemoryService
        self._lock = threading.RLock()
        self._db_path = memory_service._db_path if hasattr(memory_service, "_db_path") else None
        self._active = False
        self._thread: Optional[threading.Thread] = None

        bus.subscribe(EventType.PERSON_IDENTIFIED, self._on_person_identified)
        bus.subscribe(EventType.COMMAND_PARSED, self._on_interaction)
        bus.subscribe(EventType.ALERT_RAISED, self._on_negative_interaction)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._active = True
        self._thread = threading.Thread(
            target=self._attachment_decay_loop, daemon=True, name="SocialMemory"
        )
        self._thread.start()
        log.info("SocialMemory started")

    def stop(self) -> None:
        self._active = False
        if self._thread:
            self._thread.join(timeout=5.0)
        log.info("SocialMemory stopped")

    # ── public API ────────────────────────────────────────────────────────────

    def get_person_profile(self, person_id: str) -> Dict:
        """Retrieve full social profile for a person."""
        try:
            with self._get_db() as conn:
                c = conn.cursor()
                c.execute(
                    """SELECT person_id, display_name, familiarity, greeting_style,
                       attachment_level, interaction_count, last_seen, loyalty_boost
                       FROM social_profiles WHERE person_id = ?""",
                    (person_id,),
                )
                row = c.fetchone()
                if row:
                    return {
                        "person_id": row[0],
                        "display_name": row[1],
                        "familiarity": row[2],
                        "greeting_style": row[3],
                        "attachment_level": row[4],
                        "interaction_count": row[5],
                        "last_seen": row[6],
                        "loyalty_boost": row[7],
                    }
        except Exception as e:
            log.error(f"Error fetching person profile: {e}")
        return None

    def record_positive_interaction(self, person_id: str, interaction_type: str = "chat") -> None:
        """Log a positive interaction, increase attachment."""
        with self._lock:
            try:
                with self._get_db() as conn:
                    c = conn.cursor()
                    c.execute(
                        """UPDATE social_profiles
                           SET interaction_count = interaction_count + 1,
                               last_seen = ?,
                               attachment_level = MIN(1.0, attachment_level + ?)
                           WHERE person_id = ?""",
                        (time.time(), self._ATTACHMENT_GROWTH_POSITIVE, person_id),
                    )
                    conn.commit()
                    log.info(f"Recorded positive interaction with {person_id}")
            except Exception as e:
                log.error(f"Error recording positive interaction: {e}")

    def record_negative_interaction(self, person_id: str) -> None:
        """Log negative interaction, decrease attachment."""
        with self._lock:
            try:
                with self._get_db() as conn:
                    c = conn.cursor()
                    c.execute(
                        """UPDATE social_profiles
                           SET attachment_level = MAX(0.0, attachment_level - 0.05),
                               last_seen = ?
                           WHERE person_id = ?""",
                        (time.time(), person_id),
                    )
                    conn.commit()
                    log.info(f"Recorded negative interaction with {person_id}")
            except Exception as e:
                log.error(f"Error recording negative interaction: {e}")

    def set_loyalty_boost(self, person_id: str, is_favorite: bool = True) -> None:
        """Mark person as favorite (loyalty_boost toggle)."""
        with self._lock:
            try:
                with self._get_db() as conn:
                    c = conn.cursor()
                    c.execute(
                        """UPDATE social_profiles SET loyalty_boost = ? WHERE person_id = ?""",
                        (is_favorite, person_id),
                    )
                    conn.commit()
                    status = "favorite" if is_favorite else "regular"
                    log.info(f"Set {person_id} as {status}")
            except Exception as e:
                log.error(f"Error setting loyalty boost: {e}")

    def get_most_attached_person(self) -> Optional[Dict]:
        """Return the person with highest attachment_level."""
        try:
            with self._get_db() as conn:
                c = conn.cursor()
                c.execute(
                    """SELECT person_id, display_name, attachment_level
                       FROM social_profiles
                       ORDER BY attachment_level DESC
                       LIMIT 1""",
                )
                row = c.fetchone()
                if row:
                    return {
                        "person_id": row[0],
                        "display_name": row[1],
                        "attachment_level": row[2],
                    }
        except Exception as e:
            log.error(f"Error getting most attached person: {e}")
        return None

    def list_all_known_people(self) -> List[Dict]:
        """Return all known people sorted by attachment."""
        try:
            with self._get_db() as conn:
                c = conn.cursor()
                c.execute(
                    """SELECT person_id, display_name, attachment_level, last_seen
                       FROM social_profiles
                       ORDER BY attachment_level DESC""",
                )
                return [
                    {
                        "person_id": row[0],
                        "display_name": row[1],
                        "attachment_level": row[2],
                        "last_seen": row[3],
                    }
                    for row in c.fetchall()
                ]
        except Exception as e:
            log.error(f"Error listing people: {e}")
        return []

    def get_greeting_for_person(self, person_id: str) -> str:
        """Get personalized greeting for this person."""
        profile = self.get_person_profile(person_id)
        if not profile:
            return "Ciao!"

        greeting_map = {
            "enthusiastic": "Ehi! Che gioia di vederti!",
            "casual": "Ciao! Come stai?",
            "warm": "Ben tornato! Mi sei mancato.",
            "formal": "Salve.",
        }
        style = profile.get("greeting_style", "casual")
        return greeting_map.get(style, "Ciao!")

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_person_identified(self, event_data: Dict) -> None:
        """Someone was recognized. Update their profile."""
        person_id = event_data.get("person_id")
        person_name = event_data.get("display_name", "Unknown")
        confidence = event_data.get("confidence", 0.5)

        if person_id:
            profile = self.get_person_profile(person_id)
            if profile:
                # Already known — update last_seen + increase attachment slightly
                self.record_positive_interaction(person_id, "recognition")
            else:
                # New person — create profile
                self._create_person_profile(person_id, person_name)

    def _on_interaction(self, event_data: Dict) -> None:
        """Person issued a command (positive interaction)."""
        person_id = event_data.get("person_id")
        if person_id:
            self.record_positive_interaction(person_id, "command")

    def _on_negative_interaction(self, event_data: Dict) -> None:
        """Alert raised (e.g., obstacle, person dismissed). Mark as negative."""
        person_id = event_data.get("person_id")
        if person_id:
            self.record_negative_interaction(person_id)

    # ── attachment decay loop ─────────────────────────────────────────────────

    def _attachment_decay_loop(self) -> None:
        """Background thread that slowly decreases attachment over time."""
        while self._active:
            try:
                time.sleep(60)  # Check every minute

                with self._lock:
                    try:
                        with self._get_db() as conn:
                            c = conn.cursor()

                            # Decay attachment for everyone
                            c.execute(
                                """UPDATE social_profiles
                                   SET attachment_level = MAX(0.0, attachment_level - ?)""",
                                (self._ATTACHMENT_DECAY * 60,),  # 60 seconds passed
                            )

                            # Halve attachment if not seen in 30 days
                            thirty_days_ago = time.time() - (30 * 86400)
                            c.execute(
                                """UPDATE social_profiles
                                   SET attachment_level = attachment_level * 0.5
                                   WHERE last_seen < ? AND attachment_level > 0.1""",
                                (thirty_days_ago,),
                            )

                            conn.commit()
                    except Exception as e:
                        log.error(f"Error in attachment decay: {e}")
            except Exception as e:
                log.error(f"Attachment decay loop error: {e}")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _create_person_profile(self, person_id: str, display_name: str) -> None:
        """Create new person profile in social_profiles table."""
        try:
            with self._get_db() as conn:
                c = conn.cursor()
                c.execute(
                    """INSERT INTO social_profiles
                       (person_id, display_name, familiarity, greeting_style,
                        attachment_level, interaction_count, last_seen, loyalty_boost)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        person_id,
                        display_name,
                        0.2,  # initial familiarity
                        "casual",  # default greeting
                        0.3,  # initial attachment
                        1,  # first interaction
                        time.time(),
                        False,  # not yet favorite
                    ),
                )
                conn.commit()
                log.info(f"Created profile for {display_name}")
        except sqlite3.IntegrityError:
            # Profile already exists
            pass
        except Exception as e:
            log.error(f"Error creating person profile: {e}")

    def _get_db(self) -> sqlite3.Connection:
        """Get database connection from MemoryService."""
        if hasattr(self._memory, "_get_db"):
            return self._memory._get_db()
        raise RuntimeError("MemoryService not properly initialized")
