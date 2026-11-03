from datetime import UTC, datetime, time, timedelta

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.database.base import Base
from app.database.models import (
    ActionStatus,
    AgentAction,
    Memory,
    MemoryCategory,
    MemoryKind,
    MemorySource,
    Message,
    MessageRole,
    ToolPolicy,
    User,
)
from app.database.repositories import (
    ActionRepository,
    ConversationRepository,
    MemoryRepository,
    UserRepository,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


async def make_user(session: AsyncSession, telegram_user_id: int = 1001) -> User:
    return await UserRepository(session).create(
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_user_id,
        display_name="Test",
        timezone="Europe/Madrid",
    )


def make_memory(user: User, content: str, **overrides: object) -> Memory:
    values: dict[str, object] = {
        "user_id": user.id,
        "kind": MemoryKind.LONG_TERM,
        "category": MemoryCategory.GENERAL,
        "content": content,
        "source": MemorySource.USER_STATED,
        "importance": 3,
    }
    values.update(overrides)
    return Memory(**values)


async def test_migrations_match_models(db_engine: AsyncEngine):
    def diff(connection):
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)

    async with db_engine.connect() as connection:
        assert await connection.run_sync(diff) == []


async def test_creating_a_user_creates_default_preferences(session: AsyncSession):
    user = await make_user(session)

    preferences = await UserRepository(session).get_preferences(user.id)

    assert preferences.notifications_paused is False
    assert preferences.morning_briefing_time == time(7, 30)
    assert preferences.weekly_report_weekday == 7
    assert await UserRepository(session).get_by_telegram_id(1001) == user


async def test_telegram_user_id_is_unique(session: AsyncSession):
    await make_user(session)
    with pytest.raises(IntegrityError):
        await make_user(session)


async def test_recent_messages_are_returned_oldest_first_and_filtered_by_role(
    session: AsyncSession,
):
    user = await make_user(session)
    repo = ConversationRepository(session)
    conversation = await repo.create(user.id, NOW)
    for index, role in enumerate([MessageRole.USER, MessageRole.TOOL, MessageRole.ASSISTANT] * 2):
        await repo.add_message(conversation, Message(role=role, content=f"m{index}"))

    recent = await repo.recent_messages(conversation.id, limit=3)

    assert [m.content for m in recent] == ["m2", "m3", "m5"]


async def test_latest_conversation(session: AsyncSession):
    user = await make_user(session)
    repo = ConversationRepository(session)
    first = await repo.create(user.id, NOW)
    second = await repo.create(user.id, NOW + timedelta(hours=8))

    assert await repo.latest(user.id) == second
    assert await repo.previous(user.id, second.id) == first


async def test_memory_search_uses_spanish_stemming_and_skips_inactive(session: AsyncSession):
    user = await make_user(session)
    repo = MemoryRepository(session)
    match = await repo.add(make_memory(user, "Entreno CrossFit los lunes por la noche"))
    old = await repo.add(make_memory(user, "Entreno CrossFit los martes"))
    deleted = await repo.add(make_memory(user, "Odio los entrenamientos largos", deleted_at=NOW))
    old.superseded_by_id = match.id
    await repo.add(make_memory(user, "Mi color favorito es el azul"))
    await session.flush()

    results = await repo.search(user.id, "¿Cuándo son mis entrenamientos de crossfit?")

    assert [m.id for m in results] == [match.id]
    assert deleted.id not in [m.id for m in await repo.list_active(user.id)]


async def test_memory_search_ignores_tsquery_syntax(session: AsyncSession):
    user = await make_user(session)
    await MemoryRepository(session).add(make_memory(user, "Prefiero entrenar por la mañana"))

    assert await MemoryRepository(session).search(user.id, "entrenar' & | ! (:*") != []
    assert await MemoryRepository(session).search(user.id, "?? !!") == []


async def test_memories_are_isolated_per_user(session: AsyncSession):
    alice = await make_user(session, 1)
    bob = await make_user(session, 2)
    memory = await MemoryRepository(session).add(make_memory(alice, "Juego a baloncesto"))

    assert await MemoryRepository(session).get_active(bob.id, memory.id) is None
    assert await MemoryRepository(session).search(bob.id, "baloncesto") == []


async def test_importance_is_constrained(session: AsyncSession):
    user = await make_user(session)
    with pytest.raises(IntegrityError):
        await MemoryRepository(session).add(make_memory(user, "x", importance=9))


async def test_enum_columns_reject_unknown_values(session: AsyncSession):
    user = await make_user(session)
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO memories (user_id, kind, category, content, source) "
                "VALUES (:user_id, 'gossip', 'general', 'x', 'user_stated')"
            ),
            {"user_id": user.id},
        )


async def test_expire_pending_actions(session: AsyncSession):
    user = await make_user(session)
    repo = ActionRepository(session)
    action = await repo.add(
        AgentAction(
            user_id=user.id,
            tool_name="forget_memory",
            arguments={"memory_id": 1},
            policy=ToolPolicy.CONFIRM,
            status=ActionStatus.PENDING_CONFIRMATION,
            expires_at=NOW,
        )
    )

    assert await repo.expire_pending(user.id, NOW + timedelta(seconds=1)) == 1
    await session.refresh(action)
    assert action.status == ActionStatus.EXPIRED
    assert await repo.get_for_update(user.id + 1, action.id) is None
