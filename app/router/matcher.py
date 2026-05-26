from __future__ import annotations

from app.config import RouteRule
from app.router.rules import match_pattern


class RouterMatcher:
    def __init__(
        self,
        routes: list[RouteRule],
        aliases: dict[str, str],
        strict: bool = False,
    ):
        self.routes = routes
        self.aliases = aliases
        self.strict = strict

    def match(self, model: str) -> tuple[str, str]:
        resolved = self.aliases.get(model, model)

        for rule in self.routes:
            if match_pattern(rule.pattern, resolved):
                return rule.provider, resolved

        if self.strict:
            raise ValueError(f"No route found for model: {model}")

        last = self.routes[-1] if self.routes else None
        if last and last.pattern == "*":
            return last.provider, resolved

        raise ValueError(f"No route found for model: {model}")
