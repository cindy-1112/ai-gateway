from __future__ import annotations

from app.db.database import Database
from app.db.models import QuotaState
from app.time_utils import beijing_now


class QuotaManager:
    def __init__(
        self,
        db: Database,
        daily_limit: int = 500000,
        monthly_limit: int = 10000000,
    ):
        self.db = db
        self.daily_limit = daily_limit
        self.monthly_limit = monthly_limit
        self._cache: dict[str, QuotaState] = {}

    async def _ensure_state(self, tenant: str) -> QuotaState:
        if tenant in self._cache:
            return self._cache[tenant]

        async with self.db.session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(QuotaState).where(QuotaState.tenant == tenant)
            )
            state = result.scalar_one_or_none()

        if state is None:
            state = QuotaState(
                tenant=tenant,
                daily_tokens_used=0,
                monthly_tokens_used=0,
                daily_reset_at=beijing_now().strftime("%Y-%m-%d"),
                monthly_reset_at=beijing_now().strftime("%Y-%m"),
            )
            async with self.db.session() as session:
                session.add(state)
                await session.commit()

        self._cache[tenant] = state
        return state

    async def check_and_reserve(self, tenant: str, tokens: int) -> bool:
        state = await self._ensure_state(tenant)
        self._check_reset(state)

        if state.daily_tokens_used + tokens > self.daily_limit:
            return False
        if state.monthly_tokens_used + tokens > self.monthly_limit:
            return False

        state.daily_tokens_used += tokens
        state.monthly_tokens_used += tokens
        await self._persist(state)
        return True

    def _check_reset(self, state: QuotaState) -> None:
        now = beijing_now()
        today = now.strftime("%Y-%m-%d")
        this_month = now.strftime("%Y-%m")

        if state.daily_reset_at != today:
            state.daily_tokens_used = 0
            state.daily_reset_at = today
        if state.monthly_reset_at != this_month:
            state.monthly_tokens_used = 0
            state.monthly_reset_at = this_month

    async def reset_daily(self, tenant: str) -> None:
        state = await self._ensure_state(tenant)
        state.daily_tokens_used = 0
        state.daily_reset_at = beijing_now().strftime("%Y-%m-%d")
        await self._persist(state)

    async def get_usage(self, tenant: str) -> dict:
        state = await self._ensure_state(tenant)
        self._check_reset(state)
        return {
            "daily_tokens_used": state.daily_tokens_used,
            "daily_tokens_limit": self.daily_limit,
            "monthly_tokens_used": state.monthly_tokens_used,
            "monthly_tokens_limit": self.monthly_limit,
        }

    async def _persist(self, state: QuotaState) -> None:
        async with self.db.session() as session:
            session.add(state)
            await session.commit()
