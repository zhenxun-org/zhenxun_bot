from typing import Any

from zhenxun.services.ai.core.models import ModelCapabilities
from zhenxun.services.ai.llm.adapters.handlers.openai_handlers import (
    OpenAIResponsesTextHandler,
    ResponsesToolSerializer,
)
from zhenxun.services.ai.llm.adapters.openai import OpenAIResponsesAdapter


class GrokToolSerializer(ResponsesToolSerializer):
    """Grok 专属工具序列化器"""

    def serialize_server_tools(
        self, tools: list[Any], capabilities: ModelCapabilities
    ) -> list[dict[str, Any]]:
        res = []
        for t in tools:
            type_id = getattr(t, "type_id", "unknown")
            if type_id not in capabilities.supported_native_tools:
                continue

            if type_id == "web_search":
                res.append({"type": "web_search"})
            elif type_id == "x_search":
                payload = {"type": "x_search"}
                if getattr(t, "allowed_x_handles", None):
                    payload["allowed_x_handles"] = t.allowed_x_handles
                if getattr(t, "excluded_x_handles", None):
                    payload["excluded_x_handles"] = t.excluded_x_handles
                res.append(payload)
            elif type_id == "code_execution":
                res.append({"type": "code_interpreter"})
            elif type_id == "file_search":
                res.append({"type": "file_search"})
        return res


class GrokTextHandler(OpenAIResponsesTextHandler):
    """Grok 文本处理器"""

    def __init__(self, api_type: str = "grok"):
        super().__init__(api_type=api_type)
        self.serializer = GrokToolSerializer(api_type=api_type)


class GrokAdapter(OpenAIResponsesAdapter):
    """xAI Grok API 适配器"""

    def __init__(self):
        super().__init__()
        self.text_handler = GrokTextHandler(api_type=self.api_type)

    @property
    def api_type(self) -> str:
        return "grok"

    @property
    def supported_api_types(self) -> list[str]:
        return ["grok"]
