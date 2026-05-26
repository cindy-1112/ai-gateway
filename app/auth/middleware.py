from __future__ import annotations

from app.config import TenantConfig
from app.auth.tenant import Tenant


class AuthMiddleware:
    def __init__(self, tenants: list[TenantConfig]):
        self._tenants: dict[str, Tenant] = {}
        for t in tenants:
            tenant = Tenant.from_config(t)
            self._tenants[tenant.api_key] = tenant

    def authenticate(self, api_key: str) -> Tenant:
        if not api_key:
            raise PermissionError("Missing API key")
        tenant = self._tenants.get(api_key)
        if tenant is None:
            raise PermissionError("Invalid API key")
        return tenant

    def extract_bearer(self, header: str) -> str:
        if not header or not header.startswith("Bearer "):
            raise PermissionError("Missing Bearer token")
        return header[7:].strip()
