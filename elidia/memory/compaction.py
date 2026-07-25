import logging

from elidia.api.client import AiUtilsClient, ChatMessage
from elidia.memory.store import MemoryEntry, MemoryStore, MemoryTier

logger = logging.getLogger(__name__)

COMPACTION_PROMPT = """Summarize this conversation into a concise memory entry that captures:
1. What the user was working on
2. Key decisions made
3. Preferences expressed
4. Any corrections or feedback given
5. Tools and approaches that worked well

Keep it under 300 words. Focus on facts that would be useful in future sessions.
Do not include greetings, small talk, or redundant information.

Conversation:
{conversation}"""


class SessionCompactor:
    """Compacts session history into a persistent USER-tier memory at session end."""

    def __init__(self, store: MemoryStore, client: AiUtilsClient) -> None:
        logger.debug("Entered into SessionCompactor.__init__")
        self._store = store
        self._client = client

    async def compact_session(
        self,
        messages: list[ChatMessage],
        session_id: str,
        project_path: str = "",
        model: str = "deepseek-chat",
    ) -> str | None:
        logger.debug(f"Entered into compact_session: msg_count={len(messages)}, session_id={session_id}")

        if len(messages) < 4:
            logger.info("Session too short to compact (< 4 messages)")
            return None

        conversation = self._format_conversation(messages)

        try:
            summary_response = await self._client.chat_completion(
                messages=[
                    ChatMessage(role="system", content="You are a memory compaction assistant. Be concise and factual."),
                    ChatMessage(role="user", content=COMPACTION_PROMPT.format(conversation=conversation)),
                ],
                model=model,
                temperature=0.3,
                max_tokens=500,
            )

            if not summary_response.content:
                logger.warning("Compaction returned empty content")
                return None

            entry = MemoryEntry(
                tier=MemoryTier.USER,
                key=f"session_summary:{session_id[:8]}",
                content=summary_response.content,
                session_id=session_id,
                project_path=project_path,
                source="session_compaction",
                metadata={
                    "type": "session_summary",
                    "message_count": len(messages),
                    "compaction_model": model,
                    "compaction_tokens": summary_response.tokens_in + summary_response.tokens_out,
                },
            )

            memory_id = self._store.save(entry)
            logger.info(f"Session compacted to memory {memory_id}")
            return memory_id

        except Exception as e:
            logger.warning(f"Session compaction failed: {e}")
            return None

    def _format_conversation(self, messages: list[ChatMessage], max_chars: int = 8000) -> str:
        logger.debug(f"Entered into _format_conversation: msg_count={len(messages)}")
        lines: list[str] = []
        total = 0

        for msg in messages:
            role = msg.role.upper()
            content = msg.content
            if content.startswith("[Tool result"):
                content = content[:200] + "..." if len(content) > 200 else content
            line = f"{role}: {content}"
            if total + len(line) > max_chars:
                lines.append("... (earlier messages truncated)")
                break
            lines.append(line)
            total += len(line)

        lines.reverse()
        return "\n\n".join(lines)
