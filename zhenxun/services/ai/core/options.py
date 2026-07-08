"""
AI 模块配置数据域类型定义
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from zhenxun.utils.pydantic_compat import model_copy, model_dump, model_validate

if TYPE_CHECKING:
    from zhenxun.services.ai.llm.builder import IntentBuilder

T = TypeVar("T")


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
    """
    文本嵌入任务类型 (对应 Gemini embedding 模型的 task_type 参数)
    """

    RETRIEVAL_QUERY = "RETRIEVAL_QUERY"
    """指定给定的文本是搜索/检索设置中的查询 (Query)。"""
    RETRIEVAL_DOCUMENT = "RETRIEVAL_DOCUMENT"
    """指定给定的文本是被搜索语料库中的文档 (Document)。"""
    SEMANTIC_SIMILARITY = "SEMANTIC_SIMILARITY"
    """指定文本将用于语义文本相似度 (STS) 计算。"""
    CLASSIFICATION = "CLASSIFICATION"
    """指定嵌入向量将用于文本分类任务。"""
    CLUSTERING = "CLUSTERING"
    """指定嵌入向量将用于聚类任务。"""
    QUESTION_ANSWERING = "QUESTION_ANSWERING"
    """指定文本将用于问答任务。"""
    FACT_VERIFICATION = "FACT_VERIFICATION"
    """指定文本将用于事实核查任务。"""


class BaseOutputDefinition(Generic[T]):
    """声明式结构化输出基类"""

    type_: type[T]


class ToolOutput(BaseOutputDefinition[T]):
    """工具输出标记：使用强制工具调用 (Tool Call) 结束任务并返回指定结构"""

    name: str | None = None
    description: str | None = None
    strict: bool | None = None

    def __init__(
        self,
        type_: type[T],
        name: str | None = None,
        description: str | None = None,
        strict: bool | None = None,
    ):
        self.type_ = type_
        self.name = name
        self.description = description
        self.strict = strict


class ReasoningEffort(str, Enum):
    """推理努力程度枚举"""

    NONE = "NONE"
    """不开启推理思考"""
    MINIMAL = "MINIMAL"
    """极低推理努力，追求最快响应"""
    LOW = "LOW"
    """较低推理努力"""
    MEDIUM = "MEDIUM"
    """中等推理努力（通常是默认值）"""
    HIGH = "HIGH"
    """高推理努力"""
    XHIGH = "XHIGH"
    """极高推理努力"""
    MAX = "MAX"
    """最大推理努力 (如 GLM / DeepSeek)"""


class ImageAspectRatio(str, Enum):
    """图像宽高比枚举"""

    SQUARE = "1:1"
    """正方形"""
    LANDSCAPE_16_9 = "16:9"
    """横向宽屏 16:9"""
    PORTRAIT_9_16 = "9:16"
    """竖向全屏 9:16"""
    LANDSCAPE_4_3 = "4:3"
    """横向标准 4:3"""
    PORTRAIT_3_4 = "3:4"
    """竖向标准 3:4"""
    LANDSCAPE_3_2 = "3:2"
    """横向 3:2"""
    PORTRAIT_2_3 = "2:3"
    """竖向 2:3"""


class ImageResolution(str, Enum):
    """图像分辨率/质量枚举"""

    STANDARD = "STANDARD"
    """标准分辨率"""
    HD = "HD"
    """高清分辨率"""


class CommonLLMConfig(BaseModel):
    """三大厂商通用基础生成参数"""

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    """采样温度。较高的值会使输出更加随机，较低的值会使其更加集中和确定。"""
    max_tokens: int | None = Field(default=None, gt=0)
    """聊天完成时生成的最大 Token 数。"""
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    """核采样 (Nucleus sampling) 概率阈值。"""
    top_k: int | None = Field(default=None, gt=0)
    """仅从概率最高的前 K 个 Token 中采样 (并非所有模型支持)。"""
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    """频率惩罚。正值根据新 Token 在文本中的现有频率对其进行惩罚，
    降低模型逐字重复同一行的可能性。"""
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    """存在惩罚。正值根据新 Token 到目前为止是否出现在文本中对其进行惩罚，
    增加模型谈论新主题的可能性。"""
    repetition_penalty: float | None = Field(default=None, ge=0.0, le=2.0)
    """重复惩罚系数 (部分非 OpenAI 兼容模型独有)。"""
    stop: list[str] | str | None = Field(default=None)
    """API 停止生成后续 Token 的停止词序列。"""
    reasoning_effort: ReasoningEffort | str | None = Field(default=None)
    """跨厂商统一的思考/推理等级意图（如 'low', 'high', 'max'）。
    具体映射由底层的 Adapter 执行。"""


class OutputFormatConfig(BaseModel):
    """输出格式与结构化控制"""

    response_format: ResponseFormat | dict[str, Any] | None = Field(default=None)
    """响应格式类型 (枚举或字典形式的 json_schema 对象)。"""
    response_mime_type: str | None = Field(default=None)
    """指定 MIME 类型 (如 application/json)，主要用于 Gemini。"""
    response_schema: dict[str, Any] | None = Field(default=None)
    """JSON Schema 字典，用于强制约束模型返回的 JSON 结构。"""
    response_modalities: list[str] | None = Field(default=None)
    """允许的响应模态 (如 ["TEXT", "IMAGE"])，主要用于 Gemini。"""
    structured_output_strategy: StructuredOutputStrategy | str | None = Field(
        default=None
    )
    """结构化输出所采用的内部策略。"""


class ToolCallConfig(BaseModel):
    """工具调用统一策略配置"""

    mode: Literal["AUTO", "ANY", "NONE"] = Field(default="AUTO")
    """工具调用模式 (AUTO: 自动, ANY: 强制至少调一个, NONE: 禁用)。"""
    allowed_function_names: list[str] | None = Field(default=None)
    """允许被调用的特定函数名称白名单。"""
    include_server_side_tool_invocations: bool | None = Field(default=None)
    """是否包含服务端侧的工具调用日志流转 (主要用于 Gemini)。"""


class BaseProviderOption(BaseModel):
    """厂商配置逃生舱基类"""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")  # type: ignore


class OpenAIOptions(BaseProviderOption):
    """OpenAI 专属特权参数 (适配 Responses API)"""

    store: bool | None = Field(default=None)
    """是否允许服务端留存本次请求的选项记录"""
    metadata: dict[str, str] | None = Field(default=None)
    """附加在请求上的自定义元数据"""


class GeminiOptions(BaseProviderOption):
    """Gemini 专属特权参数"""

    include_thoughts: bool | None = Field(default=None)
    """是否在最终响应中包含模型的内部思考过程 (Thoughts)"""
    safety_settings: dict[str, str] | None = Field(default=None)
    """Gemini 专有的各个维度的安全过滤阈值配置"""
    retrieval_config: dict[str, Any] | None = Field(default=None)
    """检索定位配置，如 LBS 经纬度信息，配合 Google Maps 工具使用"""


class OpenAITTSOptions(BaseProviderOption):
    """OpenAI TTS 专属特权参数"""

    voice_id: str | None = Field(default=None)
    """专属的音色 ID 配置"""


class GeminiTTSOptions(BaseProviderOption):
    """Gemini TTS 专属特权参数"""

    voice_id: str | None = Field(default=None)
    """专属的音色 ID 配置"""
    multi_speaker: bool | None = Field(default=None)
    """是否开启多说话人模式"""
    second_voice: str | None = Field(default=None)
    """多说话人模式下的第二音色名称"""


class MiniMaxTTSOptions(BaseProviderOption):
    """MiniMax TTS 专属特权参数 (控制极度精细)"""

    voice_id: str | None = Field(default=None)
    """专属的音色 ID 配置"""
    vol: float | None = Field(default=None, gt=0.0, le=10.0)
    """音量，范围 (0, 10]"""
    pitch: int | None = Field(default=None, ge=-12, le=12)
    """语调，范围 [-12, 12]"""
    emotion: (
        Literal[
            "happy",
            "sad",
            "angry",
            "fearful",
            "disgusted",
            "surprised",
            "calm",
            "fluent",
            "whisper",
        ]
        | None
    ) = Field(default=None)
    """情感控制"""
    timbre_weights: list[dict[str, Any]] | None = Field(default=None)
    """音色混合权重 (最多4种)"""
    pronunciation_dict: dict[str, list[str]] | None = Field(default=None)
    """自定义发音字典 (如: {"tone": ["处理/(chu3)(li3)"]})"""


class MiMoTTSOptions(BaseProviderOption):
    """MiMo TTS 专属特权参数"""

    voice_id: str | None = Field(default=None)
    """专属的音色 ID 配置"""


class MediaGenerationConfig(BaseModel):
    """多模态媒体生成/优化的全局配置"""

    aspect_ratio: ImageAspectRatio | str | None = Field(default=None)
    """生成的图像/视频宽高比 (如 '16:9')"""
    resolution: str | None = Field(default=None)
    """生成的图像/视频分辨率 (如 '1K', '4K' 或 '1024x1024')"""
    quality: Literal["low", "medium", "high", "standard", "hd"] | None = Field(
        default=None
    )
    """渲染质量及细节丰富水平"""


class TTSConfig(BaseModel):
    """文本转语音 (TTS) 全局配置"""

    response_format: Literal["mp3", "wav", "pcm", "flac", "opus", "aac"] = Field(
        default="mp3"
    )
    """输出音频格式"""
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    """语速 (通用映射)"""

    openai_options: OpenAITTSOptions = Field(default_factory=OpenAITTSOptions)
    """OpenAI 厂商专属请求参数集"""
    gemini_options: GeminiTTSOptions = Field(default_factory=GeminiTTSOptions)
    """Gemini 厂商专属请求参数集"""
    minimax_options: MiniMaxTTSOptions = Field(default_factory=MiniMaxTTSOptions)
    """MiniMax 厂商专属请求参数集"""
    mimo_options: MiMoTTSOptions = Field(default_factory=MiMoTTSOptions)
    """MiMo 厂商专属请求参数集"""

    custom_kwargs: dict[str, Any] = Field(default_factory=dict)
    """兜底逃生舱，包含的键值对将直接透传至顶层请求体中 (可用于缓存 TTL)"""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class GenerationConfig(BaseModel):
    """
    现代化 LLM 生成基座 (Intent-Driven Base)。
    隔离通用参数与厂商私有参数，彻底终结"上帝类"。
    """

    common: CommonLLMConfig = Field(default_factory=CommonLLMConfig)
    """大模型生成核心通用配置项"""
    output: OutputFormatConfig = Field(default_factory=OutputFormatConfig)
    """输出格式与约束控制配置项"""
    tools: ToolCallConfig = Field(default_factory=ToolCallConfig)
    """工具调用策略与函数声明配置项"""
    media: MediaGenerationConfig = Field(default_factory=MediaGenerationConfig)
    """多媒体生成相关配置项"""

    openai_options: OpenAIOptions = Field(default_factory=OpenAIOptions)
    """OpenAI 厂商专属请求参数集"""
    gemini_options: GeminiOptions = Field(default_factory=GeminiOptions)
    """Gemini 厂商专属请求参数集"""

    enable_caching: bool | None = Field(default=None)
    """是否在此次生成中开启上下文缓存 (Context Caching)"""
    custom_kwargs: dict[str, Any] = Field(default_factory=dict)
    """兜底逃生舱，包含的键值对将直接透传至顶层请求体中"""
    validation_policy: dict[str, Any] | None = Field(default=None)
    """自定义验证策略字典"""
    response_validator: Callable[[Any], None] | None = Field(default=None)
    """针对原始返回对象的自定义回调验证器"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @classmethod
    def builder(cls) -> "IntentBuilder":
        from zhenxun.services.ai.llm.builder import IntentBuilder

        return IntentBuilder()

    def to_dict(self) -> dict[str, Any]:
        return model_dump(self, exclude_none=True)

    def merge_with(self, other: "GenerationConfig | None") -> "GenerationConfig":
        """深度合并两个配置，实现配置的无损叠加"""
        if not other:
            return model_copy(self, deep=True)

        base_dump = model_dump(self, exclude_none=True)
        other_dump = model_dump(other, exclude_none=True)

        def deep_merge(d1: dict, d2: dict) -> dict:
            res = d1.copy()
            for k, v in d2.items():
                if isinstance(v, dict) and k in res and isinstance(res[k], dict):
                    res[k] = deep_merge(res[k], v)
                else:
                    res[k] = v
            return res

        merged_dump = deep_merge(base_dump, other_dump)
        return model_validate(GenerationConfig, merged_dump)


class LLMEmbeddingConfig(BaseModel):
    """Embedding 专用配置"""

    task_type: str | None = Field(default=None)
    """生成意图的任务类型，参考 EmbeddingTaskType (主要用于 Gemini 和 Jina)"""
    output_dimensionality: int | None = Field(default=None)
    """请求模型强制输出(或截断)的较低维度数，实现维度压缩"""
    title: str | None = Field(default=None)
    """提供该文档的标题以供底层优化。仅在 task_type 为 RETRIEVAL_DOCUMENT 时有效。"""
    encoding_format: str | None = Field(default="float")
    """向量数据在响应中的编码格式 (通常为 float 或 base64)"""
    multimodal: bool | list[str] = Field(default=False)
    """是否允许多模态向量化。False 表示纯文本(极速安全)；True 表示全部放行；
    也可传入 ['image', 'text'] 细粒度控制。"""
    custom_kwargs: dict[str, Any] = Field(default_factory=dict)
    """兜底逃生舱，包含的键值对将直接透传至顶层请求体中 (可用于缓存 TTL)"""

    model_config = ConfigDict(arbitrary_types_allowed=True)


__all__ = [
    "BaseProviderOption",
    "CommonLLMConfig",
    "EmbeddingTaskType",
    "GeminiOptions",
    "GeminiTTSOptions",
    "GenerationConfig",
    "ImageAspectRatio",
    "ImageResolution",
    "LLMEmbeddingConfig",
    "MiMoTTSOptions",
    "MiniMaxTTSOptions",
    "OpenAIOptions",
    "OpenAITTSOptions",
    "OutputFormatConfig",
    "ReasoningEffort",
    "ResponseFormat",
    "StructuredOutputStrategy",
    "TTSConfig",
    "ToolCallConfig",
    "ToolOutput",
]
