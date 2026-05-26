from __future__ import annotations

import asyncio
from datetime import datetime

from app.db.database import Database
from app.db.models import UsageRecord


class UsageEventBroker:
    def __init__(self):
        self._subscribers: set[asyncio.Queue[dict]] = set()

    def subscribe(self) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, event: dict) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass


class UsageLogger:
    def __init__(self, db: Database, broker: UsageEventBroker | None = None):
        self.db = db
        self.broker = broker

    async def record(
        self,
        request_id: str,
        tenant: str,
        model: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost_usd: float,
    ) -> dict:
        record = UsageRecord(
            request_id=request_id,
            tenant=tenant,
            model=model,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
        )
        async with self.db.session() as session:
            session.add(record)
            await session.commit()

        event = {
            "request_id": request_id,
            "tenant": tenant,
            "model": model,
            "provider": provider,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
            "created_at": (
                record.created_at.isoformat() + "Z"
                if isinstance(record.created_at, datetime)
                else None
            ),
        }
        if self.broker is not None:
            await self.broker.publish(event)
        return event
