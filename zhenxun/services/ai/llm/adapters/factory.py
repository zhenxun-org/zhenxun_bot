"""
LLM 适配器工厂类
"""

from __future__ import annotations

import fnmatch
from typing import Any, ClassVar

import httpx

from zhenxun.services.ai.core.exceptions import ConfigurationException
from zhenxun.services.ai.core.models import ModelIdentity

from .base import BaseAdapter, RequestData


class LLMAdapterFactory:
    """适配器注册与按 API 类型分发的统一入口。"""

    _adapters: ClassVar[dict[str, BaseAdapter]] = {}
    _api_type_mapping: ClassVar[dict[str, str]] = {}

    @classmethod
    def initialize(cls) -> None:
        """初始化默认适配器"""
        if cls._adapters:
            return

        from .deepseek import DeepSeekAdapter
        from .doubao import DoubaoAdapter
        from .gemini import GeminiAdapter
        from .glm import GLMAdapter
        from .jina import JinaAdapter
        from .mimo import MiMoAdapter
        from .minimax import MiniMaxAdapter
        from .openai import OpenAIAdapter, OpenAIResponsesAdapter
        from .openrouter import OpenRouterAdapter

        cls.register_adapter(OpenAIAdapter())
        cls.register_adapter(OpenAIResponsesAdapter())
        cls.register_adapter(OpenRouterAdapter())
        cls.register_adapter(DeepSeekAdapter())
        cls.register_adapter(JinaAdapter())
        cls.register_adapter(GeminiAdapter())
        cls.register_adapter(GLMAdapter())
        cls.register_adapter(SmartAdapter())
        cls.register_adapter(MiMoAdapter())
        cls.register_adapter(MiniMaxAdapter())
        cls.register_adapter(DoubaoAdapter())

    @classmethod
    def register_adapter(cls, adapter: BaseAdapter) -> None:
        """注册适配器"""
        adapter_key = adapter.api_type
        cls._adapters[adapter_key] = adapter

        for api_type in adapter.supported_api_types:
            cls._api_type_mapping[api_type] = adapter_key

    @classmethod
    def get_adapter(cls, api_type: str) -> BaseAdapter:
        """获取适配器"""
        cls.initialize()

        adapter_key = cls._api_type_mapping.get(api_type)
        if not adapter_key:
            raise ConfigurationException(
                f"不支持的API类型: {api_type}",
                details={
                    "api_type": api_type,
                    "supported_types": list(cls._api_type_mapping.keys()),
                },
            )

        return cls._adapters[adapter_key]

    @classmethod
    def list_supported_types(cls) -> list[str]:
        """列出所有支持的API类型"""
        cls.initialize()
        return list(cls._api_type_mapping.keys())

    @classmethod
    def list_adapters(cls) -> dict[str, BaseAdapter]:
        """列出所有注册的适配器"""
        cls.initialize()
        return cls._adapters.copy()


def get_adapter_for_api_type(api_type: str) -> BaseAdapter:
    """按 API 类型获取适配器实例。"""
    return LLMAdapterFactory.get_adapter(api_type)


def register_adapter(adapter: BaseAdapter) -> None:
    """向工厂注册新的适配器实例。"""
    LLMAdapterFactory.register_adapter(adapter)


class SmartAdapter(BaseAdapter):
    """
    智能路由适配器。
    本身不处理序列化，而是根据规则委托给 OpenAIAdapter 或 GeminiAdapter。
    """

    @property
    def log_sanitization_context(self) -> str:
        """返回智能路由适配器的默认日志清洗上下文。"""
        return "openai_request"

    _ROUTING_RULES: ClassVar[list[tuple[str, str]]] = [
        ("*nano-banana*", "gemini"),
        ("*gemini*", "gemini"),
        ("*deepseek*", "deepseek"),
        ("*minimax*", "minimax"),
        ("*gpt*", "openai_responses"),
    ]
    _DEFAULT_API_TYPE: ClassVar[str] = "openai"

    def __init__(self):
        """初始化模型名到目标适配器的路由缓存。"""
        self._adapter_cache: dict[str, BaseAdapter] = {}

    @property
    def api_type(self) -> str:
        """适配器主类型标识。"""
        return "smart"

    @property
    def supported_api_types(self) -> list[str]:
        """当前适配器支持的 API 类型列表。"""
        return ["smart"]

    def _get_delegate_adapter(self, identity: ModelIdentity) -> BaseAdapter:
        """
        核心路由逻辑：决定使用哪个适配器 (带缓存)
        """
        if identity.api_type and identity.api_type != "smart":
            return get_adapter_for_api_type(identity.api_type)

        model_name = identity.model_name
        if model_name in self._adapter_cache:
            return self._adapter_cache[model_name]

        target_api_type = self._DEFAULT_API_TYPE
        model_name_lower = model_name.lower()

        for pattern, api_type in self._ROUTING_RULES:
            if fnmatch.fnmatch(model_name_lower, pattern):
                target_api_type = api_type
                break

        adapter = get_adapter_for_api_type(target_api_type)
        self._adapter_cache[model_name] = adapter
        return adapter

    async def prepare_payload(
        self, identity: ModelIdentity, api_key: str, request: Any
    ) -> RequestData:
        adapter = self._get_delegate_adapter(identity)
        return await adapter.prepare_payload(identity, api_key, request)

    async def parse_payload(
        self, identity: ModelIdentity, request: Any, raw_response: httpx.Response
    ) -> Any:
        adapter = self._get_delegate_adapter(identity)
        return await adapter.parse_payload(identity, request, raw_response)
