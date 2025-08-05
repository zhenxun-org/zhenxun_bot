"""
LLM 服务模块 - 公共 API 入口

提供统一的 AI 服务调用接口、核心类型定义和模型管理功能。
"""

from .api import (
    chat,
    code,
    embed,
    generate,
    generate_structured,
    run_with_tools,
    search,
)
from .config import (
    CommonOverrides,
    LLMGenerationConfig,
    register_llm_configs,
)

register_llm_configs()
from .api import ModelName
from .manager import (
    clear_model_cache,
    get_cache_stats,
    get_global_default_model_name,
    get_model_instance,
    list_available_models,
    list_embedding_models,
    list_model_identifiers,
    set_global_default_model_name,
)
from .session import AI, AIConfig
from .tools import function_tool, tool_provider_manager
from .types import (
    EmbeddingTaskType,
    LLMContentPart,
    LLMErrorCode,
    LLMException,
    LLMMessage,
    LLMResponse,
    ModelDetail,
    ModelInfo,
    ModelProvider,
    ResponseFormat,
    TaskType,
    ToolCategory,
    ToolMetadata,
    UsageInfo,
)
from .utils import create_multimodal_message, message_to_unimessage, unimsg_to_llm_parts

__all__ = [
    "AI",
    "AIConfig",
    "CommonOverrides",
    "EmbeddingTaskType",
    "LLMContentPart",
    "LLMErrorCode",
    "LLMException",
    "LLMGenerationConfig",
    "LLMMessage",
    "LLMResponse",
    "ModelDetail",
    "ModelInfo",
    "ModelName",
    "ModelProvider",
    "ResponseFormat",
    "TaskType",
    "ToolCategory",
    "ToolMetadata",
    "UsageInfo",
    "chat",
    "clear_model_cache",
    "code",
    "create_multimodal_message",
    "embed",
    "function_tool",
    "generate",
    "generate_structured",
    "get_cache_stats",
    "get_global_default_model_name",
    "get_model_instance",
    "list_available_models",
    "list_embedding_models",
    "list_model_identifiers",
    "message_to_unimessage",
    "register_llm_configs",
    "run_with_tools",
    "search",
    "set_global_default_model_name",
    "tool_provider_manager",
    "unimsg_to_llm_parts",
]
