from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, CreatedAtMixin, IdMixin, str_enum
from app.database.models.enums import ActionStatus, Actor, AuditAction, ToolPolicy


class AgentAction(IdMixin, CreatedAtMixin, Base):
    """Every state-changing tool call, including those waiting for confirmation."""

    __tablename__ = "agent_actions"
    __table_args__ = (Index("ix_agent_actions_user_id_status", "user_id", "status"),)

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="SET NULL")
    )
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    policy: Mapped[ToolPolicy] = mapped_column(str_enum(ToolPolicy, "policy"))
    status: Mapped[ActionStatus] = mapped_column(str_enum(ActionStatus, "status"))
    explanation: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None]
    decided_at: Mapped[datetime | None]
    executed_at: Mapped[datetime | None]


class AuditLog(IdMixin, CreatedAtMixin, Base):
    """Append-only record of why something changed."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_entity_type_entity_id", "entity_type", "entity_id"),)

    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    actor: Mapped[Actor] = mapped_column(str_enum(Actor, "actor"))
    action: Mapped[AuditAction] = mapped_column(str_enum(AuditAction, "action"))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    agent_action_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agent_actions.id", ondelete="SET NULL")
    )
