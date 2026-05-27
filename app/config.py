from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.security.crypto import decrypt_secret


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class RouteRule:
    pattern: str
    provider: str


@dataclass
class ProviderKeyConfig:
    value: str
    allowed_models: list[str] = field(default_factory=list)


@dataclass
class KeyPoolConfig:
    keys: list[ProviderKeyConfig] = field(default_factory=list)
    strategy: str = "round-robin"
    rate_limit: int = 60


@dataclass
class FallbackConfig:
    from_provider: str
    to_provider: str
    model_map: dict[str, str] = field(default_factory=dict)


@dataclass
class RateLimitConfig:
    rpm: int = 60
    tpm: int = 100000


@dataclass
class QuotaConfig:
    daily_tokens: int = 500000
    monthly_tokens: int = 10000000


@dataclass
class TenantConfig:
    name: str
    api_key: str
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    quota: QuotaConfig = field(default_factory=QuotaConfig)


@dataclass
class ModelPricing:
    input: float
    output: float
    cached_input: float = 0.0
    context: str | None = None
    currency: str = "CNY"


@dataclass
class LoggingConfig:
    access_log: str = "data/access.log"
    error_log: str = "data/error.log"
    retention_days: int = 10


@dataclass
class GatewayConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    routes: list[RouteRule] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    provider_base_urls: dict[str, str] = field(default_factory=dict)
    keypools: dict[str, KeyPoolConfig] = field(default_factory=dict)
    fallbacks: list[FallbackConfig] = field(default_factory=list)
    tenants: list[TenantConfig] = field(default_factory=list)
    pricing: dict[str, dict[str, ModelPricing]] = field(default_factory=dict)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


_ENV_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}$")


def _load_dotenv(start: Path) -> None:
    for directory in [start, *start.parents]:
        dotenv_path = directory / ".env"
        if not dotenv_path.exists():
            continue
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        return


def _resolve_env_value(value):
    if isinstance(value, str):
        match = _ENV_PATTERN.match(value.strip())
        if not match:
            return value
        name, default = match.groups()
        return os.environ.get(name, default if default is not None else value)
    if isinstance(value, list):
        return [_resolve_env_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_env_value(item) for key, item in value.items()}
    return value


def load_config(path: str) -> GatewayConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    _load_dotenv(config_path.resolve().parent)

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}
    raw = _resolve_env_value(raw)

    return _parse_config(raw)


def _parse_config(raw: dict) -> GatewayConfig:
    server_raw = raw.get("server", {})
    server = ServerConfig(
        host=server_raw.get("host", "0.0.0.0"),
        port=int(os.environ.get("GATEWAY_PORT", server_raw.get("port", 8000))),
    )

    routes = [
        RouteRule(pattern=r["pattern"], provider=r["provider"])
        for r in raw.get("routes", [])
    ]

    aliases = raw.get("aliases", {})
    provider_base_urls = raw.get("provider_base_urls", {})

    def parse_provider_key(item) -> ProviderKeyConfig:
        if isinstance(item, str):
            return ProviderKeyConfig(value=decrypt_secret(item))
        if isinstance(item, dict):
            return ProviderKeyConfig(
                value=decrypt_secret(str(item.get("value", ""))),
                allowed_models=list(item.get("allowed_models", []) or []),
            )
        return ProviderKeyConfig(value=str(item))

    keypools = {
        name: KeyPoolConfig(
            keys=[parse_provider_key(item) for item in kp.get("keys", [])],
            strategy=kp.get("strategy", "round-robin"),
            rate_limit=kp.get("rate_limit", 60),
        )
        for name, kp in raw.get("keypools", {}).items()
    }

    fallbacks = [
        FallbackConfig(
            from_provider=fb["from"],
            to_provider=fb["to"],
            model_map=fb.get("model_map", {}),
        )
        for fb in raw.get("fallbacks", [])
    ]

    tenants = [
        TenantConfig(
            name=t["name"],
            api_key=decrypt_secret(t["api_key"]),
            rate_limit=RateLimitConfig(**t.get("rate_limit", {})),
            quota=QuotaConfig(**t.get("quota", {})),
        )
        for t in raw.get("tenants", [])
    ]

    pricing = {}
    for provider, models in raw.get("pricing", {}).items():
        pricing[provider] = {
            model: ModelPricing(
                input=float((prices or {}).get("input", 0)),
                output=float((prices or {}).get("output", 0)),
                cached_input=float((prices or {}).get("cached_input", 0) or 0),
                context=(prices or {}).get("context"),
                currency=str((prices or {}).get("currency", "CNY") or "CNY"),
            )
            for model, prices in models.items()
        }

    logging_raw = raw.get("logging", {})
    logging_cfg = LoggingConfig(
        access_log=logging_raw.get("access_log", "data/access.log"),
        error_log=logging_raw.get("error_log", "data/error.log"),
        retention_days=logging_raw.get("retention_days", 10),
    )

    return GatewayConfig(
        server=server,
        routes=routes,
        aliases=aliases,
        provider_base_urls=provider_base_urls,
        keypools=keypools,
        fallbacks=fallbacks,
        tenants=tenants,
        pricing=pricing,
        logging=logging_cfg,
    )
