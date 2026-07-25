import logging
import re

from elidia.memory.store import MemoryEntry, MemoryStore, MemoryTier

logger = logging.getLogger(__name__)

CORRECTION_PATTERNS = [
    r"(?i)\bno,?\s+(don'?t|not|never|stop|wrong|incorrect)",
    r"(?i)\bthat'?s\s+(wrong|incorrect|not right|not what)",
    r"(?i)\bactually,?\s+(it'?s|you should|the correct)",
    r"(?i)\binstead\s+(of|use|do)",
    r"(?i)\bplease\s+(don'?t|stop|never|avoid)",
]

CONFIRMATION_PATTERNS = [
    r"(?i)\b(yes|exactly|perfect|correct|right|good|great)\b.*\b(keep|continue|always|that'?s)",
    r"(?i)\bthat'?s?\s+(exactly|perfect|correct|right|what I)",
    r"(?i)\byes,?\s+(exactly|perfect|always do)",
]

PREFERENCE_PATTERNS = [
    r"(?i)\bI\s+(prefer|like|want|need|use|always)\b",
    r"(?i)\bmy\s+(preference|style|approach|convention)\b",
    r"(?i)\bwe\s+(always|usually|prefer|use)\b",
]


class AutoMemory:
    """Detects memorable patterns in conversation and saves them automatically."""

    def __init__(self, store: MemoryStore) -> None:
        logger.debug("Entered into AutoMemory.__init__")
        self._store = store

    def analyze_user_message(
        self,
        message: str,
        session_id: str = "",
        project_path: str = "",
    ) -> list[MemoryEntry]:
        logger.debug(f"Entered into analyze_user_message: len={len(message)}")
        saved: list[MemoryEntry] = []

        for pattern in CORRECTION_PATTERNS:
            if re.search(pattern, message):
                entry = MemoryEntry(
                    tier=MemoryTier.USER,
                    key=f"correction:{_extract_topic(message)}",
                    content=message.strip(),
                    source="auto_correction",
                    session_id=session_id,
                    project_path=project_path,
                    metadata={"type": "correction"},
                )
                self._store.save(entry)
                saved.append(entry)
                logger.info(f"Auto-saved correction memory: {entry.key}")
                break

        for pattern in CONFIRMATION_PATTERNS:
            if re.search(pattern, message):
                entry = MemoryEntry(
                    tier=MemoryTier.USER,
                    key=f"confirmation:{_extract_topic(message)}",
                    content=message.strip(),
                    source="auto_confirmation",
                    session_id=session_id,
                    project_path=project_path,
                    metadata={"type": "confirmation"},
                )
                self._store.save(entry)
                saved.append(entry)
                logger.info(f"Auto-saved confirmation memory: {entry.key}")
                break

        for pattern in PREFERENCE_PATTERNS:
            if re.search(pattern, message):
                entry = MemoryEntry(
                    tier=MemoryTier.USER,
                    key=f"preference:{_extract_topic(message)}",
                    content=message.strip(),
                    source="auto_preference",
                    session_id=session_id,
                    project_path=project_path,
                    metadata={"type": "preference"},
                )
                self._store.save(entry)
                saved.append(entry)
                logger.info(f"Auto-saved preference memory: {entry.key}")
                break

        return saved

    def save_project_context(
        self,
        key: str,
        content: str,
        project_path: str,
        session_id: str = "",
    ) -> str:
        logger.debug(f"Entered into save_project_context: key={key}")
        entry = MemoryEntry(
            tier=MemoryTier.PROJECT,
            key=key,
            content=content,
            project_path=project_path,
            session_id=session_id,
            source="project_context",
            metadata={"type": "project"},
        )
        return self._store.save(entry)

    def save_system_memory(self, key: str, content: str) -> str:
        logger.debug(f"Entered into save_system_memory: key={key}")
        entry = MemoryEntry(
            tier=MemoryTier.SYSTEM,
            key=key,
            content=content,
            source="system",
            metadata={"type": "system"},
        )
        return self._store.save(entry)

    def get_relevant_memories(
        self,
        query: str,
        project_path: str = "",
        session_id: str = "",
        limit: int = 10,
    ) -> list[MemoryEntry]:
        logger.debug(f"Entered into get_relevant_memories: query={query!r}")
        results: list[MemoryEntry] = []

        system_mems = self._store.list_memories(tier=MemoryTier.SYSTEM, limit=5)
        results.extend(system_mems)

        user_mems = self._store.search_text(query, tier=MemoryTier.USER, limit=5)
        results.extend(user_mems)

        if project_path:
            proj_mems = self._store.search_text(query, project_path=project_path, limit=5)
            results.extend(m for m in proj_mems if m.id not in {r.id for r in results})

        if session_id:
            sess_mems = self._store.list_memories(
                tier=MemoryTier.SESSION, session_id=session_id, limit=5
            )
            results.extend(m for m in sess_mems if m.id not in {r.id for r in results})

        seen_ids: set[str] = set()
        deduped: list[MemoryEntry] = []
        for m in results:
            if m.id not in seen_ids:
                seen_ids.add(m.id)
                deduped.append(m)

        return deduped[:limit]


def _extract_topic(text: str) -> str:
    words = text.split()[:6]
    topic = "_".join(w.lower().strip(".,!?'\"") for w in words if len(w) > 2)
    return topic[:60] or "general"
