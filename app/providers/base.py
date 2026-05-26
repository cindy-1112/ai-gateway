from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urlparse

import httpx

from app.models.request import ChatRequest


class BaseProvider(ABC):
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url
        self.http_client = httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(timeout, connect=10.0)
        )

    @abstractmethod
    def convert_request(self, request: ChatRequest) -> dict:
        """统一格式 → 提供商格式"""

    @abstractmethod
    def auth_header(self, api_key: str) -> dict:
        """构建认证头"""

    @abstractmethod
    def chat_endpoint(self) -> str:
        """提供商的聊天 API 端点路径"""

    def request_path(self) -> str:
        endpoint = self.chat_endpoint().lstrip("/")
        base_path = urlparse(self.base_url).path.rstrip("/")
        if endpoint.startswith("v1/") and base_path.endswith(("/v1", "/v3", "/openai")):
            return endpoint[3:]
        return endpoint

    async def chat(self, request: ChatRequest, api_key: str) -> httpx.Response:
        provider_request = self.convert_request(request)
        response = await self.http_client.post(
            self.request_path(),
            headers=self.auth_header(api_key),
            json=provider_request,
        )
        response.raise_for_status()
        return response

    async def close(self) -> None:
        await self.http_client.aclose()
