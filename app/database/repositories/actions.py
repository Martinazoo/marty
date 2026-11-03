from datetime import datetime
from typing import Any

from sqlalchemy import cast, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ActionStatus, Actor, AgentAction, AuditAction, AuditLog


class ActionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, action: AgentAction) -> AgentAction:
        self._session.add(action)
        await self._session.flush()
        return action

    async def latest_identical(
        self, user_id: int, tool_name: str, arguments: dict[str, Any]
    ) -> AgentAction | None:
        """The most recent action with the same tool and (JSONB-equal) arguments."""
        return await self._session.scalar(
            select(AgentAction)
            .where(
                AgentAction.user_id == user_id,
                AgentAction.tool_name == tool_name,
                AgentAction.arguments == cast(arguments, JSONB),
            )
            .order_by(AgentAction.id.desc())
            .limit(1)
        )

    async def get_for_update(self, user_id: int, action_id: int) -> AgentAction | None:
        """Locks the row so two button presses cannot both execute the action."""
        return await self._session.scalar(
            select(AgentAction)
            .where(AgentAction.id == action_id, AgentAction.user_id == user_id)
            .with_for_update()
        )

    async def expire_pending(self, user_id: int, now: datetime) -> int:
        result = await self._session.execute(
            update(AgentAction)
            .where(
                AgentAction.user_id == user_id,
                AgentAction.status == ActionStatus.PENDING_CONFIRMATION,
                AgentAction.expires_at <= now,
            )
            .values(status=ActionStatus.EXPIRED, decided_at=now)
        )
        return result.rowcount or 0


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: int | None,
        actor: Actor,
        action: AuditAction,
        entity_type: str,
        entity_id: int | None,
        changes: dict[str, Any] | None = None,
        reason: str | None = None,
        agent_action_id: int | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            user_id=user_id,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            changes=changes,
            reason=reason,
            agent_action_id=agent_action_id,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry
