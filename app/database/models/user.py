from datetime import datetime, time

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    SmallInteger,
    Text,
    Time,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, IdMixin, TimestampMixin


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    display_name: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, server_default="Europe/Madrid")
    locale: Mapped[str] = mapped_column(Text, server_default="es-ES")
    is_active: Mapped[bool] = mapped_column(server_default=true())


class UserPreferences(Base):
    """One row per user. Also holds the notification settings used in Phase 5."""

    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint("weekly_report_weekday BETWEEN 1 AND 7", name="weekly_report_weekday"),
        CheckConstraint("study_inactivity_days >= 1", name="study_inactivity_days"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    notifications_paused: Mapped[bool] = mapped_column(server_default=false())
    paused_until: Mapped[datetime | None]
    quiet_hours_start: Mapped[time | None] = mapped_column(Time)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time)
    morning_briefing_enabled: Mapped[bool] = mapped_column(server_default=true())
    morning_briefing_time: Mapped[time] = mapped_column(Time, server_default=text("'07:30'"))
    weekly_report_enabled: Mapped[bool] = mapped_column(server_default=true())
    weekly_report_weekday: Mapped[int] = mapped_column(SmallInteger, server_default=text("7"))
    weekly_report_time: Mapped[time] = mapped_column(Time, server_default=text("'20:00'"))
    study_inactivity_days: Mapped[int] = mapped_column(SmallInteger, server_default=text("3"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
