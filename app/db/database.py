from __future__ import annotations

from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import delete

from app.db.models import Base, UsageRecord


class Database:
    def __init__(self, url: str = "sqlite+aiosqlite:///data/gateway.db"):
        self.engine = create_async_engine(url, echo=False)
        self._session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._session_factory() as session:
            yield session

    async def cleanup_old_records(self, days: int = 10) -> int:
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with self.session() as session:
            result = await session.execute(
                delete(UsageRecord).where(UsageRecord.created_at < cutoff)
            )
            await session.commit()
            return result.rowcount
