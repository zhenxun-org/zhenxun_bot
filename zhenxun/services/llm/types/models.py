"""
LLM 数据模型定义

包含模型信息、配置、工具定义和响应数据的模型类。
"""

import base64
from dataclasses import dataclass, field
from enum import Enum, auto
import mimetypes
from pathlib import Path
import sys
from typing import Any, Literal

import aiofiles
from pydantic import BaseModel, Field

from zhenxun.services.log import logger

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from strenum import StrEnum


class ModelProvider(Enum):
    """模型提供商枚举"""

    OPENAI = "openai"
    GEMINI = "gemini"
    ZHIXPU = "zhipu"
    CUSTOM = "custom"


class ResponseFormat(Enum):
    """响应格式枚举"""

    TEXT = "text"
    JSON = "json"
    MULTIMODAL = "multimodal"


class StructuredOutputStrategy(str, Enum):
    """结构化输出策略"""

    NATIVE = "native"
    """使用原生 API (如 OpenAI json_object/json_schema, Gemini mime_type)"""
    TOOL_CALL = "tool_call"
    """构造虚假工具调用来强制输出结构化数据 (适用于指令跟随弱但工具调用强的模型)"""
    PROMPT = "prompt"
    """仅在 Prompt 中追加 Schema 说明，依赖文本补全"""


class EmbeddingTaskType(str, Enum):
    """文本嵌入任务类型 (主要用于Gemini)"""

    RETRIEVAL_QUERY = "RETRIEVAL_QUERY"
    RETRIEVAL_DOCUMENT = "RETRIEVAL_DOCUMENT"
    SEMANTIC_SIMILARITY = "SEMANTIC_SIMILARITY"
    CLASSIFICATION = "CLASSIFICATION"
    CLUSTERING = "CLUSTERING"
    QUESTION_ANSWERING = "QUESTION_ANSWERING"
    FACT_VERIFICATION = "FACT_VERIFICATION"


class ToolCategory(Enum):
    """工具分类枚举"""

    FILE_SYSTEM = auto()
    NETWORK = auto()
    SYSTEM_INFO = auto()
    CALCULATION = auto()
    DATA_PROCESSING = auto()
    CUSTOM = auto()


class CodeExecutionOutcome(StrEnum):
    """代码执行结果状态枚举"""

    OUTCOME_OK = "OUTCOME_OK"
    OUTCOME_FAILED = "OUTCOME_FAILED"
    OUTCOME_DEADLINE_EXCEEDED = "OUTCOME_DEADLINE_EXCEEDED"
    OUTCOME_COMPILATION_ERROR = "OUTCOME_COMPILATION_ERROR"
    OUTCOME_RUNTIME_ERROR = "OUTCOME_RUNTIME_ERROR"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class TaskType(Enum):
    """任务类型枚举"""

    CHAT = "chat"
    CODE = "code"
    SEARCH = "search"
    ANALYSIS = "analysis"
    GENERATION = "generation"
    MULTIMODAL = "multimodal"


class LLMContentPart(BaseModel):
    """
    LLM 消息内容部分 - 支持多模态内容。

    这是一个联合体模型，`type` 字段决定了哪些其他字段是有效的。
    例如：
    - type='text': 使用 `text` 字段。
    - type='image': 使用 `image_source` 字段。
    - type='executable_code': 使用 `code_language` 和 `code_content` 字段。
    """

    type: str
    text: str | None = None
    image_source: str | None = None
    audio_source: str | None = None
    video_source: str | None = None
    document_source: str | None = None
    file_uri: str | None = None
    file_source: str | None = None
    url: str | None = None
    mime_type: str | None = None
    thought_text: str | None = None
    media_resolution: str | None = None
    code_language: str | None = None
    code_content: str | None = None
    execution_outcome: str | None = None
    execution_output: str | None = None
    metadata: dict[str, Any] | None = None

    def model_post_init(self, /, __context: Any) -> None:
        """验证内容部分的有效性"""
        _ = __context
        validation_rules = {
            "text": lambda: self.text is not None,
            "image": lambda: self.image_source,
            "audio": lambda: self.audio_source,
            "video": lambda: self.video_source,
            "document": lambda: self.document_source,
            "file": lambda: self.file_uri or self.file_source,
            "url": lambda: self.url,
            "thought": lambda: self.thought_text,
            "executable_code": lambda: self.code_content is not None,
            "execution_result": lambda: self.execution_outcome is not None,
        }

        if self.type in validation_rules:
            if not validation_rules[self.type]():
                raise ValueError(f"{self.type}类型的内容部分必须包含相应字段")

    @classmethod
    def text_part(cls, text: str) -> "LLMContentPart":
        """创建文本内容部分"""
        return cls(type="text", text=text)

    @classmethod
    def thought_part(cls, text: str) -> "LLMContentPart":
        """创建思考过程内容部分"""
        return cls(type="thought", thought_text=text)

    @classmethod
    def image_url_part(cls, url: str) -> "LLMContentPart":
        """创建图片URL内容部分"""
        return cls(type="image", image_source=url)

    @classmethod
    def image_base64_part(
        cls, data: str, mime_type: str = "image/png"
    ) -> "LLMContentPart":
        """创建Base64图片内容部分"""
        data_url = f"data:{mime_type};base64,{data}"
        return cls(type="image", image_source=data_url)

    @classmethod
    def audio_url_part(cls, url: str, mime_type: str = "audio/wav") -> "LLMContentPart":
        """创建音频URL内容部分"""
        return cls(type="audio", audio_source=url, mime_type=mime_type)

    @classmethod
    def video_url_part(cls, url: str, mime_type: str = "video/mp4") -> "LLMContentPart":
        """创建视频URL内容部分"""
        return cls(type="video", video_source=url, mime_type=mime_type)

    @classmethod
    def video_base64_part(
        cls, data: str, mime_type: str = "video/mp4"
    ) -> "LLMContentPart":
        """创建Base64视频内容部分"""
        data_url = f"data:{mime_type};base64,{data}"
        return cls(type="video", video_source=data_url, mime_type=mime_type)

    @classmethod
    def audio_base64_part(
        cls, data: str, mime_type: str = "audio/wav"
    ) -> "LLMContentPart":
        """创建Base64音频内容部分"""
        data_url = f"data:{mime_type};base64,{data}"
        return cls(type="audio", audio_source=data_url, mime_type=mime_type)

    @classmethod
    def file_uri_part(
        cls,
        file_uri: str,
        mime_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "LLMContentPart":
        """创建Gemini File API URI内容部分"""
        return cls(
            type="file",
            file_uri=file_uri,
            mime_type=mime_type,
            metadata=metadata or {},
        )

    @classmethod
    def executable_code_part(cls, language: str, code: str) -> "LLMContentPart":
        """创建可执行代码内容部分"""
        return cls(type="executable_code", code_language=language, code_content=code)

    @classmethod
    def execution_result_part(
        cls, outcome: str, output: str | None
    ) -> "LLMContentPart":
        """创建代码执行结果部分"""
        return cls(
            type="execution_result", execution_outcome=outcome, execution_output=output
        )

    @classmethod
    async def from_path(
        cls, path_like: str | Path, target_api: str | None = None
    ) -> "LLMContentPart | None":
        """
        从本地文件路径创建 LLMContentPart。
        自动检测MIME类型，并根据类型（如图片）可能加载为Base64。
        target_api 可以用于提示如何最好地准备数据（例如 'gemini' 可能偏好 base64）
        """
        try:
            path = Path(path_like)
            if not path.exists() or not path.is_file():
                logger.warning(f"文件不存在或不是一个文件: {path}")
                return None

            mime_type, _ = mimetypes.guess_type(path.resolve().as_uri())

            if not mime_type:
                logger.warning(
                    f"无法猜测文件 {path.name} 的MIME类型，将尝试作为文本文件处理。"
                )
                try:
                    async with aiofiles.open(path, encoding="utf-8") as f:
                        text_content = await f.read()
                    return cls.text_part(text_content)
                except Exception as e:
                    logger.error(f"读取文本文件 {path.name} 失败: {e}")
                    return None

            if mime_type.startswith("image/"):
                if target_api == "gemini" or not path.is_absolute():
                    try:
                        async with aiofiles.open(path, "rb") as f:
                            img_bytes = await f.read()
                        base64_data = base64.b64encode(img_bytes).decode("utf-8")
                        return cls.image_base64_part(
                            data=base64_data, mime_type=mime_type
                        )
                    except Exception as e:
                        logger.error(f"读取或编码图片文件 {path.name} 失败: {e}")
                        return None
                else:
                    logger.warning(
                        f"为本地图片路径 {path.name} 生成 image_url_part。"
                        "实际API可能不支持 file:// URI。考虑使用Base64或公网URL。"
                    )
                    return cls.image_url_part(url=path.resolve().as_uri())
            elif mime_type.startswith("audio/"):
                return cls.audio_url_part(
                    url=path.resolve().as_uri(), mime_type=mime_type
                )
            elif mime_type.startswith("video/"):
                if target_api == "gemini":
                    try:
                        async with aiofiles.open(path, "rb") as f:
                            video_bytes = await f.read()
                        base64_data = base64.b64encode(video_bytes).decode("utf-8")
                        return cls.video_base64_part(
                            data=base64_data, mime_type=mime_type
                        )
                    except Exception as e:
                        logger.error(f"读取或编码视频文件 {path.name} 失败: {e}")
                        return None
                else:
                    return cls.video_url_part(
                        url=path.resolve().as_uri(), mime_type=mime_type
                    )
            elif (
                mime_type.startswith("text/")
                or mime_type == "application/json"
                or mime_type == "application/xml"
            ):
                try:
                    async with aiofiles.open(path, encoding="utf-8") as f:
                        text_content = await f.read()
                    return cls.text_part(text_content)
                except Exception as e:
                    logger.error(f"读取文本类文件 {path.name} 失败: {e}")
                    return None
            else:
                logger.info(
                    f"文件 {path.name} (MIME: {mime_type}) 将作为通用文件URI处理。"
                )
                return cls.file_uri_part(
                    file_uri=path.resolve().as_uri(),
                    mime_type=mime_type,
                    metadata={"name": path.name, "source": "local_path"},
                )

        except Exception as e:
            logger.error(f"从路径 {path_like} 创建LLMContentPart时出错: {e}")
            return None

    def is_image_url(self) -> bool:
        """检查图像源是否为URL"""
        if not self.image_source:
            return False
        return self.image_source.startswith(("http://", "https://"))

    def is_image_base64(self) -> bool:
        """检查图像源是否为Base64 Data URL"""
        if not self.image_source:
            return False
        return self.image_source.startswith("data:")

    def get_base64_data(self) -> tuple[str, str] | None:
        """从Data URL中提取Base64数据和MIME类型"""
        if not self.is_image_base64() or not self.image_source:
            return None

        try:
            header, data = self.image_source.split(",", 1)
            mime_part = header.split(";")[0].replace("data:", "")
            return mime_part, data
        except (ValueError, IndexError):
            logger.warning(f"无法解析Base64图像数据: {self.image_source[:50]}...")
            return None


class LLMMessage(BaseModel):
    """
    LLM 消息对象，用于构建对话历史。

    核心字段说明：
    - role: 消息角色，推荐值为 'user', 'assistant', 'system', 'tool'。
    - content: 消息内容，可以是纯文本字符串，也可以是 LLMContentPart 列表（用于多模态）
    - tool_calls: (仅 assistant) 包含模型生成的工具调用请求。
    - tool_call_id: (仅 tool) 对应 tool 消息响应的调用 ID。
    - name: (仅 tool) 对应 tool 消息响应的函数名称。
    """

    role: str
    content: str | list[LLMContentPart]
    name: str | None = None
    tool_calls: list[Any] | None = None
    tool_call_id: str | None = None
    thought_signature: str | None = None

    def model_post_init(self, /, __context: Any) -> None:
        """验证消息的有效性"""
        _ = __context
        if self.role == "tool":
            if not self.tool_call_id:
                raise ValueError("工具角色的消息必须包含 tool_call_id")
            if not self.name:
                raise ValueError("工具角色的消息必须包含函数名 (在 name 字段中)")
        if self.role == "tool" and not isinstance(self.content, str):
            logger.warning(
                f"工具角色消息的内容期望是字符串，但得到的是: {type(self.content)}. "
                "将尝试转换为字符串。"
            )
            try:
                self.content = str(self.content)
            except Exception as e:
                raise ValueError(f"无法将工具角色的内容转换为字符串: {e}")

    @classmethod
    def user(cls, content: str | list[LLMContentPart]) -> "LLMMessage":
        """创建用户消息"""
        return cls(role="user", content=content)

    @classmethod
    def assistant_tool_calls(
        cls,
        tool_calls: list[Any],
        content: str | list[LLMContentPart] = "",
    ) -> "LLMMessage":
        """创建助手请求工具调用的消息"""
        return cls(role="assistant", content=content, tool_calls=tool_calls)

    @classmethod
    def assistant_text_response(
        cls, content: str | list[LLMContentPart]
    ) -> "LLMMessage":
        """创建助手纯文本回复的消息"""
        return cls(role="assistant", content=content, tool_calls=None)

    @classmethod
    def tool_response(
        cls,
        tool_call_id: str,
        function_name: str,
        result: Any,
    ) -> "LLMMessage":
        """创建工具执行结果的消息"""
        import json

        try:
            content_str = json.dumps(result)
        except TypeError as e:
            logger.error(
                f"工具 '{function_name}' 的结果无法JSON序列化: {result}. 错误: {e}"
            )
            content_str = json.dumps(
                {"error": "工具结果无法JSON序列化", "details": str(e)}
            )

        return cls(
            role="tool",
            content=content_str,
            tool_call_id=tool_call_id,
            name=function_name,
        )

    @classmethod
    def system(cls, content: str) -> "LLMMessage":
        """创建系统消息"""
        return cls(role="system", content=content)


class LLMResponse(BaseModel):
    """
    LLM 响应对象，封装了模型生成的全部信息。

    核心字段说明：
    - text: 模型生成的文本内容。如果是纯文本回复，此字段即为结果。
    - tool_calls: 如果模型决定调用工具，此列表包含调用详情。
    - content_parts: 包含多模态或结构化内容的原始部分列表（如思维链、代码块）。
    - raw_response: 原始的第三方 API 响应字典（用于调试）。
    - images: 如果请求涉及生图，此处包含生成的图片数据。
    """

    text: str
    content_parts: list[Any] | None = None
    images: list[bytes | Path] | None = None
    usage_info: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None
    tool_calls: list[Any] | None = None
    code_executions: list[Any] | None = None
    grounding_metadata: Any | None = None
    cache_info: Any | None = None
    thought_text: str | None = None
    thought_signature: str | None = None


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


class ToolChoice(BaseModel):
    """统一的工具选择配置"""

    mode: Literal["auto", "none", "any", "required"] = Field(
        default="auto", description="工具调用模式"
    )
    allowed_function_names: list[str] | None = Field(
        default=None, description="允许调用的函数名称列表"
    )


class BasePlatformTool(BaseModel):
    """平台原生工具基类"""

    class Config:
        extra = "forbid"

    def get_tool_declaration(self) -> dict[str, Any]:
        """获取放入 'tools' 列表中的声明对象 (Snake Case)"""
        raise NotImplementedError

    def get_tool_config(self) -> dict[str, Any] | None:
        """获取放入 'toolConfig' 中的配置对象 (Snake Case)"""
        return None


class GeminiCodeExecution(BasePlatformTool):
    """Gemini 代码执行工具"""

    def get_tool_declaration(self) -> dict[str, Any]:
        return {"code_execution": {}}


class GeminiGoogleSearch(BasePlatformTool):
    """Gemini 谷歌搜索 (Grounding) 工具"""

    mode: Literal["MODE_DYNAMIC"] = "MODE_DYNAMIC"
    dynamic_threshold: float | None = Field(default=None)

    def get_tool_declaration(self) -> dict[str, Any]:
        return {"google_search": {}}

    def get_tool_config(self) -> dict[str, Any] | None:
        return None


class GeminiUrlContext(BasePlatformTool):
    """Gemini 网址上下文工具"""

    urls: list[str] = Field(..., description="作为上下文的 URL 列表", max_length=20)

    def get_tool_declaration(self) -> dict[str, Any]:
        return {"google_search": {}, "url_context": {}}

    def get_tool_config(self) -> dict[str, Any] | None:
        return None


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
    api_type: str | None = None
    endpoint: str | None = None


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
    extra_headers: dict[str, str] | None = Field(
        default=None,
        description="额外请求头，用于网关鉴权等场景",
    )


class LLMToolFunction(BaseModel):
    """LLM 工具函数定义"""

    name: str
    arguments: str


class LLMToolCall(BaseModel):
    """LLM 工具调用"""

    id: str
    function: LLMToolFunction
    thought_signature: str | None = None


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
    search_entry_point: str | None = Field(
        default=None, description="Google搜索建议的HTML片段(renderedContent)"
    )
    map_widget_token: str | None = Field(
        default=None, description="Google Maps 前端组件令牌"
    )


class LLMCacheInfo(BaseModel):
    """缓存信息"""

    cache_hit: bool = False
    cache_key: str | None = None
    cache_ttl: int | None = None
    created_at: str | None = None
