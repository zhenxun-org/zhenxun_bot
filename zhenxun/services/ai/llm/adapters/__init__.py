"""
LLM 适配器模块

提供不同LLM服务商的API适配器实现，统一接口调用方式。
"""

from .base import BaseAdapter, RequestData, ResponseData
from .deepseek import DeepSeekAdapter
from .doubao import DoubaoAdapter
from .factory import LLMAdapterFactory, get_adapter_for_api_type, register_adapter
from .gemini import GeminiAdapter
from .glm import GLMAdapter
from .jina import JinaAdapter
from .mimo import MiMoAdapter
from .minimax import MiniMaxAdapter
from .openai import OpenAIAdapter, OpenAICompatAdapter, OpenAIResponsesAdapter
from .openrouter import OpenRouterAdapter

LLMAdapterFactory.initialize()

__all__ = [
    "BaseAdapter",
    "DeepSeekAdapter",
    "DoubaoAdapter",
    "GLMAdapter",
    "GeminiAdapter",
    "JinaAdapter",
    "LLMAdapterFactory",
    "MiMoAdapter",
    "MiniMaxAdapter",
    "OpenAIAdapter",
    "OpenAICompatAdapter",
    "OpenAIResponsesAdapter",
    "OpenRouterAdapter",
    "RequestData",
    "ResponseData",
    "get_adapter_for_api_type",
    "register_adapter",
]
