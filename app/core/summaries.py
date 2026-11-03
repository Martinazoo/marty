"""Rolling conversation summaries, so long chats fit in a small context."""

import logging
from datetime import datetime

from app.core.prompts import SUMMARY_SYSTEM_PROMPT, SUMMARY_USER_PROMPT
from app.database.models import Conversation, MessageRole
from app.integrations.ai import (
    AllProvidersFailedError,
    ChatMessage,
    CompletionRequest,
    InvalidRequestError,
    LLMProvider,
    ProviderError,
    Role,
)
from app.services.conversations import ConversationService

logger = logging.getLogger(__name__)

MAX_CHARS_PER_MESSAGE = 1000


class Summarizer:
    def __init__(self, llm: LLMProvider, *, keep_recent: int, min_batch: int = 8) -> None:
        """Messages outside the `keep_recent` window are folded into the summary once
        at least `min_batch` of them have accumulated."""
        self._llm = llm
        self._keep_recent = keep_recent
        self._min_batch = min_batch

    async def update(
        self,
        conversations: ConversationService,
        conversation: Conversation,
        now: datetime,
        *,
        complete: bool = False,
    ) -> bool:
        """Fold older messages into the summary. `complete=True` folds everything,
        for a conversation that has ended. Returns whether the summary changed."""
        if complete:
            last = await conversations.latest_message(conversation)
            if last is None:
                return False
            up_to_id = last.id
        else:
            window = await conversations.recent(conversation, self._keep_recent)
            if len(window) < self._keep_recent:
                return False
            up_to_id = window[0].id - 1

        batch = await conversations.unsummarized(conversation, up_to_id=up_to_id)
        if not batch or (not complete and len(batch) < self._min_batch):
            return False

        transcript = "\n".join(
            f"{'Usuario' if m.role is MessageRole.USER else 'MARTY'}: "
            f"{m.content[:MAX_CHARS_PER_MESSAGE]}"
            for m in batch
        )
        request = CompletionRequest(
            (
                ChatMessage(Role.SYSTEM, SUMMARY_SYSTEM_PROMPT),
                ChatMessage(
                    Role.USER,
                    SUMMARY_USER_PROMPT.format(
                        summary=conversation.summary or "(vacío)", transcript=transcript
                    ),
                ),
            ),
            temperature=0.2,
            max_output_tokens=2048,
        )
        try:
            result = await self._llm.complete(request)
        except (AllProvidersFailedError, ProviderError, InvalidRequestError) as exc:
            # Skipped when providers are saturated; retried after a later message.
            logger.info("Conversation summary skipped", extra={"error": type(exc).__name__})
            return False
        if not result.message.content:
            return False
        conversations.store_summary(conversation, result.message.content, batch[-1].id, now)
        return True
