import re

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Memory, MemoryCategory, MemoryKind

_WORD_RE = re.compile(r"\w{3,}")
_MAX_QUERY_TERMS = 12


def _or_tsquery(text: str) -> str | None:
    """Turn free text into `word1 | word2 | ...` for `to_tsquery`.

    `plainto_tsquery` ANDs every word, so a natural sentence rarely matches a
    stored fact. Only word characters survive, so the result is always valid
    tsquery syntax.
    """
    terms = list(dict.fromkeys(word.lower() for word in _WORD_RE.findall(text)))
    if not terms:
        return None
    return " | ".join(terms[:_MAX_QUERY_TERMS])


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _active(self, user_id: int) -> Select[tuple[Memory]]:
        return select(Memory).where(
            Memory.user_id == user_id,
            Memory.deleted_at.is_(None),
            Memory.superseded_by_id.is_(None),
        )

    async def add(self, memory: Memory) -> Memory:
        self._session.add(memory)
        await self._session.flush()
        return memory

    async def get_active(self, user_id: int, memory_id: int) -> Memory | None:
        return await self._session.scalar(self._active(user_id).where(Memory.id == memory_id))

    async def find_active_by_content(self, user_id: int, content: str) -> Memory | None:
        return await self._session.scalar(
            self._active(user_id).where(func.lower(Memory.content) == content.lower()).limit(1)
        )

    async def list_active(
        self,
        user_id: int,
        *,
        kind: MemoryKind | None = None,
        category: MemoryCategory | None = None,
        limit: int = 50,
    ) -> list[Memory]:
        query = self._active(user_id)
        if kind is not None:
            query = query.where(Memory.kind == kind)
        if category is not None:
            query = query.where(Memory.category == category)
        query = query.order_by(Memory.importance.desc(), Memory.id.desc()).limit(limit)
        return list((await self._session.scalars(query)).all())

    async def search(
        self,
        user_id: int,
        text: str,
        *,
        kind: MemoryKind | None = None,
        category: MemoryCategory | None = None,
        limit: int = 8,
    ) -> list[Memory]:
        """Full-text search (Spanish), ranked by relevance, then importance and recency."""
        terms = _or_tsquery(text)
        if terms is None:
            return []
        tsquery = func.to_tsquery("spanish", terms)
        query = self._active(user_id).where(Memory.search_vector.bool_op("@@")(tsquery))
        if kind is not None:
            query = query.where(Memory.kind == kind)
        if category is not None:
            query = query.where(Memory.category == category)
        query = query.order_by(
            func.ts_rank(Memory.search_vector, tsquery).desc(),
            Memory.importance.desc(),
            Memory.id.desc(),
        ).limit(limit)
        return list((await self._session.scalars(query)).all())
