"""
LLM 数据模型定义

包含模型信息、配置、工具定义和响应数据的模型类。
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from .enums import ModelProvider, ToolCategory

ModelName = str | None


class ToolDefinition(BaseModel):
    """
    一个结构化的工具定义模型，用于向LLM描述工具。
    """

    name: str = Field(..., description="工具的唯一名称标识")
    description: str = Field(..., description="工具功能的清晰描述")
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="符合JSON Schema规范的参数定义"
    )


class ToolResult(BaseModel):
    """
    一个结构化的工具执行结果模型。
    """

    output: Any = Field(..., description="返回给LLM的、可JSON序列化的原始输出")
    display_content: str | None = Field(
        default=None, description="用于日志或UI展示的人类可读的执行摘要"
    )


@dataclass(frozen=True)
class ModelInfo:
    """模型信息（不可变数据类）"""

    name: str
    provider: ModelProvider
    max_tokens: int = 4096
    supports_tools: bool = False
    supports_vision: bool = False
    supports_audio: bool = False
    cost_per_1k_tokens: float = 0.0


@dataclass
class UsageInfo:
    """使用信息数据类"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0

    @property
    def efficiency_ratio(self) -> float:
        """计算效率比（输出/输入）"""
        return self.completion_tokens / max(self.prompt_tokens, 1)


@dataclass
class ToolMetadata:
    """工具元数据"""

    name: str
    description: str
    category: ToolCategory
    read_only: bool = True
    destructive: bool = False
    open_world: bool = False
    parameters: dict[str, Any] = field(default_factory=dict)
    required_params: list[str] = field(default_factory=list)


class ModelDetail(BaseModel):
    """模型详细信息"""

    model_name: str
    is_available: bool = True
    is_embedding_model: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


class ProviderConfig(BaseModel):
    """LLM 提供商配置"""

    name: str = Field(..., description="Provider 的唯一名称标识")
    api_key: str | list[str] = Field(..., description="API Key 或 Key 列表")
    api_base: str | None = Field(None, description="API Base URL，如果为空则使用默认值")
    api_type: str = Field(default="openai", description="API 类型")
    openai_compat: bool = Field(default=False, description="是否使用 OpenAI 兼容模式")
    temperature: float | None = Field(default=0.7, description="默认温度参数")
    max_tokens: int | None = Field(default=None, description="默认最大输出 token 限制")
    models: list[ModelDetail] = Field(..., description="支持的模型列表")
    timeout: int = Field(default=180, description="请求超时时间")
    proxy: str | None = Field(default=None, description="代理设置")


class LLMToolFunction(BaseModel):
    """LLM 工具函数定义"""

    name: str
    arguments: str


class LLMToolCall(BaseModel):
    """LLM 工具调用"""

    id: str
    function: LLMToolFunction


class LLMCodeExecution(BaseModel):
    """代码执行结果"""

    code: str
    output: str | None = None
    error: str | None = None
    execution_time: float | None = None
    files_generated: list[str] | None = None


class LLMGroundingAttribution(BaseModel):
    """信息来源关联"""

    title: str | None = None
    uri: str | None = None
    snippet: str | None = None
    confidence_score: float | None = None


class LLMGroundingMetadata(BaseModel):
    """信息来源关联元数据"""

    web_search_queries: list[str] | None = None
    grounding_attributions: list[LLMGroundingAttribution] | None = None
    search_suggestions: list[dict[str, Any]] | None = None


class LLMCacheInfo(BaseModel):
    """缓存信息"""

    cache_hit: bool = False
    cache_key: str | None = None
    cache_ttl: int | None = None
    created_at: str | None = None
