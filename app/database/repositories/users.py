from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User, UserPreferences


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        return await self._session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )

    async def create(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        display_name: str | None,
        timezone: str,
    ) -> User:
        user = User(
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            display_name=display_name,
            timezone=timezone,
        )
        self._session.add(user)
        await self._session.flush()
        self._session.add(UserPreferences(user_id=user.id))
        await self._session.flush()
        return user

    async def get_preferences(self, user_id: int) -> UserPreferences:
        preferences = await self._session.get(UserPreferences, user_id, populate_existing=True)
        if preferences is None:
            raise LookupError(f"user {user_id} has no preferences row")
        return preferences
