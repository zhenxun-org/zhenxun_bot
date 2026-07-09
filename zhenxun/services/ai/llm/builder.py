"""
LLM 生成配置相关类和函数
"""

from typing import Any, Literal
from typing_extensions import Self

from zhenxun.services.ai.core.exceptions import ConfigurationException
from zhenxun.services.ai.core.options import (
    GenerationConfig,
    ResponseFormat,
)
from zhenxun.services.ai.utils.logger import log_llm as logger
from zhenxun.utils.pydantic_compat import model_json_schema, model_validate


class GeminiIntentNamespace:
    """Gemini 专属高级参数构建域"""

    def __init__(self, builder: "IntentBuilder"):
        self._builder = builder

    def set_safety_threshold(self, threshold: str) -> "IntentBuilder":
        """强制设置 Gemini 安全阈值 (如 BLOCK_NONE, BLOCK_ONLY_HIGH)"""
        self._builder._config.gemini_options.safety_settings = {
            "HARM_CATEGORY_HARASSMENT": threshold,
            "HARM_CATEGORY_HATE_SPEECH": threshold,
            "HARM_CATEGORY_SEXUALLY_EXPLICIT": threshold,
            "HARM_CATEGORY_DANGEROUS_CONTENT": threshold,
        }
        return self._builder


class OpenAIIntentNamespace:
    """OpenAI 专属高级参数构建域"""

    def __init__(self, builder: "IntentBuilder"):
        self._builder = builder

    def enable_server_storage(self, store: bool = True) -> "IntentBuilder":
        """设置是否在 OpenAI 服务端留存请求记录"""
        self._builder._config.openai_options.store = store
        return self._builder


class IntentBuilder:
    """
    基于能力意图声明的构建器 (Intent-Driven Builder)。
    完全屏蔽底层厂商参数差异，面向开发者提供 Fluent API。
    """

    def __init__(self):
        self._config = GenerationConfig()

    @property
    def gemini(self) -> GeminiIntentNamespace:
        return GeminiIntentNamespace(self)

    @property
    def openai(self) -> OpenAIIntentNamespace:
        return OpenAIIntentNamespace(self)

    def with_reasoning(self, level: str | None = None) -> Self:
        """
        跨厂商统一的思考/推理等级意图声明。
        自动向下转换为底层合法参数，并阻止不兼容模型的非法调用。
        """
        if level:
            self._config.common.reasoning_effort = level
            if level.lower() != "none":
                self._config.gemini_options.include_thoughts = True
        return self

    def with_local_cache(self, ttl: int = 3600) -> Self:
        """
        显式开启本次 LLM 网络请求的极速本地缓存。
        对于相同模型、相同参数、相同 Prompt 的请求，将直接返回本地记忆，免去网络开销。
        适用于 Embedding、确定性的结构化抽取或工作流节点。
        """
        self._config.custom_kwargs["__cache_ttl__"] = ttl
        return self

    def with_json_output(self) -> Self:
        """
        基础结构化意图：要求大模型输出通用 JSON 格式（不校验 Schema）。
        """
        self._config.output.response_format = ResponseFormat.JSON
        self._config.output.response_mime_type = "application/json"
        self._config.output.structured_output_strategy = "native"
        return self

    def require_structured_output(self, schema: Any, strict: bool = True) -> Self:
        """
        强制要求结构化输出意图。
        支持自动处理 Pydantic 模型并转换为厂商所需的 JSON Schema。
        """
        import inspect

        from pydantic import BaseModel

        from zhenxun.services.ai.core.options import StructuredOutputStrategy

        self._config.output.response_format = ResponseFormat.JSON
        self._config.output.response_mime_type = "application/json"
        if schema:
            if inspect.isclass(schema) and issubclass(schema, BaseModel):
                self._config.output.response_schema = model_json_schema(schema)
            else:
                from typing import cast

                self._config.output.response_schema = cast(dict[str, Any], schema)
        if strict:
            self._config.output.structured_output_strategy = (
                StructuredOutputStrategy.NATIVE
            )
        return self

    def config_core(
        self,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
    ) -> Self:
        """
        配置底层核心采样参数（如 temperature, max_tokens 等）。
        """
        if temperature is not None:
            self._config.common.temperature = temperature
        if max_tokens is not None:
            self._config.common.max_tokens = max_tokens
        if top_p is not None:
            self._config.common.top_p = top_p
        return self

    def with_safety_level(self, level: str = "moderate") -> Self:
        """
        安全合规意图。
        level 取值: 'strict' (最严格), 'moderate' (中等), 'none' (完全无限制)。
        """
        from zhenxun.services.ai.config import get_gemini_safety_threshold

        if level == "strict":
            self.gemini.set_safety_threshold("BLOCK_LOW_AND_ABOVE")
        elif level == "none":
            self.gemini.set_safety_threshold("BLOCK_NONE")
        else:
            self.gemini.set_safety_threshold(get_gemini_safety_threshold())
        return self

    def with_image_generation_params(
        self, aspect_ratio: str = "16:9", resolution: str = "1K"
    ) -> Self:
        """
        生图意图：统一配置图像生成的比例与分辨率。
        """
        self._config.media.aspect_ratio = aspect_ratio
        self._config.media.resolution = resolution
        return self

    def with_vision_optimization(
        self, quality: Literal["low", "medium", "high", "standard", "hd"] = "high"
    ) -> Self:
        """
        视觉优化意图。
        """
        self._config.media.quality = quality
        return self

    def with_provider_raw_kwargs(self, provider_name: str, **kwargs) -> Self:
        """厂商逃生舱：直接注入特有参数"""
        provider_name = provider_name.lower()
        if provider_name == "openai":
            for k, v in kwargs.items():
                setattr(self._config.openai_options, k, v)
        elif provider_name == "gemini":
            for k, v in kwargs.items():
                setattr(self._config.gemini_options, k, v)
        else:
            self._config.custom_kwargs.update(kwargs)
        return self

    def build(self) -> GenerationConfig:
        """构建最终的配置对象"""
        return self._config


def validate_override_params(
    override_config: dict[str, Any] | GenerationConfig | None,
) -> GenerationConfig:
    """验证和标准化覆盖参数"""
    if override_config is None:
        return GenerationConfig()

    if isinstance(override_config, GenerationConfig):
        return override_config

    if isinstance(override_config, dict):
        try:
            return model_validate(GenerationConfig, override_config)
        except Exception as e:
            logger.warning(f"覆盖配置参数验证失败: {e}")
            raise ConfigurationException(
                f"无效的覆盖配置参数: {e}",
                cause=e,
            )

    raise ConfigurationException(
        f"不支持的配置类型: {type(override_config)}",
    )
