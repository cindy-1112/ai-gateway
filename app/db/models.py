from datetime import datetime

from sqlalchemy import String, Integer, Float, DateTime, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UsageRecord(Base):
    __tablename__ = "usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    completion_tokens: Mapped[int] = mapped_column(Integer)
    total_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )


class QuotaState(Base):
    __tablename__ = "quota_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    daily_tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    monthly_tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    daily_reset_at: Mapped[str] = mapped_column(String(32))
    monthly_reset_at: Mapped[str] = mapped_column(String(32))


class KeyState(Base):
    __tablename__ = "key_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    key_suffix: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), default="active")
    cooldown_until: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_used_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)


class VerificationCode(Base):
    __tablename__ = "verification_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(16), index=True)
    target: Mapped[str] = mapped_column(String(128), index=True)
    code: Mapped[str] = mapped_column(String(12))
    purpose: Mapped[str] = mapped_column(String(32), default="register")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="user")
    tenant: Mapped[str] = mapped_column(String(64), default="default")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AdminUserModulePermission(Base):
    __tablename__ = "admin_user_module_permissions"
    __table_args__ = (
        UniqueConstraint("user_id", "module", name="uq_admin_user_module"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    module: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )


class UserModelRequest(Base):
    __tablename__ = "user_model_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(128), index=True)
    alias: Mapped[str] = mapped_column(String(128), index=True)
    model: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    request_type: Mapped[str] = mapped_column(String(32), default="initial")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class UserModelBinding(Base):
    __tablename__ = "user_model_bindings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_model_binding_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(128), index=True)
    alias: Mapped[str] = mapped_column(String(128), index=True)
    model: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
