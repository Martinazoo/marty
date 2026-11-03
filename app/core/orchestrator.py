"""One tool-calling loop per user message."""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tools import ToolContext, ToolExecutor, ToolRegistry
from app.database.models import Conversation, Message, MessageRole, User
from app.integrations.ai import (
    AllProvidersFailedError,
    ChatMessage,
    CompletionRequest,
    InvalidRequestError,
    LLMProvider,
    Role,
)
from app.services.conversations import ConversationService

logger = logging.getLogger(__name__)

ALL_PROVIDERS_FAILED_TEXT = (
    "Ahora mismo no puedo conversar: los proveedores de IA no responden o han llegado a su "
    "límite gratuito. Prueba de nuevo en unos minutos. Los comandos (/help) siguen funcionando."
)
ERROR_TEXT = "Algo ha fallado al procesar tu mensaje. Prueba de nuevo o dímelo de otra forma."
LOOP_EXHAUSTED_TEXT = "No he conseguido terminar esta petición. ¿Puedes decírmelo de otra forma?"


@dataclass(frozen=True)
class PendingConfirmation:
    action_id: int
    explanation: str


@dataclass
class TurnResult:
    text: str
    confirmations: list[PendingConfirmation] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


def _add(total: int | None, value: int | None) -> int | None:
    return value if total is None else total + (value or 0)


class Orchestrator:
    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        executor: ToolExecutor,
        *,
        max_iterations: int,
        max_output_tokens: int,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._executor = executor
        self._max_iterations = max_iterations
        self._max_output_tokens = max_output_tokens

    async def run_turn(
        self,
        session: AsyncSession,
        *,
        user: User,
        conversation: Conversation,
        conversations: ConversationService,
        messages: list[ChatMessage],
        message_id: int,
        now: datetime,
    ) -> TurnResult:
        ctx = ToolContext(session=session, user=user, now=now, message_id=message_id)
        turn = TurnResult(text=LOOP_EXHAUSTED_TEXT)
        tools = self._registry.specs()

        for _ in range(self._max_iterations):
            request = CompletionRequest(
                tuple(messages), tools=tools, max_output_tokens=self._max_output_tokens
            )
            try:
                result = await self._llm.complete(request)
            except AllProvidersFailedError as exc:
                logger.warning("No LLM provider answered", extra={"failures": exc.failures})
                turn.text = ALL_PROVIDERS_FAILED_TEXT
                return turn
            except InvalidRequestError:
                logger.exception("LLM rejected our request")
                turn.text = ERROR_TEXT
                return turn

            turn.provider, turn.model = result.provider, result.model
            turn.input_tokens = _add(turn.input_tokens, result.usage.input_tokens)
            turn.output_tokens = _add(turn.output_tokens, result.usage.output_tokens)

            if not result.message.tool_calls:
                turn.text = result.message.content or LOOP_EXHAUSTED_TEXT
                return turn

            messages.append(result.message)
            for call in result.message.tool_calls:
                outcome = await self._executor.run(ctx, call)
                logger.info("Tool called", extra={"tool": call.name})
                await conversations.add(
                    conversation,
                    Message(
                        role=MessageRole.TOOL,
                        content=outcome.content,
                        tool_name=call.name[:64],
                        tool_call_id=call.id[:128],
                    ),
                    now,
                )
                messages.append(ChatMessage(Role.TOOL, outcome.content, tool_call_id=call.id))
                pending = outcome.pending_action
                shown = {c.action_id for c in turn.confirmations}
                if pending is not None and pending.id not in shown:
                    turn.confirmations.append(
                        PendingConfirmation(pending.id, pending.explanation or "")
                    )

        logger.warning("Tool loop hit the iteration cap", extra={"cap": self._max_iterations})
        return turn
