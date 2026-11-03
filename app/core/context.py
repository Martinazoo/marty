"""Builds the messages sent to the LLM for one user turn."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.prompts import (
    CAPABILITIES_BASIC,
    CAPABILITIES_CALENDAR,
    NO_MEMORIES,
    SYSTEM_PROMPT,
)
from app.database.models import Conversation, Memory, MessageRole, User
from app.integrations.ai import ChatMessage, Role
from app.integrations.apple_calendar import CalendarError
from app.services.calendar import CalendarService
from app.services.conversations import ConversationService
from app.services.memory import MemoryService

WEEKDAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def format_local(now: datetime, tz: ZoneInfo) -> str:
    local = now.astimezone(tz)
    return (
        f"{WEEKDAYS_ES[local.weekday()]} {local.day} de {MONTHS_ES[local.month - 1]} "
        f"de {local.year}, {local:%H:%M}"
    )


def _memory_lines(memories: list[Memory]) -> str:
    return "\n".join(f"- [#{m.id}] ({m.category.value}) {m.content}" for m in memories)


@dataclass(frozen=True)
class ContextLimits:
    recent_messages: int = 16
    profile_memories: int = 40
    relevant_memories: int = 8


class ContextBuilder:
    def __init__(
        self, limits: ContextLimits | None = None, calendar: CalendarService | None = None
    ) -> None:
        self.limits = limits or ContextLimits()
        self._calendar = calendar

    async def _calendar_section(self) -> str:
        if self._calendar is None:
            return CAPABILITIES_BASIC
        try:
            calendars = await self._calendar.calendars()
        except CalendarError:
            return CAPABILITIES_CALENDAR + "\n\nThe calendar is unreachable at the moment."
        names = ", ".join(c.name for c in calendars) or "none yet"
        return CAPABILITIES_CALENDAR + f"\n\nThe user's calendars: {names}."

    async def build(
        self,
        session: AsyncSession,
        *,
        user: User,
        conversation: Conversation,
        conversations: ConversationService,
        user_text: str,
        now: datetime,
    ) -> list[ChatMessage]:
        memory = MemoryService(session)
        profile = await memory.profile(user.id, self.limits.profile_memories)
        relevant = await memory.relevant(user.id, user_text, self.limits.relevant_memories)

        sections = []
        if profile:
            sections.append("What you know about the user (profile):\n" + _memory_lines(profile))
        if relevant:
            sections.append(
                "Long-term memories that may be relevant now:\n" + _memory_lines(relevant)
            )
        previous = await conversations.previous(conversation)
        if previous is not None and previous.summary:
            sections.append("Summary of your previous conversation:\n" + previous.summary)
        if conversation.summary:
            sections.append(
                "Summary of earlier messages in this conversation:\n" + conversation.summary
            )

        system = SYSTEM_PROMPT.format(
            user_name=user.display_name or "the user",
            now_local=format_local(now, ZoneInfo(user.timezone)),
            timezone=user.timezone,
            capabilities=await self._calendar_section(),
            memory_section="\n\n".join(sections) if sections else NO_MEMORIES,
        )
        history = await conversations.recent(conversation, self.limits.recent_messages)
        messages = [ChatMessage(Role.SYSTEM, system)]
        messages.extend(
            ChatMessage(Role.USER if m.role is MessageRole.USER else Role.ASSISTANT, m.content)
            for m in history
        )
        return messages
