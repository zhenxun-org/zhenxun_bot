from typing import Any

from zhenxun.services.ai.core.messages import ImagePart, LLMMessage
from zhenxun.services.ai.core.models import (
    ModelCapabilities,
    ModelDetail,
)
from zhenxun.services.ai.core.options import GenerationConfig, ResponseFormat

from .handlers.openai_handlers import (
    OpenAIResponsesTextHandler,
    ResponsesConfigMapper,
    ResponsesMessageConverter,
    ResponsesResponseParser,
    ResponsesToolSerializer,
)
from .openai import OpenAIResponsesAdapter


class DeepSeekToolSerializer(ResponsesToolSerializer):
    """
    专门针对 DeepSeek 的工具序列化器。
    负责抹平 Pydantic Schema 与 DeepSeek Strict Mode 之间的差异。
    由于继承了 ResponsesToolSerializer，已自动获得 web_search 工具的原生格式支持。
    """

    def __init__(self, api_type: str = "deepseek"):
        """初始化 DeepSeek 工具序列化器。"""
        super().__init__(api_type=api_type)

    def sanitize_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        from zhenxun.services.ai.llm.engine.schema_transformer import (
            DeepSeekFallbackTransformer,
            OpenAIUnionFlattenTransformer,
            RefComplianceTransformer,
            RemoveUnsupportedKeysTransformer,
            RootRefInlineTransformer,
            SchemaPipeline,
            StrictObjectTransformer,
            TypeEnforcerTransformer,
        )

        unsupported_keys = [
            "default",
            "minLength",
            "maxLength",
            "pattern",
            "format",
            "minimum",
            "maximum",
            "multipleOf",
            "patternProperties",
            "propertyNames",
            "minItems",
            "maxItems",
            "uniqueItems",
            "$schema",
            "title",
        ]
        pipeline = SchemaPipeline(
            [
                RootRefInlineTransformer(),
                OpenAIUnionFlattenTransformer(),
                TypeEnforcerTransformer(),
                RemoveUnsupportedKeysTransformer(unsupported_keys),
                StrictObjectTransformer(),
                DeepSeekFallbackTransformer(),
                RefComplianceTransformer(),
            ]
        )
        return pipeline.run(schema)


class DeepSeekMessageConverter(ResponsesMessageConverter):
    """DeepSeek 专属 Responses 消息转换器"""

    def __init__(self, api_type: str = "deepseek"):
        super().__init__(api_type=api_type)

    async def convert_messages_async(
        self, messages: list[LLMMessage]
    ) -> list[dict[str, Any]]:
        input_items = await super().convert_messages_async(messages)

        image_parts = [
            p for msg in messages for p in msg.content if isinstance(p, ImagePart)
        ]
        img_idx = 0

        for item in input_items:
            if item.get("role") in ("user", "developer") and "content" in item:
                for c in item["content"]:
                    if c.get("type") == "input_image":
                        if img_idx < len(image_parts):
                            part = image_parts[img_idx]
                            img_idx += 1
                            if getattr(part, "media_resolution", None):
                                c["detail"] = str(part.media_resolution).lower()
        return input_items


class DeepSeekConfigMapper(ResponsesConfigMapper):
    """DeepSeek 的专属配置映射器"""

    def map_config(
        self,
        config: GenerationConfig,
        model_detail: ModelDetail | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> dict[str, Any]:
        """拦截并处理 DeepSeek 目前无法严格遵循复杂 json_schema 的问题"""
        if (
            config.output.response_format == ResponseFormat.JSON
            and config.output.response_schema
        ):
            config.output.response_schema = None

        return super().map_config(config, model_detail, capabilities)


class DeepSeekTextHandler(OpenAIResponsesTextHandler):
    """DeepSeek 专有 Responses 文本对话处理器，组装所有定制化子件"""

    def __init__(self, api_type: str = "deepseek"):
        super().__init__(api_type=api_type)
        self.converter = DeepSeekMessageConverter(api_type=api_type)
        self.serializer = DeepSeekToolSerializer(api_type=api_type)
        self.mapper = DeepSeekConfigMapper(api_type=api_type)
        self.parser = ResponsesResponseParser()


class DeepSeekAdapter(OpenAIResponsesAdapter):
    """DeepSeek Responses API 适配器"""

    def __init__(self):
        super().__init__()
        self.text_handler = DeepSeekTextHandler(api_type=self.api_type)

    @property
    def log_sanitization_context(self) -> str:
        """复用 Responses API 的日志清洗上下文。"""
        return "openai_responses_request"

    @property
    def api_type(self) -> str:
        """适配器主类型标识。"""
        return "deepseek"

    @property
    def supported_api_types(self) -> list[str]:
        """当前适配器支持的 API 类型列表。"""
        return ["deepseek"]
