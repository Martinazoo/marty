from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, IdMixin, SoftDeleteMixin, TimestampMixin, str_enum
from app.database.models.enums import MemoryCategory, MemoryKind, MemorySource


class Memory(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    """User profile facts and long-term memory.

    Corrections never overwrite: a new row is created and the old one points
    to it through `superseded_by_id`.
    """

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("importance BETWEEN 1 AND 5", name="importance"),
        Index("ix_memories_user_id_kind", "user_id", "kind"),
        Index("ix_memories_search_vector", "search_vector", postgresql_using="gin"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[MemoryKind] = mapped_column(str_enum(MemoryKind, "kind"))
    category: Mapped[MemoryCategory] = mapped_column(str_enum(MemoryCategory, "category"))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[MemorySource] = mapped_column(str_enum(MemorySource, "source"))
    importance: Mapped[int] = mapped_column(SmallInteger, server_default=text("3"))
    source_message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="SET NULL")
    )
    superseded_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("memories.id", ondelete="SET NULL")
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('spanish', content)", persisted=True), deferred=True
    )
