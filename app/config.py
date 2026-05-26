from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class RouteRule:
    pattern: str
    provider: str


@dataclass
class KeyPoolConfig:
    keys: list[str] = field(default_factory=list)
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


def load_config(path: str) -> GatewayConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

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

    keypools = {
        name: KeyPoolConfig(
            keys=kp.get("keys", []),
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
            api_key=t["api_key"],
            rate_limit=RateLimitConfig(**t.get("rate_limit", {})),
            quota=QuotaConfig(**t.get("quota", {})),
        )
        for t in raw.get("tenants", [])
    ]

    pricing = {}
    for provider, models in raw.get("pricing", {}).items():
        pricing[provider] = {
            model: ModelPricing(**prices) for model, prices in models.items()
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
