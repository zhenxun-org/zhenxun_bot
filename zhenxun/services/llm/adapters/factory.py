"""
LLM 适配器工厂类
"""

from typing import ClassVar

from ..types.exceptions import LLMErrorCode, LLMException
from .base import BaseAdapter


class LLMAdapterFactory:
    """LLM适配器工厂类"""

    _adapters: ClassVar[dict[str, BaseAdapter]] = {}
    _api_type_mapping: ClassVar[dict[str, str]] = {}

    @classmethod
    def initialize(cls) -> None:
        """初始化默认适配器"""
        if cls._adapters:
            return

        from .gemini import GeminiAdapter
        from .openai import OpenAIAdapter

        cls.register_adapter(OpenAIAdapter())
        cls.register_adapter(GeminiAdapter())

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
            raise LLMException(
                f"不支持的API类型: {api_type}",
                code=LLMErrorCode.UNKNOWN_API_TYPE,
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
    """获取指定API类型的适配器"""
    return LLMAdapterFactory.get_adapter(api_type)


def register_adapter(adapter: BaseAdapter) -> None:
    """注册新的适配器"""
    LLMAdapterFactory.register_adapter(adapter)
