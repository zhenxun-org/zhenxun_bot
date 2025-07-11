"""
OpenAI API 适配器

支持 OpenAI、DeepSeek、智谱AI 和其他 OpenAI 兼容的 API 服务。
"""

from typing import TYPE_CHECKING

from .base import OpenAICompatAdapter

if TYPE_CHECKING:
    from ..service import LLMModel


class OpenAIAdapter(OpenAICompatAdapter):
    """OpenAI兼容API适配器"""

    @property
    def api_type(self) -> str:
        return "openai"

    @property
    def supported_api_types(self) -> list[str]:
        return ["openai", "deepseek", "zhipu", "general_openai_compat", "ark"]

    def get_chat_endpoint(self, model: "LLMModel") -> str:
        """返回聊天完成端点"""
        if model.api_type == "ark":
            return "/api/v3/chat/completions"
        if model.api_type == "zhipu":
            return "/api/paas/v4/chat/completions"
        return "/v1/chat/completions"

    def get_embedding_endpoint(self, model: "LLMModel") -> str:
        """根据API类型返回嵌入端点"""
        if model.api_type == "zhipu":
            return "/v4/embeddings"
        return "/v1/embeddings"
