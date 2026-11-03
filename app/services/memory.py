"""User profile and long-term memory.

MARTY only stores what the user stated or explicitly confirmed. Corrections
create a new row and mark the old one as superseded, so history is kept.
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Actor,
    AuditAction,
    Memory,
    MemoryCategory,
    MemoryKind,
    MemorySource,
)
from app.database.repositories import AuditRepository, MemoryRepository


class MemoryNotFoundError(LookupError):
    pass


class MemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._memories = MemoryRepository(session)
        self._audit = AuditRepository(session)

    async def remember(
        self,
        user_id: int,
        *,
        content: str,
        kind: MemoryKind,
        category: MemoryCategory,
        importance: int,
        source: MemorySource,
        source_message_id: int | None,
        actor: Actor,
        agent_action_id: int | None = None,
    ) -> tuple[Memory, bool]:
        """Store a memory. Returns the existing one (and False) if identical text is known."""
        content = content.strip()
        existing = await self._memories.find_active_by_content(user_id, content)
        if existing is not None:
            return existing, False
        memory = await self._memories.add(
            Memory(
                user_id=user_id,
                kind=kind,
                category=category,
                content=content,
                importance=importance,
                source=source,
                source_message_id=source_message_id,
            )
        )
        await self._audit.record(
            user_id=user_id,
            actor=actor,
            action=AuditAction.CREATE,
            entity_type="memory",
            entity_id=memory.id,
            changes={"content": [None, content]},
            agent_action_id=agent_action_id,
        )
        return memory, True

    async def correct(
        self,
        user_id: int,
        memory_id: int,
        *,
        new_content: str,
        source_message_id: int | None,
        actor: Actor,
        agent_action_id: int | None = None,
    ) -> Memory:
        old = await self.get(user_id, memory_id)
        new = await self._memories.add(
            Memory(
                user_id=user_id,
                kind=old.kind,
                category=old.category,
                content=new_content.strip(),
                importance=old.importance,
                source=MemorySource.USER_STATED,
                source_message_id=source_message_id,
            )
        )
        old.superseded_by_id = new.id
        await self._audit.record(
            user_id=user_id,
            actor=actor,
            action=AuditAction.UPDATE,
            entity_type="memory",
            entity_id=old.id,
            changes={"content": [old.content, new.content], "superseded_by_id": [None, new.id]},
            agent_action_id=agent_action_id,
        )
        return new

    async def forget(
        self,
        user_id: int,
        memory_id: int,
        *,
        now: datetime,
        actor: Actor,
        agent_action_id: int | None = None,
    ) -> Memory:
        memory = await self.get(user_id, memory_id)
        memory.deleted_at = now
        await self._audit.record(
            user_id=user_id,
            actor=actor,
            action=AuditAction.DELETE,
            entity_type="memory",
            entity_id=memory.id,
            changes={"content": [memory.content, None]},
            agent_action_id=agent_action_id,
        )
        return memory

    async def get(self, user_id: int, memory_id: int) -> Memory:
        memory = await self._memories.get_active(user_id, memory_id)
        if memory is None:
            raise MemoryNotFoundError(f"memory {memory_id} not found")
        return memory

    async def profile(self, user_id: int, limit: int = 40) -> list[Memory]:
        return await self._memories.list_active(user_id, kind=MemoryKind.PROFILE, limit=limit)

    async def relevant(self, user_id: int, text: str, limit: int = 8) -> list[Memory]:
        return await self._memories.search(user_id, text, kind=MemoryKind.LONG_TERM, limit=limit)

    async def find(
        self,
        user_id: int,
        *,
        query: str | None = None,
        category: MemoryCategory | None = None,
        limit: int = 15,
    ) -> list[Memory]:
        if query:
            return await self._memories.search(user_id, query, category=category, limit=limit)
        return await self._memories.list_active(user_id, category=category, limit=limit)
