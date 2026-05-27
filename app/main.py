from __future__ import annotations

import os
import asyncio
import hashlib
import json
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import GatewayConfig
from app.auth.middleware import AuthMiddleware
from app.router.matcher import RouterMatcher
from app.ratelimit.limiter import TokenBucketLimiter
from app.ratelimit.quota import QuotaManager
from app.keypool.pool import KeyPool
from app.keypool.health import KeyStatus
from app.providers.openai import OpenAIProvider
from app.providers.anthropic import AnthropicProvider
from app.providers.deepseek import DeepSeekProvider
from app.providers.qwen import QwenProvider
from app.providers.wenxin import WenxinProvider
from app.providers.zhipu import ZhipuProvider
from app.providers.base import BaseProvider
from app.logging.access import AccessLogger
from app.logging.usage import UsageEventBroker, UsageLogger
from app.db.database import Database
from app.models.request import ChatRequest
from app.security.crypto import SecretCryptoError, encrypt_secret, is_encrypted_secret
from app.time_utils import beijing_month_start, beijing_now, beijing_today_start, iso_beijing, parse_beijing_datetime


ROLE_MODULES = {
    "admin": ["keys", "usage", "logs", "audit", "users", "config"],
    "operator": ["keys", "usage", "logs"],
    "user": ["usage", "logs"],
}
VALID_MODULES = ["keys", "usage", "logs", "audit", "users", "config"]
SECRET_MASK = "****"
ENV_PLACEHOLDER_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*(?::-[^}]*)?\}$")
KNOWN_PROVIDER_NAMES = {
    "openai",
    "anthropic",
    "deepseek",
    "qwen",
    "wenxin",
    "zhipu",
    "volcengine",
    "openrouter",
    "cmecloud",
    "psydo",
    "gemini",
}


def _encrypt_config_secret(value):
    if not isinstance(value, str):
        return value
    if not value or value == SECRET_MASK or is_encrypted_secret(value) or ENV_PLACEHOLDER_RE.match(value):
        return value
    return encrypt_secret(value)


def _encrypt_config_secrets(raw_config: dict) -> None:
    for keypool in raw_config.get("keypools", {}).values():
        encrypted_keys = []
        for item in keypool.get("keys", []):
            if isinstance(item, dict):
                item["value"] = _encrypt_config_secret(item.get("value", ""))
                encrypted_keys.append(item)
            else:
                encrypted_keys.append(_encrypt_config_secret(item))
        keypool["keys"] = encrypted_keys

    for tenant in raw_config.get("tenants", []):
        tenant["api_key"] = _encrypt_config_secret(tenant.get("api_key", ""))


def _is_exact_model_pattern(pattern: str) -> bool:
    return bool(pattern) and "*" not in pattern


def _validate_config_integrity(raw_config: dict) -> list[str]:
    errors: list[str] = []
    keypools = raw_config.get("keypools") or {}
    provider_base_urls = raw_config.get("provider_base_urls") or {}
    aliases = raw_config.get("aliases") or {}
    routes = raw_config.get("routes") or []
    pricing = raw_config.get("pricing") or {}

    known_providers = set(KNOWN_PROVIDER_NAMES)
    known_providers.update(keypools.keys())
    known_providers.update(provider_base_urls.keys())
    known_providers.update(pricing.keys())

    known_models = set(aliases.keys())
    known_models.update(str(value) for value in aliases.values() if value)
    for route in routes:
        pattern = str(route.get("pattern", "")).strip()
        if _is_exact_model_pattern(pattern):
            known_models.add(pattern)
    for models in pricing.values():
        if isinstance(models, dict):
            known_models.update(models.keys())

    for index, route in enumerate(routes, start=1):
        provider = str(route.get("provider", "")).strip()
        pattern = str(route.get("pattern", "")).strip()
        if not pattern:
            errors.append(f"第 {index} 条路由缺少 Pattern")
        if not provider:
            errors.append(f"第 {index} 条路由缺少 Provider")
        elif provider not in known_providers:
            errors.append(f"第 {index} 条路由指向不存在的 Provider：{provider}")

    for alias, model in aliases.items():
        if not str(alias).strip():
            errors.append("存在空的模型别名")
        if not str(model).strip():
            errors.append(f"模型别名 {alias} 缺少实际模型")

    for provider, keypool in keypools.items():
        if provider not in known_providers:
            errors.append(f"密钥池 Provider 不存在：{provider}")
        keys = keypool.get("keys") or []
        for index, item in enumerate(keys, start=1):
            if not isinstance(item, dict):
                continue
            allowed_models = item.get("allowed_models") or []
            for model in allowed_models:
                if model not in known_models:
                    errors.append(f"{provider} 第 {index} 个 Key 绑定了不存在的模型：{model}")

    return errors


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    ).hex()
    return f"pbkdf2_sha256$120000${salt}${digest}"


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt, expected = password_hash.split("$", 3)
        iterations = int(iterations_raw)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return secrets.compare_digest(digest, expected)


def _password_strength_error(password: str) -> str | None:
    if len(password) < 10:
        return "密码至少需要 10 位"
    if re.search(r"\s", password):
        return "密码不能包含空格"
    if not re.search(r"[A-Za-z]", password):
        return "密码必须包含至少 1 个字母"
    if not re.search(r"\d", password):
        return "密码必须包含至少 1 个数字"
    return None


def _require_strong_password(password: str) -> None:
    error = _password_strength_error(password)
    if error:
        raise HTTPException(status_code=400, detail=error)


def _admin_user_payload(user) -> dict:
    module_overrides = getattr(user, "module_overrides", None)
    modules = module_overrides if module_overrides is not None else ROLE_MODULES.get(user.role, ROLE_MODULES["user"])
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "tenant": user.tenant,
        "email": user.email,
        "phone": user.phone,
        "modules": modules,
        "module_overrides": module_overrides,
    }


def _model_request_payload(request) -> dict:
    return {
        "id": request.id,
        "user_id": request.user_id,
        "username": request.username,
        "alias": request.alias,
        "model": request.model,
        "provider": request.provider,
        "type": request.request_type,
        "status": request.status,
        "created_at": iso_beijing(request.created_at),
        "reviewed_at": iso_beijing(request.reviewed_at),
    }


def _registration_request_payload(request) -> dict:
    return {
        "id": request.id,
        "channel": request.channel,
        "target": request.target,
        "reason": request.reason,
        "status": request.status,
        "invite_code": request.invite_code,
        "created_at": iso_beijing(request.created_at),
        "reviewed_at": iso_beijing(request.reviewed_at),
        "registered_at": iso_beijing(request.registered_at),
        "reviewer_id": request.reviewer_id,
        "registered_user_id": request.registered_user_id,
    }


def _registration_request_public_payload(request) -> dict:
    payload = _registration_request_payload(request)
    payload["invite_code"] = None
    return payload


def _model_binding_payload(binding) -> dict | None:
    if binding is None:
        return None
    return {
        "id": binding.id,
        "user_id": binding.user_id,
        "username": binding.username,
        "alias": binding.alias,
        "model": binding.model,
        "provider": binding.provider,
        "status": binding.status,
        "created_at": iso_beijing(binding.created_at),
        "updated_at": iso_beijing(binding.updated_at),
        "stopped_at": iso_beijing(binding.stopped_at),
        "resumed_at": iso_beijing(binding.resumed_at),
    }


def _user_api_key_payload(key) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "note": key.note,
        "key_prefix": key.key_prefix,
        "status": key.status,
        "created_at": iso_beijing(key.created_at),
        "expires_at": iso_beijing(key.expires_at),
        "last_used_at": iso_beijing(key.last_used_at),
        "last_used_ip": key.last_used_ip,
        "revoked_at": iso_beijing(key.revoked_at),
    }


def _parse_optional_datetime(value) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return parse_beijing_datetime(raw, date_as_end=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid expires_at") from exc


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded_for:
        return forwarded_for[:64]
    return request.client.host[:64] if request.client else None


def _audit_log_payload(log) -> dict:
    return {
        "id": log.id,
        "actor_id": log.actor_id,
        "actor_name": log.actor_name,
        "actor_role": log.actor_role,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "target_name": log.target_name,
        "status": log.status,
        "detail": log.detail,
        "created_at": iso_beijing(log.created_at),
    }


def _request_log_payload(log) -> dict:
    return {
        "timestamp": iso_beijing(log.created_at),
        "request_id": log.request_id,
        "tenant": log.tenant,
        "user_id": log.user_id,
        "username": log.username,
        "user_api_key_prefix": log.user_api_key_prefix,
        "model_binding_id": log.model_binding_id,
        "model": log.model,
        "provider": log.provider,
        "api_key_suffix": log.api_key_suffix,
        "status": log.status,
        "latency_ms": log.latency_ms,
        "prompt_tokens": log.prompt_tokens,
        "completion_tokens": log.completion_tokens,
        "total_tokens": log.total_tokens,
        "stream": bool(log.stream),
    }


def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _generate_user_api_key() -> str:
    return "ugk_live_" + secrets.token_urlsafe(32)


async def _attach_module_overrides(session, user):
    from sqlalchemy import select
    from app.db.models import AdminUserModulePermission

    result = await session.execute(
        select(AdminUserModulePermission.module)
        .where(AdminUserModulePermission.user_id == user.id)
    )
    found = set(result.scalars().all())
    modules = [module for module in VALID_MODULES if module in found]
    user.module_overrides = modules if modules else None
    return user


async def _ensure_default_admin(db: Database) -> None:
    from sqlalchemy import select
    from app.db.models import AdminUser

    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(24)

    async with db.session() as session:
        existing = await session.execute(
            select(AdminUser).where(AdminUser.username == username)
        )
        if existing.scalars().first():
            return
        session.add(
            AdminUser(
                username=username,
                password_hash=_hash_password(password),
                role="admin",
                tenant="default",
            )
        )
        await session.commit()
        if "ADMIN_PASSWORD" not in os.environ:
            print(
                "Generated initial admin password. "
                "Set ADMIN_PASSWORD before first startup in shared environments. "
                f"username={username} password={password}"
            )


async def _create_admin_session(db: Database, user_id: int) -> str:
    from app.db.models import AdminSession

    session_id = secrets.token_urlsafe(32)
    expires_at = beijing_now() + timedelta(days=7)
    async with db.session() as session:
        session.add(
            AdminSession(
                session_id=session_id,
                user_id=user_id,
                expires_at=expires_at,
            )
        )
        await session.commit()
    return session_id


async def _get_admin_user_from_request(db: Database, request: Request):
    from sqlalchemy import select
    from app.db.models import AdminSession, AdminUser

    session_id = request.cookies.get("gw_admin_session")
    if not session_id:
        return None

    now = beijing_now()
    async with db.session() as session:
        session_result = await session.execute(
            select(AdminSession).where(
                AdminSession.session_id == session_id,
                AdminSession.revoked_at.is_(None),
                AdminSession.expires_at >= now,
            )
        )
        admin_session = session_result.scalars().first()
        if admin_session is None:
            return None

        user_result = await session.execute(
            select(AdminUser).where(AdminUser.id == admin_session.user_id)
        )
        user = user_result.scalars().first()
        if user is not None:
            await _attach_module_overrides(session, user)
        return user


def create_app(config: GatewayConfig) -> FastAPI:
    db_url = os.environ.get(
        "TEST_DB_URL", "sqlite+aiosqlite:///data/gateway.db"
    )
    db = Database(db_url)

    auth = AuthMiddleware(config.tenants)
    router = RouterMatcher(config.routes, config.aliases)

    rate_limiters: dict[str, dict[str, TokenBucketLimiter]] = {}
    for t in config.tenants:
        rate_limiters[t.name] = {
            "rpm": TokenBucketLimiter(
                capacity=t.rate_limit.rpm, refill_rate=t.rate_limit.rpm / 60.0
            ),
            "tpm": TokenBucketLimiter(
                capacity=t.rate_limit.tpm, refill_rate=t.rate_limit.tpm / 60.0
            ),
        }

    key_pools: dict[str, KeyPool] = {
        name: KeyPool(
            provider=name, keys=kp.keys, strategy=kp.strategy
        )
        for name, kp in config.keypools.items()
    }

    default_base_urls = {
        "openai": "https://api.openai.com",
        "anthropic": "https://api.anthropic.com",
        "deepseek": "https://api.deepseek.com",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode",
        "wenxin": "https://aip.baidubce.com",
        "zhipu": "https://open.bigmodel.cn",
        "volcengine": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "openrouter": "https://openrouter.ai/api/v1",
        "cmecloud": "https://zhenze-huhehaote.cmecloud.cn/api/coding/v1",
        "psydo": "https://api.psydo.top/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    }

    def provider_url(name: str) -> str:
        return config.provider_base_urls.get(name, default_base_urls[name])

    providers: dict[str, BaseProvider] = {
        "openai": OpenAIProvider(base_url=provider_url("openai")),
        "anthropic": AnthropicProvider(base_url=provider_url("anthropic")),
        "deepseek": DeepSeekProvider(base_url=provider_url("deepseek")),
        "qwen": QwenProvider(base_url=provider_url("qwen")),
        "wenxin": WenxinProvider(base_url=provider_url("wenxin")),
        "zhipu": ZhipuProvider(base_url=provider_url("zhipu")),
        "volcengine": OpenAIProvider(base_url=provider_url("volcengine")),
        "openrouter": OpenAIProvider(base_url=provider_url("openrouter")),
        "cmecloud": OpenAIProvider(base_url=provider_url("cmecloud")),
        "psydo": OpenAIProvider(base_url=provider_url("psydo")),
        "gemini": OpenAIProvider(base_url=provider_url("gemini")),
    }

    quota_managers: dict[str, QuotaManager] = {
        t.name: QuotaManager(
            db,
            daily_limit=t.quota.daily_tokens,
            monthly_limit=t.quota.monthly_tokens,
        )
        for t in config.tenants
    }
    os.makedirs(os.path.dirname(config.logging.access_log) or ".", exist_ok=True)
    access_logger = AccessLogger(config.logging.access_log, db)
    usage_events = UsageEventBroker()
    usage_logger = UsageLogger(db, usage_events)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        os.makedirs("data", exist_ok=True)
        await db.init()
        await _ensure_default_admin(db)
        yield
        access_logger.close()
        for p in providers.values():
            await p.close()
        await db.close()

    app = FastAPI(lifespan=lifespan)
    app.state.access_logger = access_logger
    app.state.usage_logger = usage_logger
    app.state.usage_events = usage_events
    app.state.quota_managers = quota_managers

    async def authenticate_bearer_token(token: str, request: Request | None = None) -> dict:
        from sqlalchemy import select
        from app.db.models import UserApiKey, AdminUser

        async with db.session() as session:
            key_result = await session.execute(
                select(UserApiKey).where(UserApiKey.key_hash == _hash_api_key(token))
            )
            user_key = key_result.scalars().first()
            if user_key is not None:
                if user_key.status != "active":
                    raise PermissionError("User API key is revoked")
                if user_key.expires_at and user_key.expires_at < beijing_now():
                    raise PermissionError("User API key is expired")
                user_result = await session.execute(
                    select(AdminUser).where(AdminUser.id == user_key.user_id)
                )
                user = user_result.scalars().first()
                if user is None:
                    raise PermissionError("User not found")
                tenant = next((item for item in config.tenants if item.name == user_key.tenant), None)
                if tenant is None:
                    raise PermissionError("Tenant not found for user API key")
                user_key.last_used_at = beijing_now()
                user_key.last_used_ip = _client_ip(request)
                await session.commit()
                return {
                    "kind": "user_api_key",
                    "principal": user,
                    "tenant": tenant,
                    "api_key_prefix": user_key.key_prefix,
                }

        return {"kind": "tenant_key", "principal": auth.authenticate(token)}

    async def require_admin_or_bearer(request: Request):
        admin_user = await _get_admin_user_from_request(db, request)
        if admin_user is not None:
            return {"kind": "admin_user", "principal": admin_user}

        auth_header = request.headers.get("authorization", "")
        try:
            token = auth.extract_bearer(auth_header)
            return await authenticate_bearer_token(token, request)
        except PermissionError as e:
            raise HTTPException(status_code=401, detail=str(e))

    async def write_audit_log(
        actor,
        *,
        action: str,
        target_type: str,
        target_id: str | int | None = None,
        target_name: str | None = None,
        status: str = "success",
        detail: str | None = None,
    ) -> None:
        from app.db.models import AuditLog

        log = AuditLog(
            actor_id=getattr(actor, "id", None),
            actor_name=getattr(actor, "username", "system") or "system",
            actor_role=getattr(actor, "role", "system") or "system",
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            target_name=target_name,
            status=status,
            detail=detail[:1024] if detail else None,
        )
        async with db.session() as session:
            session.add(log)
            await session.commit()

    def tenant_filter_for_principal(principal_info) -> str | None:
        if principal_info["kind"] != "admin_user":
            return None
        user = principal_info["principal"]
        if user.role in {"admin", "operator"}:
            return None
        return user.tenant

    def require_module_access(principal_info, module: str) -> None:
        if principal_info["kind"] == "tenant_key":
            return
        user = principal_info["principal"]
        modules = getattr(user, "module_overrides", None)
        if modules is None:
            modules = ROLE_MODULES.get(user.role, ROLE_MODULES["user"])
        if module not in modules:
            raise HTTPException(status_code=403, detail="Module access denied")

    def require_config_admin(principal_info) -> None:
        if principal_info["kind"] == "tenant_key":
            return
        user = principal_info["principal"]
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

    def resolve_available_model(alias: str) -> dict:
        if alias not in config.aliases:
            raise HTTPException(status_code=404, detail="Model alias not found")
        try:
            provider, resolved_model = router.match(alias)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        pool = key_pools.get(provider)
        has_keys = provider in config.keypools and bool(config.keypools[provider].keys)
        if not (provider and provider in providers and pool and has_keys):
            raise HTTPException(status_code=400, detail="Model is not available")
        return {"alias": alias, "model": resolved_model, "provider": provider}

    async def current_user_model_binding(session, user_id: int):
        from sqlalchemy import select
        from app.db.models import UserModelBinding

        result = await session.execute(
            select(UserModelBinding).where(UserModelBinding.user_id == user_id)
        )
        return result.scalars().first()

    async def enforce_user_model_access(user, requested_model: str, resolved_model: str):
        if user is None or user.role != "user":
            return None
        async with db.session() as session:
            binding = await current_user_model_binding(session, user.id)
        if binding is None:
            raise HTTPException(status_code=403, detail="No approved model binding")
        if binding.status != "active":
            raise HTTPException(status_code=403, detail="Model binding is stopped by administrator")
        if requested_model not in {binding.alias, binding.model} and resolved_model != binding.model:
            raise HTTPException(status_code=403, detail="Requested model is not bound to this user")
        return binding

    async def enforce_admin_user_model_access(request: Request, requested_model: str, resolved_model: str) -> None:
        user = await _get_admin_user_from_request(db, request)
        await enforce_user_model_access(user, requested_model, resolved_model)

    def extract_test_message(data: dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        first = choices[0] or {}
        message = first.get("message") or {}
        content = message.get("content")
        if content is not None:
            return str(content)
        text = first.get("text")
        return str(text) if text is not None else ""

    def build_model_test_failure(
        *,
        provider_name: str,
        model: str,
        key_suffix: str,
        start_time: float,
        error: Exception,
    ) -> dict:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
        error_detail = str(error)
        if response is not None:
            try:
                payload = response.json()
                error_detail = payload.get("error", {}).get("message") or payload.get("detail") or str(payload)
            except Exception:
                error_detail = getattr(response, "text", "") or str(error)
        return {
            "status": "failed",
            "provider": provider_name,
            "model": model,
            "key_suffix": key_suffix,
            "latency_ms": int((time.monotonic() - start_time) * 1000),
            "status_code": status_code,
            "error": error_detail[:1000],
            "suggestion": "请检查 Base URL、模型名、Key 权限、额度或上游服务状态。",
            "tested_at": iso_beijing(beijing_now()),
        }

    def select_model_test_key(provider_name: str, key_suffix: str | None) -> str:
        keypool_cfg = config.keypools.get(provider_name)
        if keypool_cfg is None or not keypool_cfg.keys:
            raise HTTPException(status_code=400, detail="Provider has no configured keys")
        if not key_suffix or key_suffix == "auto":
            pool = key_pools.get(provider_name)
            if pool is None:
                raise HTTPException(status_code=400, detail="Provider key pool is not available")
            return pool.select(model=None)
        for item in keypool_cfg.keys:
            if item.value.endswith(key_suffix):
                return item.value
        raise HTTPException(status_code=404, detail="Selected key was not found")

    async def run_model_test_call(
        *,
        provider_name: str,
        model: str,
        api_key: str,
        prompt: str,
    ) -> dict:
        provider = providers.get(provider_name)
        if provider is None:
            raise HTTPException(status_code=400, detail="Unknown provider")
        chat_req = ChatRequest.from_dict({
            "model": model,
            "messages": [{"role": "user", "content": prompt or "你好，请回复 ok"}],
            "max_tokens": 64,
        })
        start_time = time.monotonic()
        key_suffix = api_key[-4:] if len(api_key) >= 4 else api_key
        try:
            response = await provider.chat(chat_req, api_key)
            data = response.json()
        except Exception as error:
            return build_model_test_failure(
                provider_name=provider_name,
                model=model,
                key_suffix=key_suffix,
                start_time=start_time,
                error=error,
            )
        usage = data.get("usage", {}) or {}
        return {
            "status": "success",
            "provider": provider_name,
            "model": model,
            "key_suffix": key_suffix,
            "latency_ms": int((time.monotonic() - start_time) * 1000),
            "content": extract_test_message(data),
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
                "completion_tokens": usage.get("completion_tokens", 0) or 0,
                "total_tokens": usage.get("total_tokens", 0) or 0,
            },
            "tested_at": iso_beijing(beijing_now()),
        }

    def extract_cached_input_tokens(usage: dict) -> int:
        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            for key in ("cached_tokens", "cached_input_tokens"):
                value = prompt_details.get(key)
                if isinstance(value, int | float):
                    return int(value)
        for key in ("cache_read_input_tokens", "cached_input_tokens"):
            value = usage.get(key)
            if isinstance(value, int | float):
                return int(value)
        return 0

    def calculate_usage_cost(
        *,
        provider_name: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_input_tokens: int,
    ) -> tuple[float, int, str]:
        cached_input_tokens = max(0, min(cached_input_tokens, prompt_tokens))
        uncached_input_tokens = max(prompt_tokens - cached_input_tokens, 0)
        model_pricing = config.pricing.get(provider_name, {}).get(model)
        if not model_pricing:
            return 0.0, uncached_input_tokens, "CNY"
        cached_price = model_pricing.cached_input or model_pricing.input
        cost = (
            uncached_input_tokens * model_pricing.input
            + cached_input_tokens * cached_price
            + completion_tokens * model_pricing.output
        ) / 1_000_000
        return cost, uncached_input_tokens, model_pricing.currency or "CNY"

    async def record_usage_if_present(
        *,
        request_id: str,
        tenant_name: str,
        model: str,
        provider_name: str,
        usage: dict | None,
        user_id: int | None = None,
        username: str | None = None,
        api_key_prefix: str | None = None,
        model_binding_id: int | None = None,
    ) -> dict | None:
        usage = usage or {}
        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        completion_tokens = usage.get("completion_tokens", 0) or 0
        total_tokens = usage.get("total_tokens", 0) or 0
        if total_tokens <= 0:
            return None

        cached_input_tokens = extract_cached_input_tokens(usage)
        cost, uncached_input_tokens, cost_currency = calculate_usage_cost(
            provider_name=provider_name,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_input_tokens=cached_input_tokens,
        )
        return await app.state.usage_logger.record(
            request_id=request_id,
            tenant=tenant_name,
            model=model,
            provider=provider_name,
            prompt_tokens=prompt_tokens,
            uncached_input_tokens=uncached_input_tokens,
            cached_input_tokens=max(0, min(cached_input_tokens, prompt_tokens)),
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            cost_currency=cost_currency,
            user_id=user_id,
            username=username,
            api_key_prefix=api_key_prefix,
            model_binding_id=model_binding_id,
        )

    def extract_usage_from_sse_text(text: str) -> dict | None:
        usage: dict | None = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and isinstance(data.get("usage"), dict):
                usage = data["usage"]
        return usage

    async def stream_with_usage_recording(
        response,
        *,
        request_id: str,
        tenant_name: str,
        model: str,
        provider_name: str,
        user_id: int | None = None,
        username: str | None = None,
        api_key_prefix: str | None = None,
        model_binding_id: int | None = None,
    ):
        tail = ""
        latest_usage: dict | None = None
        async for chunk in response.aiter_bytes():
            yield chunk
            text = tail + chunk.decode("utf-8", errors="ignore")
            latest_usage = extract_usage_from_sse_text(text) or latest_usage
            tail = text[-4096:]

        await record_usage_if_present(
            request_id=request_id,
            tenant_name=tenant_name,
            model=model,
            provider_name=provider_name,
            usage=latest_usage,
            user_id=user_id,
            username=username,
            api_key_prefix=api_key_prefix,
            model_binding_id=model_binding_id,
        )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        try:
            await db.init()
        except Exception:
            raise HTTPException(status_code=503, detail="Database unavailable")
        available = sum(1 for p in key_pools.values() if p.get_status()["available"] > 0)
        if available == 0 and len(key_pools) > 0:
            raise HTTPException(status_code=503, detail="No available key pools")
        return {"status": "ready"}

    @app.get("/v1/usage")
    async def get_usage(request: Request):
        auth_header = request.headers.get("authorization", "")
        try:
            token = auth.extract_bearer(auth_header)
            bearer_info = await authenticate_bearer_token(token, request)
            tenant = bearer_info["tenant"] if bearer_info["kind"] == "user_api_key" else bearer_info["principal"]
        except PermissionError as e:
            raise HTTPException(status_code=401, detail=str(e))

        quota_mgr = app.state.quota_managers.get(tenant.name)
        if quota_mgr is None:
            raise HTTPException(status_code=404, detail="Tenant not found")
        return await quota_mgr.get_usage(tenant.name)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        request_id = f"req-{uuid.uuid4().hex[:12]}"
        start_time = time.monotonic()

        auth_header = request.headers.get("authorization", "")
        try:
            token = auth.extract_bearer(auth_header)
            bearer_info = await authenticate_bearer_token(token, request)
            if bearer_info["kind"] == "user_api_key":
                tenant = bearer_info["tenant"]
                api_user = bearer_info["principal"]
            else:
                tenant = bearer_info["principal"]
                api_user = None
        except PermissionError as e:
            raise HTTPException(status_code=401, detail=str(e))

        body = await request.json()
        try:
            chat_req = ChatRequest.from_dict(body)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        provider_name, resolved_model = router.match(chat_req.model)
        user_binding = None
        if api_user is not None:
            user_binding = await enforce_user_model_access(api_user, chat_req.model, resolved_model)
        else:
            await enforce_admin_user_model_access(request, chat_req.model, resolved_model)
        chat_req.model = resolved_model

        # Rate limiting
        tenant_limiters = rate_limiters.get(tenant.name, {})
        rpm_limiter = tenant_limiters.get("rpm")
        tpm_limiter = tenant_limiters.get("tpm")

        if rpm_limiter and not rpm_limiter.allow():
            retry_after = rpm_limiter.retry_after_seconds()
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded (rpm)",
                headers={"Retry-After": str(int(retry_after))},
            )

        estimated_tokens = sum(len(m.content) for m in chat_req.messages) // 4
        if tpm_limiter and not tpm_limiter.allow_n(estimated_tokens):
            retry_after = tpm_limiter.retry_after_seconds()
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded (tpm)",
                headers={"Retry-After": str(int(retry_after))},
            )

        # Quota check
        quota_mgr = app.state.quota_managers.get(tenant.name)
        if quota_mgr:
            if not await quota_mgr.check_and_reserve(tenant.name, estimated_tokens):
                raise HTTPException(status_code=429, detail="Quota exceeded")

        # Key selection
        pool = key_pools.get(provider_name)
        if not pool:
            raise HTTPException(status_code=503, detail=f"No key pool for provider: {provider_name}")

        try:
            api_key = pool.select(model=chat_req.model)
        except RuntimeError as e:
            # Try fallback
            resolved = False
            for fb in config.fallbacks:
                if fb.from_provider == provider_name:
                    fallback_pool = key_pools.get(fb.to_provider)
                    if fallback_pool:
                        try:
                            mapped_model = fb.model_map.get(chat_req.model, chat_req.model)
                            api_key = fallback_pool.select(model=mapped_model)
                            chat_req.model = mapped_model
                            provider_name = fb.to_provider
                            pool = fallback_pool
                            resolved = True
                            break
                        except RuntimeError:
                            continue
            if not resolved:
                raise HTTPException(status_code=503, detail=str(e))

        key_suffix = api_key[-4:] if len(api_key) >= 4 else api_key

        # Provider call
        provider = providers.get(provider_name)
        if not provider:
            raise HTTPException(status_code=503, detail=f"Unknown provider: {provider_name}")

        try:
            response = await provider.chat(chat_req, api_key)
        except Exception as e:
            status_code = getattr(e, "status_code", 500)
            if status_code == 401 or status_code == 403:
                pool.mark_status(api_key, KeyStatus.INVALID)
            elif status_code == 429:
                pool.mark_status(api_key, KeyStatus.RATE_LIMITED, cooldown_seconds=60)
            elif status_code >= 500:
                pool.mark_status(api_key, KeyStatus.UNHEALTHY, cooldown_seconds=10)

            latency_ms = int((time.monotonic() - start_time) * 1000)
            await app.state.access_logger.log(
                request_id=request_id, tenant=tenant.name, model=chat_req.model,
                provider=provider_name, api_key_suffix=key_suffix,
                status=status_code, latency_ms=latency_ms,
                prompt_tokens=0, completion_tokens=0, total_tokens=0,
                stream=chat_req.stream,
                user_id=api_user.id if api_user is not None else None,
                username=api_user.username if api_user is not None else None,
                user_api_key_prefix=bearer_info.get("api_key_prefix") if bearer_info["kind"] == "user_api_key" else None,
                model_binding_id=user_binding.id if user_binding is not None else None,
            )
            raise HTTPException(status_code=502, detail=str(e))

        latency_ms = int((time.monotonic() - start_time) * 1000)

        # Log access for successful request
        await app.state.access_logger.log(
            request_id=request_id, tenant=tenant.name, model=chat_req.model,
            provider=provider_name, api_key_suffix=key_suffix,
            status=200, latency_ms=latency_ms,
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            stream=chat_req.stream,
            user_id=api_user.id if api_user is not None else None,
            username=api_user.username if api_user is not None else None,
            user_api_key_prefix=bearer_info.get("api_key_prefix") if bearer_info["kind"] == "user_api_key" else None,
            model_binding_id=user_binding.id if user_binding is not None else None,
        )

        if chat_req.stream:
            return StreamingResponse(
                stream_with_usage_recording(
                    response,
                    request_id=request_id,
                    tenant_name=tenant.name,
                    model=chat_req.model,
                    provider_name=provider_name,
                    user_id=api_user.id if api_user is not None else None,
                    username=api_user.username if api_user is not None else None,
                    api_key_prefix=bearer_info.get("api_key_prefix") if bearer_info["kind"] == "user_api_key" else None,
                    model_binding_id=user_binding.id if user_binding is not None else None,
                ),
                media_type="text/event-stream",
            )
        else:
            resp_data = response.json()
            await record_usage_if_present(
                request_id=request_id,
                tenant_name=tenant.name,
                model=chat_req.model,
                provider_name=provider_name,
                usage=resp_data.get("usage", {}),
                user_id=api_user.id if api_user is not None else None,
                username=api_user.username if api_user is not None else None,
                api_key_prefix=bearer_info.get("api_key_prefix") if bearer_info["kind"] == "user_api_key" else None,
                model_binding_id=user_binding.id if user_binding is not None else None,
            )
            return JSONResponse(content=resp_data)

    @app.get("/v1/keys/status")
    async def keys_status(request: Request):
        principal_info = await require_admin_or_bearer(request)
        require_module_access(principal_info, "keys")

        result = {}
        for name, pool in key_pools.items():
            result[name] = pool.get_status()
        return result

    @app.get("/v1/usage/summary")
    async def usage_summary(request: Request, period: str = "today"):
        principal_info = await require_admin_or_bearer(request)
        require_module_access(principal_info, "usage")
        tenant_filter = tenant_filter_for_principal(principal_info)

        from datetime import datetime, timedelta
        from sqlalchemy import select
        from app.db.models import UsageRecord

        if period == "today":
            since = beijing_today_start()
        elif period == "week":
            since = beijing_now() - timedelta(days=7)
        elif period == "month":
            since = beijing_month_start()
        else:
            since = beijing_today_start()

        async with db.session() as session:
            user_binding = None
            if principal_info["kind"] == "admin_user" and principal_info["principal"].role == "user":
                user_binding = await current_user_model_binding(session, principal_info["principal"].id)
                if user_binding is None or user_binding.status != "active":
                    return {
                        "total_tokens": 0,
                        "total_cost_usd": 0,
                        "cost_currency": "CNY",
                        "by_model": {},
                        "by_tenant": {},
                        "model_binding": _model_binding_payload(user_binding),
                        "model_binding_required": user_binding is None,
                        "model_binding_stopped": bool(user_binding and user_binding.status == "stopped"),
                    }
            query = select(UsageRecord).where(UsageRecord.created_at >= since)
            if tenant_filter is not None:
                query = query.where(UsageRecord.tenant == tenant_filter)
            if user_binding is not None:
                query = query.where(
                    UsageRecord.user_id == principal_info["principal"].id,
                    UsageRecord.model == user_binding.model,
                )
            records = await session.execute(query)
            rows = records.scalars().all()

        total_tokens = sum(r.total_tokens for r in rows)
        total_cost = sum(r.cost_usd for r in rows)

        by_model: dict[str, dict] = {}
        for r in rows:
            if r.model not in by_model:
                by_model[r.model] = {"tokens": 0, "cost": 0.0, "requests": 0}
            by_model[r.model]["tokens"] += r.total_tokens
            by_model[r.model]["cost"] += r.cost_usd
            by_model[r.model]["requests"] += 1

        by_tenant: dict[str, dict] = {}
        for r in rows:
            if r.tenant not in by_tenant:
                by_tenant[r.tenant] = {"tokens": 0, "cost": 0.0}
            by_tenant[r.tenant]["tokens"] += r.total_tokens
            by_tenant[r.tenant]["cost"] += r.cost_usd

        by_user: dict[str, dict] = {}
        for r in rows:
            if not r.user_id:
                continue
            key = str(r.user_id)
            if key not in by_user:
                by_user[key] = {
                    "username": r.username or f"用户 #{r.user_id}",
                    "tokens": 0,
                    "cost": 0.0,
                    "requests": 0,
                }
            by_user[key]["tokens"] += r.total_tokens
            by_user[key]["cost"] += r.cost_usd
            by_user[key]["requests"] += 1

        return {
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "cost_currency": "CNY",
            "by_model": by_model,
            "by_tenant": by_tenant,
            "by_user": by_user,
            "model_binding": _model_binding_payload(user_binding) if "user_binding" in locals() else None,
        }

    @app.get("/v1/usage/events")
    async def usage_events_stream(request: Request):
        principal_info = await require_admin_or_bearer(request)
        require_module_access(principal_info, "usage")
        tenant_filter = tenant_filter_for_principal(principal_info)
        queue = app.state.usage_events.subscribe()

        async def event_generator():
            try:
                yield ": connected\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
                        continue

                    if tenant_filter is not None and event.get("tenant") != tenant_filter:
                        continue
                    payload = json.dumps(event, ensure_ascii=False)
                    yield f"event: usage_recorded\ndata: {payload}\n\n"
            finally:
                app.state.usage_events.unsubscribe(queue)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/v1/logs")
    async def get_logs(
        request: Request,
        limit: int = 50,
        offset: int = 0,
        status: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        username: str | None = None,
        user_id: int | None = None,
        start: str | None = None,
        end: str | None = None,
    ):
        principal_info = await require_admin_or_bearer(request)
        require_module_access(principal_info, "logs")
        tenant_filter = tenant_filter_for_principal(principal_info)

        from sqlalchemy import desc, func, select
        from app.db.models import RequestLogRecord

        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        conditions = []
        if principal_info["kind"] == "admin_user" and principal_info["principal"].role == "user":
            conditions.append(RequestLogRecord.user_id == principal_info["principal"].id)
        if tenant_filter is not None:
            conditions.append(RequestLogRecord.tenant == tenant_filter)
        if status is not None:
            conditions.append(RequestLogRecord.status == status)
        if provider:
            conditions.append(RequestLogRecord.provider == provider)
        if model:
            conditions.append(RequestLogRecord.model == model)
        if username:
            conditions.append(RequestLogRecord.username.ilike(f"%{username}%"))
        if user_id is not None:
            conditions.append(RequestLogRecord.user_id == user_id)
        if start:
            try:
                conditions.append(RequestLogRecord.created_at >= parse_beijing_datetime(start))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid start time")
        if end:
            try:
                conditions.append(RequestLogRecord.created_at <= parse_beijing_datetime(end))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid end time")

        base_query = select(RequestLogRecord)
        count_query = select(func.count()).select_from(RequestLogRecord)
        for condition in conditions:
            base_query = base_query.where(condition)
            count_query = count_query.where(condition)

        async with db.session() as session:
            total_result = await session.execute(count_query)
            logs_result = await session.execute(
                base_query
                .order_by(desc(RequestLogRecord.created_at), desc(RequestLogRecord.id))
                .offset(offset)
                .limit(limit)
            )

        return {
            "total": total_result.scalar_one(),
            "items": [_request_log_payload(item) for item in logs_result.scalars().all()],
        }

    @app.get("/admin/api/audit-logs")
    async def list_audit_logs(request: Request, limit: int = 100, offset: int = 0):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user":
            raise HTTPException(status_code=403, detail="Admin role required")
        require_module_access(principal_info, "audit")

        from sqlalchemy import desc, func, select
        from app.db.models import AuditLog

        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        async with db.session() as session:
            total_result = await session.execute(select(func.count()).select_from(AuditLog))
            logs_result = await session.execute(
                select(AuditLog)
                .order_by(desc(AuditLog.created_at), desc(AuditLog.id))
                .offset(offset)
                .limit(limit)
            )

        return {
            "total": total_result.scalar_one(),
            "items": [_audit_log_payload(log) for log in logs_result.scalars().all()],
        }

    @app.get("/v1/config")
    async def get_config(request: Request):
        principal_info = await require_admin_or_bearer(request)
        require_module_access(principal_info, "config")

        result = {
            "server": {"host": config.server.host, "port": config.server.port},
            "routes": [{"pattern": r.pattern, "provider": r.provider} for r in config.routes],
            "aliases": config.aliases,
            "provider_base_urls": config.provider_base_urls,
            "keypools": {},
            "fallbacks": [
                {"from": fb.from_provider, "to": fb.to_provider, "model_map": fb.model_map}
                for fb in config.fallbacks
            ],
            "tenants": [
                {
                    "name": t.name,
                    "api_key": "****" if t.api_key else "",
                    "rate_limit": {"rpm": t.rate_limit.rpm, "tpm": t.rate_limit.tpm},
                    "quota": {"daily_tokens": t.quota.daily_tokens, "monthly_tokens": t.quota.monthly_tokens},
                }
                for t in config.tenants
            ],
            "pricing": {
                provider: {model: {"input": p.input, "output": p.output} for model, p in models.items()}
                for provider, models in config.pricing.items()
            },
            "logging": {
                "access_log": config.logging.access_log,
                "error_log": config.logging.error_log,
                "retention_days": config.logging.retention_days,
            },
        }
        for name, kp in config.keypools.items():
            result["keypools"][name] = {
                "keys": [
                    {
                        "value": "****" if key.value else "",
                        "allowed_models": key.allowed_models,
                    }
                    if key.allowed_models
                    else ("****" if key.value else "")
                    for key in kp.keys
                ],
                "strategy": kp.strategy,
                "rate_limit": kp.rate_limit,
            }
        return result

    @app.get("/v1/config/keypools/{provider_name}/keys")
    async def get_keypool_plain_keys(request: Request, provider_name: str):
        principal_info = await require_admin_or_bearer(request)
        require_module_access(principal_info, "config")

        keypool = config.keypools.get(provider_name)
        if keypool is None:
            raise HTTPException(status_code=404, detail="Key pool not found")
        return {
            "provider": provider_name,
            "keys": [
                {
                    "value": key.value,
                    "allowed_models": key.allowed_models,
                }
                if key.allowed_models
                else key.value
                for key in keypool.keys
            ],
        }

    @app.put("/v1/config")
    async def update_config(request: Request):
        principal_info = await require_admin_or_bearer(request)
        require_module_access(principal_info, "config")

        body = await request.json()

        # Merge keys: if value is "****", keep original
        for provider_name, kp_data in body.get("keypools", {}).items():
            original_kp = config.keypools.get(provider_name)
            if original_kp:
                merged_keys = []
                new_keys = kp_data.get("keys", [])
                orig_keys = original_kp.keys
                for i, k in enumerate(new_keys):
                    if isinstance(k, dict):
                        value = k.get("value", "")
                        if value == SECRET_MASK and i < len(orig_keys):
                            k["value"] = orig_keys[i].value
                        merged_keys.append(k)
                    elif k == SECRET_MASK and i < len(orig_keys):
                        original_key = orig_keys[i]
                        if original_key.allowed_models:
                            merged_keys.append({
                                "value": original_key.value,
                                "allowed_models": original_key.allowed_models,
                            })
                        else:
                            merged_keys.append(original_key.value)
                    else:
                        merged_keys.append(k)
                kp_data["keys"] = merged_keys

        # Merge tenant api_keys: if value is "****", keep original
        for i, t_data in enumerate(body.get("tenants", [])):
            if t_data.get("api_key") == SECRET_MASK and i < len(config.tenants):
                t_data["api_key"] = config.tenants[i].api_key

        validation_errors = _validate_config_integrity(body)
        if validation_errors:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "配置校验失败",
                    "errors": validation_errors,
                },
            )

        config_path = os.environ.get("GATEWAY_CONFIG_PATH", "config/gateway.yaml")
        try:
            _encrypt_config_secrets(body)

            # Write to gateway.yaml
            import yaml as _yaml
            with open(config_path, "w", encoding="utf-8") as f:
                _yaml.dump(body, f, default_flow_style=False, allow_unicode=True)

            # Hot reload
            from app.config import load_config as _load_config
            new_cfg = _load_config(config_path)
        except SecretCryptoError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Update mutable references
        config.routes = new_cfg.routes
        config.aliases = new_cfg.aliases
        config.provider_base_urls = new_cfg.provider_base_urls
        config.keypools = new_cfg.keypools
        config.fallbacks = new_cfg.fallbacks
        config.tenants = new_cfg.tenants
        config.pricing = new_cfg.pricing
        config.logging = new_cfg.logging

        # Rebuild runtime objects
        router.routes = config.routes
        router.aliases = config.aliases

        key_pools.clear()
        key_pools.update({
            name: KeyPool(provider=name, keys=kp.keys, strategy=kp.strategy)
            for name, kp in config.keypools.items()
        })

        await write_audit_log(
            principal_info["principal"],
            action="config_update",
            target_type="config",
            target_name="gateway.yaml",
            detail="保存网关配置",
        )
        return {"status": "ok"}

    @app.post("/admin/api/auth/verification-code")
    async def send_verification_code(request: Request):
        body = await request.json()
        channel = str(body.get("channel", "")).strip().lower()
        target = str(body.get("target", "")).strip()

        if channel not in {"email", "phone"}:
            raise HTTPException(status_code=400, detail="channel must be email or phone")

        if channel == "email":
            target = target.lower()
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", target):
                raise HTTPException(status_code=400, detail="Invalid email")
        else:
            target = re.sub(r"[\s-]", "", target)
            if not re.match(r"^\+?\d{6,20}$", target):
                raise HTTPException(status_code=400, detail="Invalid phone")

        from sqlalchemy import desc, select
        from app.db.models import VerificationCode

        now = beijing_now()
        async with db.session() as session:
            latest = await session.execute(
                select(VerificationCode)
                .where(
                    VerificationCode.channel == channel,
                    VerificationCode.target == target,
                    VerificationCode.purpose == "register",
                )
                .order_by(desc(VerificationCode.created_at))
                .limit(1)
            )
            latest_code = latest.scalars().first()
            if latest_code and latest_code.created_at > now - timedelta(seconds=60):
                raise HTTPException(status_code=429, detail="Please wait before requesting another code")

            code = f"{secrets.randbelow(1_000_000):06d}"
            record = VerificationCode(
                channel=channel,
                target=target,
                code=code,
                purpose="register",
                expires_at=now + timedelta(minutes=5),
            )
            session.add(record)
            await session.commit()

        return {
            "status": "ok",
            "channel": channel,
            "target": target,
            "expires_in_seconds": 300,
            "debug_code": code,
        }

    def normalize_registration_target(channel: str, target: str) -> tuple[str, str]:
        channel = str(channel or "").strip().lower()
        target = str(target or "").strip()
        if channel not in {"email", "phone"}:
            raise HTTPException(status_code=400, detail="channel must be email or phone")
        if channel == "email":
            target = target.lower()
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", target):
                raise HTTPException(status_code=400, detail="Invalid email")
        else:
            target = re.sub(r"[\s-]", "", target)
            if not re.match(r"^\+?\d{6,20}$", target):
                raise HTTPException(status_code=400, detail="Invalid phone")
        return channel, target

    def generate_registration_invite_code() -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        part = lambda: "".join(secrets.choice(alphabet) for _ in range(4))
        return f"RG-{part()}-{part()}"

    @app.post("/admin/api/auth/registration-requests")
    async def create_registration_request(request: Request):
        body = await request.json()
        channel, target = normalize_registration_target(
            str(body.get("channel", "")).strip().lower(),
            str(body.get("target", "")).strip(),
        )
        reason = str(body.get("reason", "")).strip()[:512] or None

        from sqlalchemy import or_, select
        from app.db.models import AdminUser, RegistrationRequest

        async with db.session() as session:
            existing_user = await session.execute(
                select(AdminUser).where(
                    or_(
                        AdminUser.username == target,
                        AdminUser.email == target if channel == "email" else AdminUser.phone == target,
                    )
                )
            )
            if existing_user.scalars().first():
                raise HTTPException(status_code=409, detail="Account already exists")

            existing_request = await session.execute(
                select(RegistrationRequest)
                .where(
                    RegistrationRequest.target == target,
                    RegistrationRequest.status.in_(["pending", "approved"]),
                )
                .order_by(RegistrationRequest.created_at.desc(), RegistrationRequest.id.desc())
                .limit(1)
            )
            request_record = existing_request.scalars().first()
            if request_record:
                return {"status": "ok", "request": _registration_request_public_payload(request_record)}

            request_record = RegistrationRequest(
                channel=channel,
                target=target,
                reason=reason,
                status="pending",
            )
            session.add(request_record)
            await session.commit()
            await session.refresh(request_record)

        return {"status": "ok", "request": _registration_request_public_payload(request_record)}

    @app.post("/admin/api/auth/register-with-code")
    async def register_with_invite_code(request: Request):
        body = await request.json()
        target = str(body.get("target", "")).strip()
        password = str(body.get("password", ""))
        invite_code = str(body.get("code", "")).strip().upper()

        _require_strong_password(password)
        if not re.match(r"^RG-[A-Z0-9]{4}-[A-Z0-9]{4}$", invite_code):
            raise HTTPException(status_code=400, detail="Invalid registration code")

        from sqlalchemy import or_, select
        from app.db.models import AdminUser, RegistrationRequest

        async with db.session() as session:
            request_result = await session.execute(
                select(RegistrationRequest)
                .where(
                    RegistrationRequest.invite_code == invite_code,
                    RegistrationRequest.status == "approved",
                    RegistrationRequest.registered_at.is_(None),
                )
                .limit(1)
            )
            request_record = request_result.scalars().first()
            if request_record is None:
                raise HTTPException(status_code=400, detail="Invalid or used registration code")
            if request_record.target != target.strip().lower() and request_record.target != re.sub(r"[\s-]", "", target.strip()):
                raise HTTPException(status_code=400, detail="Registration code does not match account")

            channel, normalized_target = normalize_registration_target(request_record.channel, request_record.target)
            existing = await session.execute(
                select(AdminUser).where(
                    or_(
                        AdminUser.username == normalized_target,
                        AdminUser.email == normalized_target if channel == "email" else AdminUser.phone == normalized_target,
                    )
                )
            )
            if existing.scalars().first():
                raise HTTPException(status_code=409, detail="Account already exists")

            now = beijing_now()
            user = AdminUser(
                username=normalized_target,
                email=normalized_target if channel == "email" else None,
                phone=normalized_target if channel == "phone" else None,
                password_hash=_hash_password(password),
                role="user",
                tenant="default",
            )
            session.add(user)
            await session.flush()
            request_record.status = "registered"
            request_record.registered_at = now
            request_record.registered_user_id = user.id
            await session.commit()
            await session.refresh(user)

        return {
            "status": "ok",
            "username": user.username,
            "role": user.role,
            "tenant": user.tenant,
        }

    @app.get("/admin/api/registration-requests")
    async def list_registration_requests(request: Request):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user" or principal_info["principal"].role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

        from sqlalchemy import select
        from app.db.models import RegistrationRequest

        async with db.session() as session:
            result = await session.execute(
                select(RegistrationRequest)
                .order_by(RegistrationRequest.created_at.desc(), RegistrationRequest.id.desc())
            )
            requests = result.scalars().all()

        return {"items": [_registration_request_payload(item) for item in requests]}

    @app.post("/admin/api/registration-requests/{request_id}/approve")
    async def approve_registration_request(request: Request, request_id: int):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user" or principal_info["principal"].role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

        from sqlalchemy import select
        from app.db.models import RegistrationRequest

        async with db.session() as session:
            result = await session.execute(select(RegistrationRequest).where(RegistrationRequest.id == request_id))
            request_record = result.scalars().first()
            if request_record is None:
                raise HTTPException(status_code=404, detail="Registration request not found")
            if request_record.status not in {"pending", "approved"}:
                raise HTTPException(status_code=400, detail="Only pending requests can be approved")

            existing_code = request_record.invite_code
            while not existing_code:
                candidate = generate_registration_invite_code()
                duplicate = await session.execute(
                    select(RegistrationRequest).where(RegistrationRequest.invite_code == candidate)
                )
                if duplicate.scalars().first() is None:
                    existing_code = candidate

            request_record.status = "approved"
            request_record.invite_code = existing_code
            request_record.reviewed_at = beijing_now()
            request_record.reviewer_id = principal_info["principal"].id
            await session.commit()
            await session.refresh(request_record)

        await write_audit_log(
            principal_info["principal"],
            action="registration_approve",
            target_type="registration_request",
            target_id=request_record.id,
            target_name=request_record.target,
            detail=f"通过注册申请并生成注册码：{request_record.target}",
        )
        return {"status": "ok", "request": _registration_request_payload(request_record)}

    @app.post("/admin/api/registration-requests/{request_id}/reject")
    async def reject_registration_request(request: Request, request_id: int):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user" or principal_info["principal"].role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

        from sqlalchemy import select
        from app.db.models import RegistrationRequest

        async with db.session() as session:
            result = await session.execute(select(RegistrationRequest).where(RegistrationRequest.id == request_id))
            request_record = result.scalars().first()
            if request_record is None:
                raise HTTPException(status_code=404, detail="Registration request not found")
            if request_record.status == "registered":
                raise HTTPException(status_code=400, detail="Registered request cannot be rejected")
            request_record.status = "rejected"
            request_record.reviewed_at = beijing_now()
            request_record.reviewer_id = principal_info["principal"].id
            await session.commit()
            await session.refresh(request_record)

        await write_audit_log(
            principal_info["principal"],
            action="registration_reject",
            target_type="registration_request",
            target_id=request_record.id,
            target_name=request_record.target,
            detail=f"拒绝注册申请：{request_record.target}",
        )
        return {"status": "ok", "request": _registration_request_payload(request_record)}

    @app.post("/admin/api/registration-requests/{request_id}/reset-code")
    async def reset_registration_code(request: Request, request_id: int):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user" or principal_info["principal"].role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

        from sqlalchemy import select
        from app.db.models import RegistrationRequest

        async with db.session() as session:
            result = await session.execute(select(RegistrationRequest).where(RegistrationRequest.id == request_id))
            request_record = result.scalars().first()
            if request_record is None:
                raise HTTPException(status_code=404, detail="Registration request not found")
            if request_record.status != "approved":
                raise HTTPException(status_code=400, detail="Only approved requests can reset code")

            new_code = ""
            while not new_code:
                candidate = generate_registration_invite_code()
                duplicate = await session.execute(
                    select(RegistrationRequest).where(RegistrationRequest.invite_code == candidate)
                )
                if duplicate.scalars().first() is None:
                    new_code = candidate
            request_record.invite_code = new_code
            request_record.reviewed_at = beijing_now()
            request_record.reviewer_id = principal_info["principal"].id
            await session.commit()
            await session.refresh(request_record)

        await write_audit_log(
            principal_info["principal"],
            action="registration_code_reset",
            target_type="registration_request",
            target_id=request_record.id,
            target_name=request_record.target,
            detail=f"重新生成注册申请注册码：{request_record.target}",
        )
        return {"status": "ok", "request": _registration_request_payload(request_record)}

    @app.post("/admin/api/auth/register")
    async def register_admin_user(request: Request):
        body = await request.json()
        channel = str(body.get("channel", "")).strip().lower()
        target = str(body.get("target", "")).strip()
        password = str(body.get("password", ""))
        code = str(body.get("code", "")).strip()

        if channel not in {"email", "phone"}:
            raise HTTPException(status_code=400, detail="channel must be email or phone")
        _require_strong_password(password)
        if not re.match(r"^\d{6}$", code):
            raise HTTPException(status_code=400, detail="Invalid verification code")

        if channel == "email":
            target = target.lower()
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", target):
                raise HTTPException(status_code=400, detail="Invalid email")
        else:
            target = re.sub(r"[\s-]", "", target)
            if not re.match(r"^\+?\d{6,20}$", target):
                raise HTTPException(status_code=400, detail="Invalid phone")

        from sqlalchemy import or_, select
        from app.db.models import AdminUser, VerificationCode

        now = beijing_now()
        async with db.session() as session:
            existing = await session.execute(
                select(AdminUser).where(
                    or_(
                        AdminUser.username == target,
                        AdminUser.email == target if channel == "email" else AdminUser.phone == target,
                    )
                )
            )
            if existing.scalars().first():
                raise HTTPException(status_code=409, detail="Account already exists")

            code_result = await session.execute(
                select(VerificationCode)
                .where(
                    VerificationCode.channel == channel,
                    VerificationCode.target == target,
                    VerificationCode.purpose == "register",
                    VerificationCode.code == code,
                    VerificationCode.consumed_at.is_(None),
                    VerificationCode.expires_at >= now,
                )
                .order_by(VerificationCode.created_at.desc())
                .limit(1)
            )
            verification = code_result.scalars().first()
            if verification is None:
                raise HTTPException(status_code=400, detail="Invalid or expired verification code")

            user = AdminUser(
                username=target,
                email=target if channel == "email" else None,
                phone=target if channel == "phone" else None,
                password_hash=_hash_password(password),
                role="user",
                tenant="default",
            )
            verification.consumed_at = now
            session.add(user)
            await session.commit()

        return {
            "status": "ok",
            "username": target,
            "role": "user",
            "tenant": "default",
        }

    @app.post("/admin/api/auth/login")
    async def login_admin_user(request: Request):
        body = await request.json()
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))

        if not username or not password:
            raise HTTPException(status_code=400, detail="Username and password are required")

        from sqlalchemy import or_, select
        from app.db.models import AdminUser

        lookup = username.lower() if "@" in username else re.sub(r"[\s-]", "", username)
        async with db.session() as session:
            result = await session.execute(
                select(AdminUser).where(
                    or_(
                        AdminUser.username == username,
                        AdminUser.email == lookup,
                        AdminUser.phone == lookup,
                    )
                )
            )
            user = result.scalars().first()
            if user is not None:
                await _attach_module_overrides(session, user)

        if user is None or not _verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid username or password")

        response = JSONResponse(content={
            "status": "ok",
            "user": _admin_user_payload(user),
        })
        session_id = await _create_admin_session(db, user.id)
        response.set_cookie(
            key="gw_admin_session",
            value=session_id,
            httponly=True,
            samesite="lax",
            max_age=7 * 24 * 60 * 60,
            path="/",
        )
        return response

    @app.get("/admin/api/auth/me")
    async def get_current_admin_user(request: Request):
        user = await _get_admin_user_from_request(db, request)
        if user is None:
            raise HTTPException(status_code=401, detail="Not logged in")
        return {"status": "ok", "user": _admin_user_payload(user)}

    @app.get("/admin/api/models/available")
    async def list_available_models(request: Request):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user":
            raise HTTPException(status_code=403, detail="Login required")

        items = []
        for alias, raw_model in sorted(config.aliases.items()):
            try:
                provider, resolved_model = router.match(alias)
            except ValueError:
                provider = ""
                resolved_model = raw_model

            pool = key_pools.get(provider)
            has_keys = provider in config.keypools and bool(config.keypools[provider].keys)
            items.append({
                "alias": alias,
                "model": resolved_model,
                "provider": provider,
                "available": bool(provider and provider in providers and pool and has_keys),
                "status": "可用" if provider and provider in providers and pool and has_keys else "不可用",
                "description": "来自当前网关配置的模型别名",
            })

        return {"items": items}

    @app.post("/admin/api/model-tests/run")
    async def run_model_test(request: Request):
        user = await _get_admin_user_from_request(db, request)
        if user is None:
            raise HTTPException(status_code=401, detail="Not logged in")

        body = await request.json()
        prompt = str(body.get("prompt", "你好，请回复 ok")).strip() or "你好，请回复 ok"

        if user.role in {"admin", "operator"}:
            provider_name = str(body.get("provider", "")).strip()
            model = str(body.get("model", "")).strip()
            key_suffix = str(body.get("key_suffix", "auto")).strip() or "auto"
            if not provider_name or provider_name not in providers:
                raise HTTPException(status_code=400, detail="Provider is required")
            if provider_name not in config.keypools:
                raise HTTPException(status_code=400, detail="Provider has no key pool")
            if not model:
                raise HTTPException(status_code=400, detail="Model is required")

            api_key = select_model_test_key(provider_name, key_suffix)
            result = await run_model_test_call(
                provider_name=provider_name,
                model=model,
                api_key=api_key,
                prompt=prompt,
            )
            test_request_id = f"model-test-{uuid.uuid4().hex[:12]}"
            await app.state.access_logger.log(
                request_id=test_request_id,
                tenant=user.tenant,
                model=model,
                provider=provider_name,
                api_key_suffix=result.get("key_suffix") or (api_key[-4:] if len(api_key) >= 4 else api_key),
                status=200 if result.get("status") == "success" else int(result.get("status_code") or 500),
                latency_ms=int(result.get("latency_ms") or 0),
                prompt_tokens=int(result.get("usage", {}).get("prompt_tokens", 0) or 0),
                completion_tokens=int(result.get("usage", {}).get("completion_tokens", 0) or 0),
                total_tokens=int(result.get("usage", {}).get("total_tokens", 0) or 0),
                stream=False,
                user_id=user.id,
                username=user.username,
            )
            result["mode"] = "system"
            result["base_url"] = provider_url(provider_name)
            result["request_id"] = test_request_id
            return result

        if user.role != "user":
            raise HTTPException(status_code=403, detail="Model test is not available for this role")

        from sqlalchemy import select
        from app.db.models import UserApiKey

        key_id_raw = body.get("api_key_id")
        try:
            key_id = int(key_id_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="User API key is required")

        async with db.session() as session:
            key_result = await session.execute(
                select(UserApiKey).where(UserApiKey.id == key_id, UserApiKey.user_id == user.id)
            )
            user_key = key_result.scalars().first()
            if user_key is None:
                raise HTTPException(status_code=404, detail="User API key not found")
            if user_key.status != "active":
                raise HTTPException(status_code=403, detail="User API key is not active")
            binding = await current_user_model_binding(session, user.id)

        if binding is None:
            raise HTTPException(status_code=403, detail="No approved model binding")
        if binding.status != "active":
            raise HTTPException(status_code=403, detail="Model binding is stopped by administrator")

        api_key = select_model_test_key(binding.provider, "auto")
        result = await run_model_test_call(
            provider_name=binding.provider,
            model=binding.model,
            api_key=api_key,
            prompt=prompt,
        )
        test_request_id = f"model-test-{uuid.uuid4().hex[:12]}"
        await app.state.access_logger.log(
            request_id=test_request_id,
            tenant=user.tenant,
            model=binding.model,
            provider=binding.provider,
            api_key_suffix=result.get("key_suffix") or (api_key[-4:] if len(api_key) >= 4 else api_key),
            status=200 if result.get("status") == "success" else int(result.get("status_code") or 500),
            latency_ms=int(result.get("latency_ms") or 0),
            prompt_tokens=int(result.get("usage", {}).get("prompt_tokens", 0) or 0),
            completion_tokens=int(result.get("usage", {}).get("completion_tokens", 0) or 0),
            total_tokens=int(result.get("usage", {}).get("total_tokens", 0) or 0),
            stream=False,
            user_id=user.id,
            username=user.username,
            user_api_key_prefix=user_key.key_prefix,
            model_binding_id=binding.id,
        )
        if result.get("status") == "success":
            await record_usage_if_present(
                request_id=test_request_id,
                tenant_name=user.tenant,
                model=binding.model,
                provider_name=binding.provider,
                usage=result.get("usage", {}),
                user_id=user.id,
                username=user.username,
                api_key_prefix=user_key.key_prefix,
                model_binding_id=binding.id,
            )
        result["mode"] = "personal"
        result["alias"] = binding.alias
        result["user_api_key_prefix"] = user_key.key_prefix
        result["model_binding_id"] = binding.id
        return result

    @app.get("/admin/api/model-requests/me")
    async def get_my_model_requests(request: Request):
        user = await _get_admin_user_from_request(db, request)
        if user is None:
            raise HTTPException(status_code=401, detail="Not logged in")

        from sqlalchemy import desc, select
        from app.db.models import UserModelBinding, UserModelRequest

        async with db.session() as session:
            requests_result = await session.execute(
                select(UserModelRequest)
                .where(UserModelRequest.user_id == user.id)
                .order_by(desc(UserModelRequest.created_at), desc(UserModelRequest.id))
            )
            binding_result = await session.execute(
                select(UserModelBinding).where(UserModelBinding.user_id == user.id)
            )

        requests = requests_result.scalars().all()
        binding = binding_result.scalars().first()
        return {
            "requests": [_model_request_payload(item) for item in requests],
            "binding": _model_binding_payload(binding),
        }

    @app.post("/admin/api/model-requests")
    async def create_model_request(request: Request):
        user = await _get_admin_user_from_request(db, request)
        if user is None:
            raise HTTPException(status_code=401, detail="Not logged in")
        if user.role != "user":
            raise HTTPException(status_code=403, detail="Only normal users can apply for models")

        body = await request.json()
        alias = str(body.get("alias", "")).strip()
        model_info = resolve_available_model(alias)

        from sqlalchemy import select
        from app.db.models import UserModelRequest

        async with db.session() as session:
            pending_result = await session.execute(
                select(UserModelRequest).where(
                    UserModelRequest.user_id == user.id,
                    UserModelRequest.status == "pending",
                )
            )
            if pending_result.scalars().first():
                raise HTTPException(status_code=409, detail="A model request is already pending")

            binding = await current_user_model_binding(session, user.id)
            if binding and binding.status == "active" and binding.alias == model_info["alias"]:
                raise HTTPException(status_code=409, detail="Model is already bound")

            record = UserModelRequest(
                user_id=user.id,
                username=user.username,
                alias=model_info["alias"],
                model=model_info["model"],
                provider=model_info["provider"],
                request_type="change" if binding else "initial",
                status="pending",
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)

        await write_audit_log(
            user,
            action="model_request_create",
            target_type="model_request",
            target_id=record.id,
            target_name=f"{record.username} / {record.alias}",
            detail=f"提交模型申请：{record.alias} -> {record.model}",
        )
        return {"status": "ok", "request": _model_request_payload(record)}

    @app.get("/admin/api/model-requests")
    async def list_model_requests(request: Request):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user":
            raise HTTPException(status_code=403, detail="Admin role required")
        if principal_info["principal"].role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

        from sqlalchemy import desc, select
        from app.db.models import UserModelBinding, UserModelRequest

        async with db.session() as session:
            requests_result = await session.execute(
                select(UserModelRequest).order_by(desc(UserModelRequest.created_at), desc(UserModelRequest.id))
            )
            bindings_result = await session.execute(
                select(UserModelBinding).order_by(desc(UserModelBinding.updated_at), desc(UserModelBinding.id))
            )

        return {
            "requests": [_model_request_payload(item) for item in requests_result.scalars().all()],
            "bindings": [_model_binding_payload(item) for item in bindings_result.scalars().all()],
        }

    @app.post("/admin/api/model-requests/{request_id}/approve")
    async def approve_model_request(request: Request, request_id: int):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user" or principal_info["principal"].role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

        from sqlalchemy import select
        from app.db.models import UserModelBinding, UserModelRequest

        now = beijing_now()
        async with db.session() as session:
            result = await session.execute(
                select(UserModelRequest).where(UserModelRequest.id == request_id)
            )
            model_request = result.scalars().first()
            if model_request is None:
                raise HTTPException(status_code=404, detail="Model request not found")
            if model_request.status != "pending":
                raise HTTPException(status_code=409, detail="Model request already reviewed")

            binding = await current_user_model_binding(session, model_request.user_id)
            if binding is None:
                binding = UserModelBinding(
                    user_id=model_request.user_id,
                    username=model_request.username,
                    alias=model_request.alias,
                    model=model_request.model,
                    provider=model_request.provider,
                    status="active",
                    updated_at=now,
                )
                session.add(binding)
            else:
                binding.username = model_request.username
                binding.alias = model_request.alias
                binding.model = model_request.model
                binding.provider = model_request.provider
                binding.status = "active"
                binding.updated_at = now
                binding.resumed_at = now
                binding.stopped_at = None

            model_request.status = "approved"
            model_request.reviewed_at = now
            model_request.reviewer_id = principal_info["principal"].id
            await session.commit()
            await session.refresh(model_request)
            await session.refresh(binding)

        await write_audit_log(
            principal_info["principal"],
            action="model_request_approve",
            target_type="model_request",
            target_id=model_request.id,
            target_name=f"{model_request.username} / {model_request.alias}",
            detail=f"审批通过模型申请：{model_request.alias} -> {model_request.model}",
        )
        return {
            "status": "ok",
            "request": _model_request_payload(model_request),
            "binding": _model_binding_payload(binding),
        }

    @app.post("/admin/api/model-requests/{request_id}/reject")
    async def reject_model_request(request: Request, request_id: int):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user" or principal_info["principal"].role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

        from sqlalchemy import select
        from app.db.models import UserModelRequest

        async with db.session() as session:
            result = await session.execute(
                select(UserModelRequest).where(UserModelRequest.id == request_id)
            )
            model_request = result.scalars().first()
            if model_request is None:
                raise HTTPException(status_code=404, detail="Model request not found")
            if model_request.status != "pending":
                raise HTTPException(status_code=409, detail="Model request already reviewed")

            model_request.status = "rejected"
            model_request.reviewed_at = beijing_now()
            model_request.reviewer_id = principal_info["principal"].id
            await session.commit()
            await session.refresh(model_request)

        await write_audit_log(
            principal_info["principal"],
            action="model_request_reject",
            target_type="model_request",
            target_id=model_request.id,
            target_name=f"{model_request.username} / {model_request.alias}",
            detail=f"拒绝模型申请：{model_request.alias} -> {model_request.model}",
        )
        return {"status": "ok", "request": _model_request_payload(model_request)}

    @app.post("/admin/api/model-bindings/{binding_id}/stop")
    async def stop_model_binding(request: Request, binding_id: int):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user" or principal_info["principal"].role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

        from sqlalchemy import select
        from app.db.models import UserModelBinding

        now = beijing_now()
        async with db.session() as session:
            result = await session.execute(
                select(UserModelBinding).where(UserModelBinding.id == binding_id)
            )
            binding = result.scalars().first()
            if binding is None:
                raise HTTPException(status_code=404, detail="Model binding not found")
            binding.status = "stopped"
            binding.stopped_at = now
            binding.updated_at = now
            await session.commit()
            await session.refresh(binding)

        await write_audit_log(
            principal_info["principal"],
            action="model_binding_stop",
            target_type="model_binding",
            target_id=binding.id,
            target_name=f"{binding.username} / {binding.alias}",
            detail=f"停止用户使用模型：{binding.alias} -> {binding.model}",
        )
        return {"status": "ok", "binding": _model_binding_payload(binding)}

    @app.post("/admin/api/model-bindings/{binding_id}/resume")
    async def resume_model_binding(request: Request, binding_id: int):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user" or principal_info["principal"].role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

        from sqlalchemy import select
        from app.db.models import UserModelBinding

        now = beijing_now()
        async with db.session() as session:
            result = await session.execute(
                select(UserModelBinding).where(UserModelBinding.id == binding_id)
            )
            binding = result.scalars().first()
            if binding is None:
                raise HTTPException(status_code=404, detail="Model binding not found")
            binding.status = "active"
            binding.resumed_at = now
            binding.updated_at = now
            await session.commit()
            await session.refresh(binding)

        await write_audit_log(
            principal_info["principal"],
            action="model_binding_resume",
            target_type="model_binding",
            target_id=binding.id,
            target_name=f"{binding.username} / {binding.alias}",
            detail=f"恢复用户使用模型：{binding.alias} -> {binding.model}",
        )
        return {"status": "ok", "binding": _model_binding_payload(binding)}

    @app.get("/admin/api/user-api-keys/me")
    async def list_my_user_api_keys(request: Request):
        user = await _get_admin_user_from_request(db, request)
        if user is None:
            raise HTTPException(status_code=401, detail="Not logged in")
        if user.role != "user":
            raise HTTPException(status_code=403, detail="Only normal users can manage user API keys")

        from sqlalchemy import desc, select
        from app.db.models import UserApiKey

        async with db.session() as session:
            result = await session.execute(
                select(UserApiKey)
                .where(UserApiKey.user_id == user.id)
                .order_by(desc(UserApiKey.created_at), desc(UserApiKey.id))
            )
            keys = result.scalars().all()

        return {"items": [_user_api_key_payload(key) for key in keys]}

    @app.post("/admin/api/user-api-keys")
    async def create_my_user_api_key(request: Request):
        user = await _get_admin_user_from_request(db, request)
        if user is None:
            raise HTTPException(status_code=401, detail="Not logged in")
        if user.role != "user":
            raise HTTPException(status_code=403, detail="Only normal users can manage user API keys")

        body = await request.json()
        name = str(body.get("name", "默认调用 Key")).strip() or "默认调用 Key"
        name = name[:128]
        note = str(body.get("note", "")).strip()[:256] or None
        expires_at = _parse_optional_datetime(body.get("expires_at"))

        from sqlalchemy import func, select
        from app.db.models import UserApiKey

        async with db.session() as session:
            active_count_result = await session.execute(
                select(func.count())
                .select_from(UserApiKey)
                .where(UserApiKey.user_id == user.id, UserApiKey.status == "active")
            )
            if active_count_result.scalar_one() >= 3:
                raise HTTPException(status_code=409, detail="Active user API key limit reached")

            api_key = _generate_user_api_key()
            record = UserApiKey(
                user_id=user.id,
                username=user.username,
                tenant=user.tenant,
                key_hash=_hash_api_key(api_key),
                key_secret=api_key,
                key_prefix=api_key[:17],
                name=name,
                note=note,
                expires_at=expires_at,
                status="active",
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)

        await write_audit_log(
            user,
            action="user_api_key_create",
            target_type="user_api_key",
            target_id=record.id,
            target_name=record.name,
            detail=f"生成用户 API Key：{record.key_prefix}...",
        )
        return {
            "status": "ok",
            "api_key": api_key,
            "key": _user_api_key_payload(record),
        }

    @app.get("/admin/api/user-api-keys/{key_id}/reveal")
    async def reveal_my_user_api_key(request: Request, key_id: int):
        user = await _get_admin_user_from_request(db, request)
        if user is None:
            raise HTTPException(status_code=401, detail="Not logged in")
        if user.role != "user":
            raise HTTPException(status_code=403, detail="Only normal users can manage user API keys")

        from sqlalchemy import select
        from app.db.models import UserApiKey

        async with db.session() as session:
            result = await session.execute(
                select(UserApiKey).where(UserApiKey.id == key_id, UserApiKey.user_id == user.id)
            )
            key = result.scalars().first()
            if key is None:
                raise HTTPException(status_code=404, detail="User API key not found")
            if not key.key_secret:
                raise HTTPException(status_code=409, detail="这把旧 Key 创建时未保存明文，无法显示。请重新生成一把新 Key。")

        return {"status": "ok", "api_key": key.key_secret, "key": _user_api_key_payload(key)}

    @app.post("/admin/api/user-api-keys/{key_id}/revoke")
    async def revoke_my_user_api_key(request: Request, key_id: int):
        user = await _get_admin_user_from_request(db, request)
        if user is None:
            raise HTTPException(status_code=401, detail="Not logged in")
        if user.role != "user":
            raise HTTPException(status_code=403, detail="Only normal users can manage user API keys")

        from sqlalchemy import select
        from app.db.models import UserApiKey

        async with db.session() as session:
            result = await session.execute(
                select(UserApiKey).where(UserApiKey.id == key_id, UserApiKey.user_id == user.id)
            )
            key = result.scalars().first()
            if key is None:
                raise HTTPException(status_code=404, detail="User API key not found")
            key.status = "revoked"
            key.revoked_at = beijing_now()
            await session.commit()
            await session.refresh(key)

        await write_audit_log(
            user,
            action="user_api_key_revoke",
            target_type="user_api_key",
            target_id=key.id,
            target_name=key.name,
            detail=f"停用用户 API Key：{key.key_prefix}...",
        )
        return {"status": "ok", "key": _user_api_key_payload(key)}

    @app.post("/admin/api/user-api-keys/{key_id}/rotate")
    async def rotate_my_user_api_key(request: Request, key_id: int):
        user = await _get_admin_user_from_request(db, request)
        if user is None:
            raise HTTPException(status_code=401, detail="Not logged in")
        if user.role != "user":
            raise HTTPException(status_code=403, detail="Only normal users can manage user API keys")

        from sqlalchemy import select
        from app.db.models import UserApiKey

        async with db.session() as session:
            result = await session.execute(
                select(UserApiKey).where(UserApiKey.id == key_id, UserApiKey.user_id == user.id)
            )
            old_key = result.scalars().first()
            if old_key is None:
                raise HTTPException(status_code=404, detail="User API key not found")
            old_key.status = "revoked"
            old_key.revoked_at = beijing_now()

            api_key = _generate_user_api_key()
            record = UserApiKey(
                user_id=user.id,
                username=user.username,
                tenant=user.tenant,
                key_hash=_hash_api_key(api_key),
                key_secret=api_key,
                key_prefix=api_key[:17],
                name=old_key.name,
                note=old_key.note,
                expires_at=old_key.expires_at,
                status="active",
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)

        await write_audit_log(
            user,
            action="user_api_key_rotate",
            target_type="user_api_key",
            target_id=record.id,
            target_name=record.name,
            detail=f"重新生成用户 API Key：旧 {old_key.key_prefix}...，新 {record.key_prefix}...",
        )
        return {
            "status": "ok",
            "api_key": api_key,
            "key": _user_api_key_payload(record),
        }

    @app.get("/admin/api/users")
    async def list_admin_users(request: Request):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user":
            raise HTTPException(status_code=403, detail="Admin role required")
        require_module_access(principal_info, "users")

        from sqlalchemy import select
        from app.db.models import AdminUser

        async with db.session() as session:
            result = await session.execute(
                select(AdminUser).order_by(AdminUser.created_at.desc(), AdminUser.id.desc())
            )
            users = result.scalars().all()
            for user in users:
                await _attach_module_overrides(session, user)

        return {
            "items": [
                {
                    **_admin_user_payload(user),
                    "created_at": iso_beijing(user.created_at),
                }
                for user in users
            ]
        }

    def normalize_user_payload(body: dict, require_password: bool = False) -> dict:
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        role = str(body.get("role", "user")).strip()
        tenant = str(body.get("tenant", "default")).strip() or "default"
        email = str(body.get("email", "")).strip().lower() or None
        phone = re.sub(r"[\s-]", "", str(body.get("phone", "")).strip()) or None
        module_overrides_raw = body.get("module_overrides", None)

        if not username:
            raise HTTPException(status_code=400, detail="Username is required")
        if require_password or password:
            _require_strong_password(password)
        if role not in {"admin", "operator", "user"}:
            raise HTTPException(status_code=400, detail="Invalid role")
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise HTTPException(status_code=400, detail="Invalid email")
        if phone and not re.match(r"^\+?\d{6,20}$", phone):
            raise HTTPException(status_code=400, detail="Invalid phone")
        if module_overrides_raw is None:
            module_overrides = None
        else:
            if not isinstance(module_overrides_raw, list):
                raise HTTPException(status_code=400, detail="module_overrides must be a list")
            module_overrides = []
            for module in module_overrides_raw:
                module_name = str(module).strip()
                if module_name not in VALID_MODULES:
                    raise HTTPException(status_code=400, detail=f"Invalid module: {module_name}")
                if module_name not in module_overrides:
                    module_overrides.append(module_name)

        return {
            "username": username,
            "password": password,
            "role": role,
            "tenant": tenant,
            "email": email,
            "phone": phone,
            "module_overrides": module_overrides,
        }

    async def replace_user_module_overrides(session, user_id: int, modules: list[str] | None) -> None:
        from sqlalchemy import delete
        from app.db.models import AdminUserModulePermission

        await session.execute(
            delete(AdminUserModulePermission).where(AdminUserModulePermission.user_id == user_id)
        )
        if modules is None:
            return
        for module in modules:
            session.add(AdminUserModulePermission(user_id=user_id, module=module))

    @app.post("/admin/api/users")
    async def create_admin_user(request: Request):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user":
            raise HTTPException(status_code=403, detail="Admin role required")
        require_module_access(principal_info, "users")
        body = normalize_user_payload(await request.json(), require_password=True)

        from sqlalchemy import or_, select
        from app.db.models import AdminUser

        unique_checks = [AdminUser.username == body["username"]]
        if body["email"]:
            unique_checks.append(AdminUser.email == body["email"])
        if body["phone"]:
            unique_checks.append(AdminUser.phone == body["phone"])

        async with db.session() as session:
            existing = await session.execute(select(AdminUser).where(or_(*unique_checks)))
            if existing.scalars().first():
                raise HTTPException(status_code=409, detail="Account already exists")

            user = AdminUser(
                username=body["username"],
                password_hash=_hash_password(body["password"]),
                email=body["email"],
                phone=body["phone"],
                role=body["role"],
                tenant=body["tenant"],
            )
            session.add(user)
            await session.commit()
            await replace_user_module_overrides(session, user.id, body["module_overrides"])
            await session.commit()
            await session.refresh(user)
            await _attach_module_overrides(session, user)

        await write_audit_log(
            principal_info["principal"],
            action="user_create",
            target_type="admin_user",
            target_id=user.id,
            target_name=user.username,
            detail=f"新增用户：{user.username}（{user.role}）",
        )
        return {
            "status": "ok",
            "user": {
                **_admin_user_payload(user),
                "created_at": iso_beijing(user.created_at),
            },
        }

    @app.put("/admin/api/users/{user_id}")
    async def update_admin_user(request: Request, user_id: int):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user":
            raise HTTPException(status_code=403, detail="Admin role required")
        require_module_access(principal_info, "users")
        current_user = principal_info["principal"]
        body = normalize_user_payload(await request.json(), require_password=False)

        if current_user.id == user_id and body["role"] != "admin":
            raise HTTPException(status_code=400, detail="Cannot remove admin role from current user")
        if current_user.id == user_id and body["module_overrides"] is not None:
            required_self_modules = {"users", "config"}
            if not required_self_modules.issubset(set(body["module_overrides"])):
                raise HTTPException(status_code=400, detail="Cannot remove own admin management modules")

        from sqlalchemy import or_, select
        from app.db.models import AdminUser

        unique_checks = [AdminUser.username == body["username"]]
        if body["email"]:
            unique_checks.append(AdminUser.email == body["email"])
        if body["phone"]:
            unique_checks.append(AdminUser.phone == body["phone"])

        async with db.session() as session:
            result = await session.execute(select(AdminUser).where(AdminUser.id == user_id))
            user = result.scalars().first()
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")

            duplicate = await session.execute(
                select(AdminUser).where(or_(*unique_checks), AdminUser.id != user_id)
            )
            if duplicate.scalars().first():
                raise HTTPException(status_code=409, detail="Account already exists")

            user.username = body["username"]
            user.email = body["email"]
            user.phone = body["phone"]
            user.role = body["role"]
            user.tenant = body["tenant"]
            if body["password"]:
                user.password_hash = _hash_password(body["password"])
            await replace_user_module_overrides(session, user.id, body["module_overrides"])
            await session.commit()
            await session.refresh(user)
            await _attach_module_overrides(session, user)

        await write_audit_log(
            current_user,
            action="user_update",
            target_type="admin_user",
            target_id=user.id,
            target_name=user.username,
            detail=f"更新用户：{user.username}（{user.role}）",
        )
        return {
            "status": "ok",
            "user": {
                **_admin_user_payload(user),
                "created_at": iso_beijing(user.created_at),
            },
        }

    @app.delete("/admin/api/users/{user_id}")
    async def delete_admin_user(request: Request, user_id: int):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user":
            raise HTTPException(status_code=403, detail="Admin role required")
        require_module_access(principal_info, "users")
        current_user = principal_info["principal"]
        if current_user.id == user_id:
            raise HTTPException(status_code=400, detail="Cannot delete current user")

        from sqlalchemy import delete, select
        from app.db.models import AdminSession, AdminUser, AdminUserModulePermission

        async with db.session() as session:
            result = await session.execute(select(AdminUser).where(AdminUser.id == user_id))
            user = result.scalars().first()
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            deleted_username = user.username

            await session.execute(delete(AdminSession).where(AdminSession.user_id == user_id))
            await session.execute(
                delete(AdminUserModulePermission).where(AdminUserModulePermission.user_id == user_id)
            )
            await session.delete(user)
            await session.commit()

        await write_audit_log(
            current_user,
            action="user_delete",
            target_type="admin_user",
            target_id=user_id,
            target_name=deleted_username,
            detail=f"删除用户：{deleted_username}",
        )
        return {"status": "ok"}

    @app.post("/admin/api/auth/logout")
    async def logout_admin_user(request: Request):
        from sqlalchemy import select
        from app.db.models import AdminSession

        session_id = request.cookies.get("gw_admin_session")
        if session_id:
            async with db.session() as session:
                result = await session.execute(
                    select(AdminSession).where(AdminSession.session_id == session_id)
                )
                admin_session = result.scalars().first()
                if admin_session is not None:
                    admin_session.revoked_at = beijing_now()
                    await session.commit()

        response = JSONResponse(content={"status": "ok"})
        response.delete_cookie("gw_admin_session", path="/")
        return response

    @app.get("/admin/")
    async def admin_page(request: Request):
        user = await _get_admin_user_from_request(db, request)
        if user is None:
            return RedirectResponse("/admin/login", status_code=303)
        return FileResponse("app/static/admin.html")

    @app.get("/admin/login")
    async def admin_login_page(request: Request):
        user = await _get_admin_user_from_request(db, request)
        if user is not None:
            return RedirectResponse("/admin/", status_code=303)
        return FileResponse("app/static/login.html")

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    return app
