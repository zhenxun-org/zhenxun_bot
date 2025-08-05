"""
LLM 类型定义模块

统一导出所有核心类型、协议和异常定义。
"""

from .capabilities import ModelCapabilities, ModelModality, get_model_capabilities
from .content import (
    LLMContentPart,
    LLMMessage,
    LLMResponse,
)
from .enums import (
    EmbeddingTaskType,
    ModelProvider,
    ResponseFormat,
    TaskType,
    ToolCategory,
)
from .exceptions import LLMErrorCode, LLMException, get_user_friendly_error_message
from .models import (
    LLMCacheInfo,
    LLMCodeExecution,
    LLMGroundingAttribution,
    LLMGroundingMetadata,
    LLMToolCall,
    LLMToolFunction,
    ModelDetail,
    ModelInfo,
    ModelName,
    ProviderConfig,
    ToolMetadata,
    ToolResult,
    UsageInfo,
)
from .protocols import ToolExecutable, ToolProvider

__all__ = [
    "EmbeddingTaskType",
    "LLMCacheInfo",
    "LLMCodeExecution",
    "LLMContentPart",
    "LLMErrorCode",
    "LLMException",
    "LLMGroundingAttribution",
    "LLMGroundingMetadata",
    "LLMMessage",
    "LLMResponse",
    "LLMToolCall",
    "LLMToolFunction",
    "ModelCapabilities",
    "ModelDetail",
    "ModelInfo",
    "ModelModality",
    "ModelName",
    "ModelProvider",
    "ProviderConfig",
    "ResponseFormat",
    "TaskType",
    "ToolCategory",
    "ToolExecutable",
    "ToolMetadata",
    "ToolProvider",
    "ToolResult",
    "UsageInfo",
    "get_model_capabilities",
    "get_user_friendly_error_message",
]
