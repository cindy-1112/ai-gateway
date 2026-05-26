from __future__ import annotations

from dataclasses import dataclass
from app.config import TenantConfig


@dataclass
class Tenant:
    name: str
    api_key: str
    rate_limit_rpm: int
    rate_limit_tpm: int
    daily_token_limit: int
    monthly_token_limit: int

    @classmethod
    def from_config(cls, config: TenantConfig) -> Tenant:
        return cls(
            name=config.name,
            api_key=config.api_key,
            rate_limit_rpm=config.rate_limit.rpm,
            rate_limit_tpm=config.rate_limit.tpm,
            daily_token_limit=config.quota.daily_tokens,
            monthly_token_limit=config.quota.monthly_tokens,
        )
