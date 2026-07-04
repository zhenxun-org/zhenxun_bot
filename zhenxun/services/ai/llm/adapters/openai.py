"""
OpenAI API 适配器

支持 OpenAI、智谱AI 等 OpenAI 兼容的 API 服务。
"""

from __future__ import annotations

from abc import abstractmethod

from zhenxun.services.ai.core.models import ModelIdentity

from .base import (
    BaseAdapter,
    RequestData,
)
from .handlers.openai_handlers import (
    CompositeOpenAITextHandler,
    OpenAIAudioHandler,
    OpenAIEmbeddingHandler,
    OpenAIImageHandler,
    OpenAIRerankHandler,
)


class OpenAICompatAdapter(BaseAdapter):
    """
    OpenAI 兼容 API 适配器基类。
    保留端点获取等基础逻辑。
    """

    @property
    def log_sanitization_context(self) -> str:
        """返回 OpenAI 系列请求的日志清洗上下文。"""
        return "openai_request"

    @abstractmethod
    def get_chat_endpoint(self, identity: ModelIdentity) -> str:
        """子类必须实现，返回 chat completions 的端点"""
        pass

    def get_embedding_endpoint(self, identity: ModelIdentity) -> str:
        """返回 embeddings 的默认端点"""
        return "/v1/embeddings"

    async def prepare_simple_request(
        self,
        identity: ModelIdentity,
        api_key: str,
        prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> RequestData:
        """准备简单文本生成请求"""
        from zhenxun.services.ai.core.messages import (
            AssistantMessage,
            SystemMessage,
            TextPart,
            UserMessage,
        )

        messages = []
        if history:
            for msg in history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    messages.append(SystemMessage(content=[TextPart(text=content)]))
                elif role == "assistant":
                    messages.append(AssistantMessage(content=[TextPart(text=content)]))
                else:
                    messages.append(UserMessage(content=[TextPart(text=content)]))
        messages.append(UserMessage(content=[TextPart(text=prompt)]))
        config = identity.generation_config

        from zhenxun.services.ai.core.messages import ChatRequest

        request = ChatRequest(messages=messages, config=config)
        return await self.prepare_advanced_request(
            identity=identity,
            api_key=api_key,
            request=request,
        )


class OpenAIAdapter(OpenAICompatAdapter):
    """OpenAI 系列适配器，统一装配文本/图像/嵌入/重排处理链。"""

    def __init__(self):
        """初始化并挂载复合文本处理器与通用多模态处理器。"""
        super().__init__()
        self.text_handler = CompositeOpenAITextHandler(api_type=self.api_type)
        self.image_handler = OpenAIImageHandler()
        self.embedding_handler = OpenAIEmbeddingHandler()
        self.rerank_handler = OpenAIRerankHandler()
        self.audio_handler = OpenAIAudioHandler()

    @property
    def api_type(self) -> str:
        """适配器主类型标识。"""
        return "openai"

    @property
    def supported_api_types(self) -> list[str]:
        """支持的 API 类型及别名。"""
        return [
            "openai",
            "openai_responses",
        ]

    def get_chat_endpoint(self, identity: ModelIdentity) -> str:
        """返回聊天完成端点"""
        current_api_type = identity.api_type

        if current_api_type == "openai_responses":
            return "/v1/responses"
        if current_api_type == "doubao":
            return "/api/v3/chat/completions"
        return "/v1/chat/completions"

    def get_embedding_endpoint(self, identity: ModelIdentity) -> str:
        """返回嵌入端点。"""
        return "/v1/embeddings"
