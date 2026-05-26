from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field


class KeyStatus(enum.Enum):
    ACTIVE = "active"
    RATE_LIMITED = "rate_limited"
    UNHEALTHY = "unhealthy"
    INVALID = "invalid"


@dataclass
class KeyHealth:
    key_suffix: str
    status: KeyStatus = KeyStatus.ACTIVE
    cooldown_until: float = 0.0
    use_count: int = 0
    last_used_at: float = 0.0
    cooldown_seconds: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if self.cooldown_seconds > 0:
            self.cooldown_until = time.monotonic() + self.cooldown_seconds

    def is_available(self) -> bool:
        if self.status == KeyStatus.INVALID:
            return False
        if self.status in (KeyStatus.RATE_LIMITED, KeyStatus.UNHEALTHY):
            return time.monotonic() >= self.cooldown_until
        return True

    def mark(self, status: KeyStatus, cooldown_seconds: int = 0) -> None:
        self.status = status
        if cooldown_seconds > 0:
            self.cooldown_until = time.monotonic() + cooldown_seconds
        else:
            self.cooldown_until = 0.0
