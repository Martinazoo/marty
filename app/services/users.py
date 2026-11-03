from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Actor, AuditAction, User, UserPreferences
from app.database.repositories import AuditRepository, UserRepository


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._users = UserRepository(session)
        self._audit = AuditRepository(session)

    async def get(self, user_id: int) -> User:
        user = await self._users.get(user_id)
        if user is None:
            raise LookupError(f"user {user_id} not found")
        return user

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        return await self._users.get_by_telegram_id(telegram_user_id)

    async def ensure(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        display_name: str | None,
        timezone: str,
    ) -> tuple[User, bool]:
        """Return the user, creating it on first contact. The flag says whether it is new."""
        user = await self._users.get_by_telegram_id(telegram_user_id)
        if user is None:
            user = await self._users.create(
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                display_name=display_name,
                timezone=timezone,
            )
            await self._audit.record(
                user_id=user.id,
                actor=Actor.SYSTEM,
                action=AuditAction.CREATE,
                entity_type="user",
                entity_id=user.id,
                reason="First message from an allowed Telegram user",
            )
            return user, True
        if user.telegram_chat_id != telegram_chat_id:
            user.telegram_chat_id = telegram_chat_id
        if display_name and user.display_name != display_name:
            user.display_name = display_name
        return user, False

    async def preferences(self, user_id: int) -> UserPreferences:
        return await self._users.get_preferences(user_id)

    async def pause_notifications(self, user_id: int, until: datetime | None) -> UserPreferences:
        preferences = await self._users.get_preferences(user_id)
        preferences.notifications_paused = True
        preferences.paused_until = until
        await self._audit.record(
            user_id=user_id,
            actor=Actor.USER,
            action=AuditAction.UPDATE,
            entity_type="user_preferences",
            entity_id=user_id,
            changes={"notifications_paused": True, "paused_until": until and until.isoformat()},
            reason="/pause",
        )
        return preferences

    async def resume_notifications(self, user_id: int) -> UserPreferences:
        preferences = await self._users.get_preferences(user_id)
        preferences.notifications_paused = False
        preferences.paused_until = None
        await self._audit.record(
            user_id=user_id,
            actor=Actor.USER,
            action=AuditAction.UPDATE,
            entity_type="user_preferences",
            entity_id=user_id,
            changes={"notifications_paused": False},
            reason="/resume",
        )
        return preferences
