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


ROLE_MODULES = {
    "admin": ["keys", "usage", "logs", "users", "config"],
    "operator": ["keys", "usage", "logs"],
    "user": ["usage", "logs"],
}
VALID_MODULES = ["keys", "usage", "logs", "users", "config"]


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
        "created_at": request.created_at.isoformat() if request.created_at else None,
        "reviewed_at": request.reviewed_at.isoformat() if request.reviewed_at else None,
    }


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
        "created_at": binding.created_at.isoformat() if binding.created_at else None,
        "updated_at": binding.updated_at.isoformat() if binding.updated_at else None,
        "stopped_at": binding.stopped_at.isoformat() if binding.stopped_at else None,
        "resumed_at": binding.resumed_at.isoformat() if binding.resumed_at else None,
    }


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


async def _create_admin_session(db: Database, user_id: int) -> str:
    from app.db.models import AdminSession

    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=7)
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

    now = datetime.utcnow()
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
    access_logger = AccessLogger(config.logging.access_log)
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

    async def require_admin_or_bearer(request: Request):
        admin_user = await _get_admin_user_from_request(db, request)
        if admin_user is not None:
            return {"kind": "admin_user", "principal": admin_user}

        auth_header = request.headers.get("authorization", "")
        try:
            token = auth.extract_bearer(auth_header)
            return {"kind": "tenant_key", "principal": auth.authenticate(token)}
        except PermissionError as e:
            raise HTTPException(status_code=401, detail=str(e))

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

    async def enforce_admin_user_model_access(request: Request, requested_model: str, resolved_model: str) -> None:
        user = await _get_admin_user_from_request(db, request)
        if user is None or user.role != "user":
            return
        async with db.session() as session:
            binding = await current_user_model_binding(session, user.id)
        if binding is None:
            raise HTTPException(status_code=403, detail="No approved model binding")
        if binding.status != "active":
            raise HTTPException(status_code=403, detail="Model binding is stopped by administrator")
        if requested_model not in {binding.alias, binding.model} and resolved_model != binding.model:
            raise HTTPException(status_code=403, detail="Requested model is not bound to this user")

    async def record_usage_if_present(
        *,
        request_id: str,
        tenant_name: str,
        model: str,
        provider_name: str,
        usage: dict | None,
    ) -> dict | None:
        usage = usage or {}
        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        completion_tokens = usage.get("completion_tokens", 0) or 0
        total_tokens = usage.get("total_tokens", 0) or 0
        if total_tokens <= 0:
            return None

        cost = 0.0
        model_pricing = config.pricing.get(provider_name, {}).get(model)
        if model_pricing:
            cost = (
                prompt_tokens * model_pricing.input
                + completion_tokens * model_pricing.output
            ) / 1_000_000
        return await app.state.usage_logger.record(
            request_id=request_id,
            tenant=tenant_name,
            model=model,
            provider=provider_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
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
            tenant = auth.authenticate(token)
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
            tenant = auth.authenticate(token)
        except PermissionError as e:
            raise HTTPException(status_code=401, detail=str(e))

        body = await request.json()
        try:
            chat_req = ChatRequest.from_dict(body)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        provider_name, resolved_model = router.match(chat_req.model)
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
            api_key = pool.select()
        except RuntimeError as e:
            # Try fallback
            resolved = False
            for fb in config.fallbacks:
                if fb.from_provider == provider_name:
                    fallback_pool = key_pools.get(fb.to_provider)
                    if fallback_pool:
                        try:
                            api_key = fallback_pool.select()
                            mapped_model = fb.model_map.get(chat_req.model, chat_req.model)
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
            app.state.access_logger.log(
                request_id=request_id, tenant=tenant.name, model=chat_req.model,
                provider=provider_name, api_key_suffix=key_suffix,
                status=status_code, latency_ms=latency_ms,
                prompt_tokens=0, completion_tokens=0, total_tokens=0,
                stream=chat_req.stream,
            )
            raise HTTPException(status_code=502, detail=str(e))

        latency_ms = int((time.monotonic() - start_time) * 1000)

        # Log access for successful request
        app.state.access_logger.log(
            request_id=request_id, tenant=tenant.name, model=chat_req.model,
            provider=provider_name, api_key_suffix=key_suffix,
            status=200, latency_ms=latency_ms,
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            stream=chat_req.stream,
        )

        if chat_req.stream:
            return StreamingResponse(
                stream_with_usage_recording(
                    response,
                    request_id=request_id,
                    tenant_name=tenant.name,
                    model=chat_req.model,
                    provider_name=provider_name,
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
            since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            since = datetime.utcnow() - timedelta(days=7)
        elif period == "month":
            since = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        async with db.session() as session:
            user_binding = None
            if principal_info["kind"] == "admin_user" and principal_info["principal"].role == "user":
                user_binding = await current_user_model_binding(session, principal_info["principal"].id)
                if user_binding is None or user_binding.status != "active":
                    return {
                        "total_tokens": 0,
                        "total_cost_usd": 0,
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
                query = query.where(UsageRecord.model == user_binding.model)
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

        return {
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "by_model": by_model,
            "by_tenant": by_tenant,
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
    async def get_logs(request: Request, limit: int = 50, offset: int = 0):
        principal_info = await require_admin_or_bearer(request)
        require_module_access(principal_info, "logs")
        tenant_filter = tenant_filter_for_principal(principal_info)

        log_path = config.logging.access_log
        if not os.path.exists(log_path):
            return {"total": 0, "items": []}

        import json as _json
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        items = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                item = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if tenant_filter is not None and item.get("tenant") != tenant_filter:
                continue
            items.append(item)

        total = len(items)
        page = items[offset:offset + limit]
        return {"total": total, "items": page}

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
                "keys": ["****" if k else "" for k in kp.keys],
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
        return {"provider": provider_name, "keys": keypool.keys}

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
                    if k == "****" and i < len(orig_keys):
                        merged_keys.append(orig_keys[i])
                    else:
                        merged_keys.append(k)
                kp_data["keys"] = merged_keys

        # Merge tenant api_keys: if value is "****", keep original
        for i, t_data in enumerate(body.get("tenants", [])):
            if t_data.get("api_key") == "****" and i < len(config.tenants):
                t_data["api_key"] = config.tenants[i].api_key

        # Write to gateway.yaml
        import yaml as _yaml
        config_path = os.environ.get("GATEWAY_CONFIG_PATH", "config/gateway.yaml")
        with open(config_path, "w", encoding="utf-8") as f:
            _yaml.dump(body, f, default_flow_style=False, allow_unicode=True)

        # Hot reload
        from app.config import load_config as _load_config
        new_cfg = _load_config(config_path)

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

        now = datetime.utcnow()
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

    @app.post("/admin/api/auth/register")
    async def register_admin_user(request: Request):
        body = await request.json()
        channel = str(body.get("channel", "")).strip().lower()
        target = str(body.get("target", "")).strip()
        password = str(body.get("password", ""))
        code = str(body.get("code", "")).strip()

        if channel not in {"email", "phone"}:
            raise HTTPException(status_code=400, detail="channel must be email or phone")
        if len(password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
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

        now = datetime.utcnow()
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

        now = datetime.utcnow()
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
            model_request.reviewed_at = datetime.utcnow()
            model_request.reviewer_id = principal_info["principal"].id
            await session.commit()
            await session.refresh(model_request)

        return {"status": "ok", "request": _model_request_payload(model_request)}

    @app.post("/admin/api/model-bindings/{binding_id}/stop")
    async def stop_model_binding(request: Request, binding_id: int):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user" or principal_info["principal"].role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

        from sqlalchemy import select
        from app.db.models import UserModelBinding

        now = datetime.utcnow()
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

        return {"status": "ok", "binding": _model_binding_payload(binding)}

    @app.post("/admin/api/model-bindings/{binding_id}/resume")
    async def resume_model_binding(request: Request, binding_id: int):
        principal_info = await require_admin_or_bearer(request)
        if principal_info["kind"] != "admin_user" or principal_info["principal"].role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

        from sqlalchemy import select
        from app.db.models import UserModelBinding

        now = datetime.utcnow()
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

        return {"status": "ok", "binding": _model_binding_payload(binding)}

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
                    "created_at": user.created_at.isoformat() if user.created_at else None,
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
        if require_password and len(password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
        if password and len(password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
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

        return {
            "status": "ok",
            "user": {
                **_admin_user_payload(user),
                "created_at": user.created_at.isoformat() if user.created_at else None,
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

        return {
            "status": "ok",
            "user": {
                **_admin_user_payload(user),
                "created_at": user.created_at.isoformat() if user.created_at else None,
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

            await session.execute(delete(AdminSession).where(AdminSession.user_id == user_id))
            await session.execute(
                delete(AdminUserModulePermission).where(AdminUserModulePermission.user_id == user_id)
            )
            await session.delete(user)
            await session.commit()

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
                    admin_session.revoked_at = datetime.utcnow()
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
