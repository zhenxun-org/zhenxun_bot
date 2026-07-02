from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from zhenxun.services.ai.core.models import ToolChoice
from zhenxun.services.ai.core.options import (
    GenerationConfig,
    LLMEmbeddingConfig,
    TTSConfig,
)
from zhenxun.utils.pydantic_compat import model_dump

from .models import LLMMessage
from .parts import EmbedBatch


class BaseRequest(BaseModel):
    """基础请求 DTO"""

    timeout: float | None = Field(default=None)
    extra: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_cache_hash_payload(self) -> dict[str, Any]:
        """获取用于计算缓存 Hash 的安全载荷，排除所有运行时动态变量"""
        request_dict = model_dump(self, exclude_none=True)

        if "config" in request_dict and isinstance(request_dict["config"], dict):
            custom_kwargs = request_dict["config"].get("custom_kwargs", {})
            if "__cache_ttl__" in custom_kwargs:
                custom_kwargs.pop("__cache_ttl__")

        if "extra" in request_dict:
            request_dict["extra"] = {
                k: v
                for k, v in request_dict["extra"].items()
                if not k.startswith("_")
                and k not in ("run_context", "output_processor", "guardrails")
            }
        return request_dict


class ChatRequest(BaseRequest):
    """对话生成请求 DTO"""

    messages: list[LLMMessage]
    config: GenerationConfig | None = None
    tools: list[Any] | None = None
    tool_choice: str | dict[str, Any] | ToolChoice | None = None

    def get_cache_hash_payload(self) -> dict[str, Any]:
        payload = super().get_cache_hash_payload()
        for msg in payload.get("messages", []):
            msg.pop("created_at", None)
            msg.pop("token_cost", None)
            msg.pop("metadata", None)

        if "tools" in payload and isinstance(payload["tools"], list):
            safe_tools = []
            for t in payload["tools"]:
                if isinstance(t, dict | str):
                    safe_tools.append(t)
                else:
                    safe_tools.append(getattr(t, "name", type(t).__name__))
            payload["tools"] = safe_tools
        return payload


class EmbeddingRequest(BaseRequest):
    """向量嵌入请求 DTO"""

    batch: EmbedBatch
    """向量嵌入的批次载体"""
    config: LLMEmbeddingConfig | None = None
    """向量嵌入配置"""


class ImageRequest(BaseRequest):
    """图像生成请求 DTO"""

    prompt: str
    """图像生成提示词"""
    images: list[Any] | None = None
    """输入参考图像列表"""
    config: GenerationConfig | None = None
    """图像生成配置"""


class SpeechRequest(BaseRequest):
    """语音合成请求 DTO"""

    input_text: str
    """待合成的文本内容"""
    voice: str | None = None
    """发音人/音色标识 (快捷覆盖参数，为空则使用模型默认音色)"""
    config: TTSConfig | None = None
    """语音合成配置"""


class RerankRequest(BaseRequest):
    """文本重排请求 DTO"""

    query: str
    """检索查询词"""
    documents: list[str | dict[str, str]]
    """待排序的候选文档列表"""
    top_n: int = 3
    """返回的最相关文档数量"""


__all__ = [
    "BaseRequest",
    "ChatRequest",
    "EmbeddingRequest",
    "ImageRequest",
    "RerankRequest",
    "SpeechRequest",
]
