from zhenxun.services.ai.core.messages import EmbeddingRequest
from zhenxun.services.ai.core.models import ModelIdentity
from zhenxun.services.ai.core.options import LLMEmbeddingConfig
from zhenxun.services.ai.utils.logger import log_llm as logger

from .base import BaseAdapter, RequestData
from .handlers.openai_handlers import (
    OpenAIEmbeddingHandler,
    OpenAIRerankHandler,
)
from .openai import OpenAICompatAdapter


class JinaEmbeddingHandler(OpenAIEmbeddingHandler):
    """Jina 专属文本/多模态嵌入处理器"""

    async def prepare_embedding_request(
        self,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        api_key: str,
        request: EmbeddingRequest,
    ) -> RequestData:
        batch = request.batch
        config = request.config or LLMEmbeddingConfig()
        endpoint = getattr(adapter, "get_embedding_endpoint")(identity)
        url = adapter.get_api_url(identity, endpoint)
        headers = adapter.get_base_headers(api_key)

        is_omni = "omni" in identity.model_name.lower()
        inputs_payload = []

        if is_omni:
            from zhenxun.services.ai.core.messages import (
                AudioPart,
                FilePart,
                ImagePart,
                TextPart,
                VideoPart,
            )

            for payload in batch.payloads:
                jina_content = []
                for part in payload.parts:
                    if isinstance(part, TextPart):
                        jina_content.append({"text": part.text})
                    elif isinstance(part, ImagePart):
                        if part.url:
                            jina_content.append({"image": part.url})
                        else:
                            jina_content.append(
                                {"image": await part.get_data_uri("image/png")}
                            )
                    elif isinstance(part, AudioPart):
                        if part.url:
                            jina_content.append({"audio": part.url})
                        else:
                            jina_content.append(
                                {"audio": await part.get_data_uri("audio/mp3")}
                            )
                    elif isinstance(part, VideoPart):
                        if part.url:
                            jina_content.append({"video": part.url})
                        else:
                            jina_content.append(
                                {"video": await part.get_data_uri("video/mp4")}
                            )
                    elif isinstance(part, FilePart):
                        logger.warning(
                            f"Jina 暂不明确支持 Base64 内联 "
                            f"{type(part).__name__}，已忽略。"
                        )

                if not jina_content:
                    jina_content.append({"text": " "})

                inputs_payload.append({"content": jina_content})
        else:
            inputs_payload = batch.to_text_only(
                f"{identity.model_name} (API: {adapter.api_type})"
            )

        body = {
            "model": identity.model_name,
            "input": inputs_payload,
        }

        if config.output_dimensionality:
            body["dimensions"] = config.output_dimensionality

        if config.task_type:
            task_mapping = {
                "RETRIEVAL_QUERY": "retrieval.query",
                "RETRIEVAL_DOCUMENT": "retrieval.passage",
                "SEMANTIC_SIMILARITY": "text-matching",
                "CLASSIFICATION": "classification",
                "CLUSTERING": "clustering",
            }
            body["task"] = task_mapping.get(config.task_type, config.task_type)

        if config.encoding_format and config.encoding_format != "float":
            body["embedding_type"] = config.encoding_format

        return RequestData(url=url, headers=headers, body=body)


class JinaAdapter(OpenAICompatAdapter):
    """Jina API 专有适配器 (仅支持 Embedding 和 Rerank)"""

    def __init__(self):
        super().__init__()
        self.text_handler = None
        self.embedding_handler = JinaEmbeddingHandler()
        self.rerank_handler = OpenAIRerankHandler()

    @property
    def api_type(self) -> str:
        """适配器主类型标识。"""
        return "jina"

    @property
    def supported_api_types(self) -> list[str]:
        """当前适配器支持的 API 类型列表。"""
        return ["jina"]

    def get_chat_endpoint(self, identity: ModelIdentity) -> str:
        raise NotImplementedError("Jina API 专精于检索，暂不支持常规对话生成。")

    def get_embedding_endpoint(self, identity: ModelIdentity) -> str:
        return "/v1/embeddings"
