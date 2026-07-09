from typing import Any

from zhenxun.services.ai.core.models import (
    ModelCapabilities,
    ModelDetail,
    ModelIdentity,
)
from zhenxun.services.ai.core.options import GenerationConfig

from .handlers.openai_handlers import (
    OpenAIConfigMapper,
    OpenAITextHandler,
    OpenAIToolSerializer,
)
from .openai import OpenAICompatAdapter


class DeepSeekToolSerializer(OpenAIToolSerializer):
    """
    专门针对 DeepSeek 的工具序列化器。
    负责抹平 Pydantic Schema 与 DeepSeek Strict Mode 之间的差异。
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


class DeepSeekConfigMapper(OpenAIConfigMapper):
    """DeepSeek 的专属配置映射器"""

    def map_config(
        self,
        config: GenerationConfig,
        model_detail: ModelDetail | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> dict[str, Any]:
        """映射生成参数并处理 DeepSeek 专有 `thinking` 与响应格式差异。"""
        params = super().map_config(config, model_detail, capabilities)

        if "response_format" in params:
            rf = params["response_format"]
            if isinstance(rf, dict) and rf.get("type") == "json_schema":
                params["response_format"] = {"type": "json_object"}

        return params


class DeepSeekTextHandler(OpenAITextHandler):
    """DeepSeek 专有文本处理器，替换了特定序列化组件"""

    def __init__(self, api_type: str = "deepseek"):
        """替换 OpenAI 默认组件为 DeepSeek 专用实现。"""
        super().__init__(api_type=api_type)
        self.serializer = DeepSeekToolSerializer(api_type=api_type)
        self.mapper = DeepSeekConfigMapper(api_type=api_type)


class DeepSeekAdapter(OpenAICompatAdapter):
    """DeepSeek 官方 API 适配器"""

    def __init__(self):
        """初始化 DeepSeek 适配器并挂载文本处理器。"""
        super().__init__()
        self.text_handler = DeepSeekTextHandler(api_type=self.api_type)

    @property
    def log_sanitization_context(self) -> str:
        """返回 DeepSeek 请求日志清洗上下文。"""
        return "openai_request"

    @property
    def api_type(self) -> str:
        """适配器主类型标识。"""
        return "deepseek"

    @property
    def supported_api_types(self) -> list[str]:
        """当前适配器支持的 API 类型列表。"""
        return ["deepseek"]

    def get_chat_endpoint(self, identity: ModelIdentity) -> str:
        """返回对话端点，优先使用模型级自定义端点。"""
        return "/v1/chat/completions"
