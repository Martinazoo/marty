from enum import StrEnum


class Channel(StrEnum):
    TELEGRAM = "telegram"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MemoryKind(StrEnum):
    PROFILE = "profile"
    LONG_TERM = "long_term"


class MemoryCategory(StrEnum):
    TRAINING = "training"
    STUDY = "study"
    WORK = "work"
    PERSONAL = "personal"
    PREFERENCE = "preference"
    GENERAL = "general"


class MemorySource(StrEnum):
    USER_STATED = "user_stated"
    USER_CONFIRMED = "user_confirmed"
    IMPORTED = "imported"


class ToolPolicy(StrEnum):
    READ = "read"
    WRITE = "write"
    CONFIRM = "confirm"


class ActionStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class Actor(StrEnum):
    USER = "user"
    MARTY = "marty"
    SCHEDULER = "scheduler"
    SYSTEM = "system"


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    SYNC = "sync"
    IMPORT = "import"
