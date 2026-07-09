"""
模型自身设定域类型定义
"""

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .options import GenerationConfig

ModelName = str | None

TReq = TypeVar("TReq", bound=BaseModel)
"""泛型：LLM 请求体模型约束 (必须继承自 BaseModel)"""
TRes = TypeVar("TRes", bound=BaseModel)
"""泛型：LLM 响应体模型约束 (必须继承自 BaseModel)"""


@dataclass
class ModelIdentity:
    """模型的身份标识与基础能力数据传输对象 (DTO)，剥离运行时状态"""

    provider_name: str
    """模型提供商名称"""
    model_name: str
    """模型名称"""
    api_type: str
    """API 适配器类型"""
    api_base: str | None
    """API 基础请求地址/网关终结点"""
    path_prefix: str | None
    """中转路由的 URL 前缀"""
    capabilities: "ModelCapabilities"
    """模型的能力配置定义描述"""
    generation_config: GenerationConfig | None
    """模型的默认生成配置"""


class CancellationToken:
    """全局取消令牌，用于在异步链路中传递中止信号"""

    def __init__(self):
        self._cancelled = False
        self._futures: list[asyncio.Future] = []

    def cancel(self) -> None:
        self._cancelled = True
        for f in self._futures:
            if not f.done():
                f.cancel()

    def is_cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise asyncio.CancelledError(
                "任务已被主动取消 (CancellationToken triggered)"
            )

    def link_future(self, future: asyncio.Future) -> None:
        if self._cancelled:
            future.cancel()
        else:
            self._futures.append(future)


class LLMContext(BaseModel, Generic[TReq, TRes]):
    """LLM 执行上下文，用于在中间件管道中传递请求状态"""

    request: TReq
    """强类型的各模态请求对象实体。"""

    runtime_state: dict[str, Any] = Field(default_factory=dict)
    """中间件运行时的临时状态存储。"""
    cancellation_token: CancellationToken | None = Field(default=None)
    """全局取消令牌。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ToolDefinition(BaseModel):
    """结构化的工具定义模型"""

    name: str = Field(...)
    """工具名称"""
    description: str = Field(...)
    """工具描述"""
    parameters: dict[str, Any] = Field(default_factory=dict)
    """JSON Schema 参数"""
    metadata: dict[str, Any] = Field(default_factory=dict)
    """元数据"""


class ToolChoice(BaseModel):
    """工具选择配置"""

    mode: Literal["auto", "none", "any", "required"] = Field(default="auto")
    """工具选择模式"""
    allowed_function_names: list[str] | None = Field(default=None)
    """允许调用的函数名称列表"""


class ModelModality(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"
    EMBEDDING = "embedding"


class ReasoningMode(str, Enum):
    """推理/思考模式类型"""

    NONE = "none"
    BUDGET = "budget"
    LEVEL = "level"
    EFFORT = "effort"


class ModelCapabilities(BaseModel):
    """定义一个模型的核心能力。"""

    input_modalities: set[ModelModality] = Field(default={ModelModality.TEXT})
    """模型支持的输入模态集合。"""
    output_modalities: set[ModelModality] = Field(default={ModelModality.TEXT})
    """模型支持的输出模态集合。"""
    supports_tool_calling: bool = False
    """是否支持工具调用能力。"""
    supports_thinking_toggle: bool = False
    """是否支持通过 {"thinking": {"type": "enabled/disabled"}} 显式控制思考模式。"""
    is_embedding_model: bool = False
    """是否为嵌入模型。"""
    is_rerank_model: bool = False
    """是否为重排序模型。"""
    reasoning_mode: ReasoningMode = ReasoningMode.NONE
    """推理模式类型。"""
    reasoning_visibility: Literal["visible", "hidden", "none"] = "none"
    """推理过程可见性设置。"""
    reasoning_effort_map: dict[str, str] = Field(default_factory=dict)
    """思考强度参数(reasoning_effort)的降级映射矩阵，如 {"max": "xhigh"}。"""
    max_input_tokens: int = Field(default=256000)
    """最大输入 Token 数量（用于触发上下文压缩策略，未显式声明则默认为 256K）。"""
    supported_native_tools: set[str] = Field(default_factory=set)
    """该模型实际支持的云端原生能力/内置工具。"""
    default_voice_id: str | None = None
    """默认的语音合成音色 ID（TTS模型专用）。"""

    features: set[str] = Field(default_factory=set)
    """用于第三方插件动态注入的自定义能力标签。"""

    def supports_task(self, task: str) -> bool:
        """判断模型是否支持指定的底层任务类型"""
        if task == "embedding":
            return self.is_embedding_model
        elif task == "rerank":
            return self.is_rerank_model
        elif task == "tts":
            return ModelModality.AUDIO in self.output_modalities
        elif task == "image":
            return ModelModality.IMAGE in self.output_modalities
        elif task == "chat":
            return ModelModality.TEXT in self.output_modalities
        return False

    def accepts_input(self, modality: ModelModality) -> bool:
        """检查模型是否支持某种输入模态"""
        return modality in self.input_modalities

    def accepts_output(self, modality: ModelModality) -> bool:
        """检查模型是否支持某种输出模态"""
        return modality in self.output_modalities

    def has_feature(self, feature: str) -> bool:
        """检查模型是否具备某个扩展特性"""
        return feature in self.features


class ModelDetail(BaseModel):
    """模型详细信息"""

    model_name: str
    """模型名称。"""
    is_available: bool = True
    """模型是否可用。"""
    temperature: float | None = None
    """采样温度参数。"""
    max_output_tokens: int | None = None
    """单次生成最大 Token 数。"""
    api_type: str | None = None
    """API 类型标识。"""
    endpoint: str | None = None
    """模型服务端点地址。"""
    task_type: str | None = Field(default=None)
    """显式声明的主任务类型 (如 'image_generation')。"""
    path_prefix: str | None = Field(default=None)
    """中转路由前缀，例如 '/cogvideox' 或 '/minimax'。"""
    max_input_tokens: int | None = None
    """最大输入上下文窗口（用于控制记忆压缩策略）"""
    reasoning_effort: str | None = None
    """该模型的默认思考/推理等级（如 'high', 'low', 'none'）"""


__all__ = [
    "CancellationToken",
    "LLMContext",
    "ModelCapabilities",
    "ModelDetail",
    "ModelIdentity",
    "ModelModality",
    "ModelName",
    "ReasoningMode",
    "ToolChoice",
    "ToolDefinition",
]
