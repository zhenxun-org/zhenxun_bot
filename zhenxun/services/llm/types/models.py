"""
LLM 数据模型定义

包含模型信息、配置、工具定义和响应数据的模型类。
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from .enums import ModelProvider, ToolCategory

if TYPE_CHECKING:
    from .protocols import MCPCompatible

    MCPSessionType = (
        MCPCompatible | Callable[[], AbstractAsyncContextManager[MCPCompatible]] | None
    )
else:
    MCPCompatible = object
    MCPSessionType = Any

ModelName = str | None


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


class LLMTool(BaseModel):
    """LLM 工具定义（支持 MCP 风格）"""

    model_config = {"arbitrary_types_allowed": True}

    type: str = "function"
    function: dict[str, Any] | None = None
    mcp_session: MCPSessionType = None
    annotations: dict[str, Any] | None = Field(default=None, description="工具注解")

    def model_post_init(self, /, __context: Any) -> None:
        """验证工具定义的有效性"""
        _ = __context
        if self.type == "function" and self.function is None:
            raise ValueError("函数类型的工具必须包含 'function' 字段。")
        if self.type == "mcp" and self.mcp_session is None:
            raise ValueError("MCP 类型的工具必须包含 'mcp_session' 字段。")

    @classmethod
    def create(
        cls,
        name: str,
        description: str,
        parameters: dict[str, Any],
        required: list[str] | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> "LLMTool":
        """创建函数工具"""
        function_def = {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": parameters,
                "required": required or [],
            },
        }
        return cls(type="function", function=function_def, annotations=annotations)

    @classmethod
    def from_mcp_session(
        cls,
        session: Any,
        annotations: dict[str, Any] | None = None,
    ) -> "LLMTool":
        """从 MCP 会话创建工具"""
        return cls(type="mcp", mcp_session=session, annotations=annotations)


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
