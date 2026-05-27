from __future__ import annotations

from datetime import timedelta
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import delete, text

from app.db.models import Base, UsageRecord
from app.time_utils import beijing_now


class Database:
    def __init__(self, url: str = "sqlite+aiosqlite:///data/gateway.db"):
        self.engine = create_async_engine(url, echo=False)
        self._session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if self.engine.url.get_backend_name().startswith("sqlite"):
                await conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS gateway_migrations "
                        "(name VARCHAR(128) PRIMARY KEY, applied_at DATETIME NOT NULL)"
                    )
                )
                result = await conn.execute(text("PRAGMA table_info(user_api_keys)"))
                columns = {row[1] for row in result.fetchall()}
                user_key_additions = {
                    "key_secret": "VARCHAR(256)",
                    "note": "VARCHAR(256)",
                    "expires_at": "DATETIME",
                    "last_used_ip": "VARCHAR(64)",
                }
                for column, column_type in user_key_additions.items():
                    if column not in columns:
                        await conn.execute(
                            text(f"ALTER TABLE user_api_keys ADD COLUMN {column} {column_type}")
                        )
                result = await conn.execute(text("PRAGMA table_info(usage)"))
                usage_columns = {row[1] for row in result.fetchall()}
                usage_additions = {
                    "user_id": "INTEGER",
                    "username": "VARCHAR(128)",
                    "api_key_prefix": "VARCHAR(32)",
                    "model_binding_id": "INTEGER",
                    "uncached_input_tokens": "INTEGER DEFAULT 0",
                    "cached_input_tokens": "INTEGER DEFAULT 0",
                    "cost_currency": "VARCHAR(8) DEFAULT 'CNY'",
                }
                for column, column_type in usage_additions.items():
                    if column not in usage_columns:
                        await conn.execute(
                            text(f"ALTER TABLE usage ADD COLUMN {column} {column_type}")
                        )
                await conn.execute(
                    text(
                        "UPDATE usage SET uncached_input_tokens = prompt_tokens "
                        "WHERE uncached_input_tokens IS NULL OR uncached_input_tokens = 0"
                    )
                )
                await conn.execute(
                    text(
                        "UPDATE usage SET cost_currency = 'CNY' "
                        "WHERE cost_currency IS NULL OR cost_currency = ''"
                    )
                )
                migration = await conn.execute(
                    text(
                        "SELECT name FROM gateway_migrations "
                        "WHERE name = 'timestamps_beijing_v1'"
                    )
                )
                if migration.first() is None:
                    datetime_columns = {
                        "usage": ["created_at"],
                        "request_logs": ["created_at"],
                        "verification_codes": ["created_at", "expires_at", "consumed_at"],
                        "registration_requests": ["created_at", "reviewed_at", "registered_at"],
                        "admin_users": ["created_at"],
                        "admin_sessions": ["created_at", "expires_at", "revoked_at"],
                        "admin_user_module_permissions": ["created_at"],
                        "user_model_requests": ["created_at", "reviewed_at"],
                        "user_model_bindings": ["created_at", "updated_at", "stopped_at", "resumed_at"],
                        "user_api_keys": ["created_at", "expires_at", "last_used_at", "revoked_at"],
                        "audit_logs": ["created_at"],
                    }
                    for table_name, column_names in datetime_columns.items():
                        for column_name in column_names:
                            await conn.execute(
                                text(
                                    f"UPDATE {table_name} "
                                    f"SET {column_name} = datetime({column_name}, '+8 hours') "
                                    f"WHERE {column_name} IS NOT NULL"
                                )
                            )
                    await conn.execute(
                        text(
                            "INSERT INTO gateway_migrations (name, applied_at) "
                            "VALUES ('timestamps_beijing_v1', :applied_at)"
                        ),
                        {"applied_at": beijing_now()},
                    )

    async def close(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._session_factory() as session:
            yield session

    async def cleanup_old_records(self, days: int = 10) -> int:
        cutoff = beijing_now() - timedelta(days=days)
        async with self.session() as session:
            result = await session.execute(
                delete(UsageRecord).where(UsageRecord.created_at < cutoff)
            )
            await session.commit()
            return result.rowcount
