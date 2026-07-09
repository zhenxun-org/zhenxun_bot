"""
LLM 适配器基类和通用数据结构
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import inspect
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid

import httpx
from pydantic import BaseModel, Field

from zhenxun.configs.path_config import TEMP_PATH
from zhenxun.services.ai.core.engine.token_counter import parse_usage_info
from zhenxun.services.ai.core.exceptions import (
    AuthenticationException,
    ConfigurationException,
    ContentFilteredException,
    ContextLengthExceededException,
    InvalidRequestException,
    LLMException,
    LocationNotSupportedException,
    QuotaExceededException,
    RateLimitException,
    ResponseParseException,
    UpstreamServerException,
)
from zhenxun.services.ai.core.messages import (
    AudioResponse,
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    ImagePart,
    ImageRequest,
    ImageResponse,
    LLMContentPart,
    RerankRequest,
    RerankResponse,
    RerankResult,
    SpeechRequest,
    TextPart,
    ThoughtPart,
)
from zhenxun.services.ai.core.models import ModelIdentity
from zhenxun.services.ai.utils.logger import log_llm as logger
from zhenxun.utils.log_sanitizer import sanitize_for_logging

if TYPE_CHECKING:
    from .handlers.base import (
        BaseAudioHandler,
        BaseEmbeddingHandler,
        BaseImageHandler,
        BaseRerankHandler,
        BaseTextHandler,
    )


class RequestData(BaseModel):
    """标准化的请求载体，用于向上层 HTTP 客户端传递请求参数。"""

    method: str = "POST"
    """请求的 HTTP 方法，默认 'POST'"""
    url: str
    """请求的目标 HTTP URL"""
    headers: dict[str, str]
    """请求的 HTTP 头部键值对"""
    body: dict[str, Any]
    """请求的 HTTP 载荷体 JSON 字典"""
    files: dict[str, Any] | list[tuple[str, Any]] | None = None
    """要上传的多媒体或二进制文件字典"""


class ResponseData(BaseModel):
    """标准化的响应载体，统一承接文本、多模态与附加元数据。"""

    content_parts: list[LLMContentPart] = Field(default_factory=list)
    """大模型生成的结构化内容片段列表（如文本、图片、工具调用）"""
    usage_info: dict[str, Any] | None = None
    """底层 API Token 消耗使用统计字典"""
    raw_response: dict[str, Any] | None = None
    """接口返回的原始 JSON 响应字典"""
    grounding_metadata: Any | None = None
    """Gemini 等模型特有的 Grounding 搜索依据元数据"""
    cache_info: Any | None = None
    """接口缓存的命中与生成情况等元数据"""

    @property
    def text(self) -> str:
        """提取并拼接所有 `TextPart` 文本内容。"""
        return "".join(
            p.text for p in self.content_parts if isinstance(p, TextPart)
        ).strip()

    @text.setter
    def text(self, value: str):
        """设置首个 `TextPart`，不存在则追加新的 `TextPart`。"""
        for p in self.content_parts:
            if isinstance(p, TextPart):
                p.text = value
                return
        self.content_parts.append(TextPart(text=value))

    @property
    def thought_text(self) -> str | None:
        """提取并拼接所有思维片段文本，未命中则返回 `None`。"""
        thoughts = [
            p.thought_text for p in self.content_parts if isinstance(p, ThoughtPart)
        ]
        return "\n".join(thoughts).strip() if thoughts else None

    @property
    def thought_signature(self) -> str | None:
        """从末尾向前查找思维签名，用于后续连续推理场景。"""
        for p in reversed(self.content_parts):
            if (
                hasattr(p, "metadata")
                and p.metadata
                and "thought_signature" in p.metadata
            ):
                return p.metadata["thought_signature"]
        return None

    @property
    def images(self) -> list[bytes | Path | str]:
        """收集图片内容，按 URL / 原始字节 / 本地路径顺序返回。"""
        imgs = []
        for p in self.content_parts:
            if isinstance(p, ImagePart):
                if p.url:
                    imgs.append(p.url)
                elif p.raw:
                    imgs.append(p.raw)
                elif p.path:
                    imgs.append(p.path)
        return imgs

    @images.setter
    def images(self, value: list[bytes | Path | str]):
        """覆盖图片片段并按输入类型重建 `ImagePart` 列表。"""
        self.content_parts = [
            p for p in self.content_parts if not isinstance(p, ImagePart)
        ]
        for img in value:
            if isinstance(img, str) and img.startswith(("http://", "https://")):
                self.content_parts.append(ImagePart(url=img))
            elif isinstance(img, bytes):
                self.content_parts.append(ImagePart(raw=img))
            else:
                self.content_parts.append(ImagePart(path=Path(img)))

    code_execution_results: list[dict[str, Any]] | None = None
    search_results: list[dict[str, Any]] | None = None
    function_calls: list[dict[str, Any]] | None = None
    safety_ratings: list[dict[str, Any]] | None = None
    citations: list[dict[str, Any]] | None = None


def process_image_data(image_data: bytes) -> bytes | Path:
    """处理图片二进制数据：超过 2MB 时落盘并返回文件路径。"""
    max_inline_size = 2 * 1024 * 1024
    if len(image_data) > max_inline_size:
        save_dir = TEMP_PATH / "llm"
        save_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{uuid.uuid4()}.png"
        file_path = save_dir / file_name
        file_path.write_bytes(image_data)
        logger.info(
            f"图片数据过大 ({len(image_data)} bytes)，已保存到临时文件: {file_path}",
            "LLMAdapter",
        )
        return file_path.resolve()
    return image_data


class BaseAdapter(ABC):
    """
    LLM API适配器基类 (门面模式 Facade)。
    负责维护厂商级别的通用配置(如 URL 拼接、请求头构建、通用错误拦截)，
    而将具体的模态序列化与反序列化逻辑委派给各路 Handler。
    """

    text_handler: "BaseTextHandler | None" = None
    image_handler: "BaseImageHandler | None" = None
    embedding_handler: "BaseEmbeddingHandler | None" = None
    rerank_handler: "BaseRerankHandler | None" = None
    audio_handler: "BaseAudioHandler | None" = None

    @property
    def log_sanitization_context(self) -> str:
        """用于日志清洗的上下文名称，默认 'default'"""
        return "default"

    @property
    @abstractmethod
    def api_type(self) -> str:
        """API类型标识"""
        pass

    @property
    @abstractmethod
    def supported_api_types(self) -> list[str]:
        """支持的API类型列表"""
        pass

    async def prepare_payload(
        self, identity: ModelIdentity, api_key: str, request: Any
    ) -> RequestData:
        """泛型请求构建分发入口 (Polymorphic Dispatch)"""
        dispatch = {
            ChatRequest: self.prepare_advanced_request,
            EmbeddingRequest: self.prepare_embedding_request,
            RerankRequest: self.prepare_rerank_request,
            ImageRequest: self.prepare_image_request,
            SpeechRequest: self.prepare_speech_request,
        }
        handler = dispatch.get(type(request))
        if not handler:
            raise ValueError(
                f"适配器 {self.api_type} 不支持的请求类型: {type(request)}"
            )

        res = handler(identity, api_key, request)
        if inspect.isawaitable(res):
            return await res
        return res

    async def parse_payload(
        self, identity: ModelIdentity, request: Any, raw_response: httpx.Response
    ) -> Any:
        """泛型响应解析分发入口 (Polymorphic Dispatch)"""
        if isinstance(request, SpeechRequest):
            res = self.parse_speech_response(identity, raw_response)
            if inspect.isawaitable(res):
                return await res
            return res

        response_bytes = await raw_response.aread()
        logger.debug(f"📦 响应体已完整读取 ({len(response_bytes)} bytes)")
        try:
            response_json = json.loads(response_bytes)
        except json.JSONDecodeError:
            raise ResponseParseException(
                "API 返回了非 JSON 格式的内容，可能是 URL 路径错误或中转站配置异常。",
                details={
                    "raw_response": response_bytes.decode("utf-8", errors="ignore")[
                        :500
                    ]
                },
            )

        sanitizer_req_context = self.log_sanitization_context
        sanitizer_resp_context = sanitizer_req_context.replace("_request", "_response")
        if sanitizer_resp_context == sanitizer_req_context:
            sanitizer_resp_context = f"{sanitizer_req_context}_response"

        sanitized_response = sanitize_for_logging(
            response_json, context=sanitizer_resp_context
        )
        response_json_str = json.dumps(sanitized_response, ensure_ascii=False, indent=2)
        logger.debug(f"📋 响应JSON: {response_json_str}")

        dispatch = {
            EmbeddingRequest: self._parse_embedding_payload,
            RerankRequest: self._parse_rerank_payload,
            ImageRequest: self._parse_image_payload,
            ChatRequest: self._parse_chat_payload,
        }
        handler = dispatch.get(type(request))
        if not handler:
            raise ValueError(
                f"适配器 {self.api_type} 不支持的请求类型解析: {type(request)}"
            )

        return handler(identity, response_json)

    def _parse_embedding_payload(
        self, identity: ModelIdentity, response_json: dict
    ) -> EmbeddingResponse:
        self.validate_embedding_response(response_json)
        embeddings = self.parse_embedding_response(response_json)
        return EmbeddingResponse(
            embeddings=embeddings,
            usage=parse_usage_info(
                response_json.get("usage") or response_json.get("usageMetadata")
            ),
            model_name=identity.model_name,
        )

    def _parse_rerank_payload(
        self, identity: ModelIdentity, response_json: dict
    ) -> RerankResponse:
        return RerankResponse(results=self.parse_rerank_response(response_json))

    def _parse_image_payload(
        self, identity: ModelIdentity, response_json: dict
    ) -> ImageResponse:
        response_data = self.parse_image_response(response_json)
        return ImageResponse(
            content_parts=response_data.content_parts, raw_response=response_json
        )

    def _parse_chat_payload(
        self, identity: ModelIdentity, response_json: dict
    ) -> ChatResponse:
        response_data = self.parse_response(identity, response_json, is_advanced=True)
        return ChatResponse(
            content_parts=response_data.content_parts,
            usage_info=response_data.usage_info,
            raw_response=response_data.raw_response,
            grounding_metadata=response_data.grounding_metadata,
        )

    async def prepare_simple_request(
        self,
        identity: ModelIdentity,
        api_key: str,
        prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> RequestData:
        """准备简单文本生成请求

        默认实现：将简单请求转换为高级请求格式
        子类可以重写此方法以提供特定的优化实现
        """
        from zhenxun.services.ai.core.messages import (
            AssistantMessage,
            SystemMessage,
            TextPart,
            UserMessage,
        )

        messages: list[Any] = []

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

        return await self.prepare_advanced_request(
            identity=identity,
            api_key=api_key,
            request=ChatRequest(messages=messages, config=config),
        )

    async def prepare_advanced_request(
        self,
        identity: ModelIdentity,
        api_key: str,
        request: ChatRequest,
    ) -> RequestData:
        """准备高级对话请求并委派给 `text_handler` 完成序列化。"""
        if self.text_handler:
            return await self.text_handler.prepare_text_request(
                adapter=self,
                identity=identity,
                api_key=api_key,
                request=request,
            )
        raise NotImplementedError(
            f"API 类型 '{self.api_type}' 未装配 TextHandler，暂不支持文本对话能力。"
        )

    def parse_response(
        self,
        identity: ModelIdentity,
        response_json: dict[str, Any],
        is_advanced: bool = False,
    ) -> ResponseData:
        """解析文本响应并委派给 `text_handler`。"""
        if self.text_handler:
            return self.text_handler.parse_text_response(
                adapter=self,
                identity=identity,
                response_json=response_json,
                is_advanced=is_advanced,
            )
        raise NotImplementedError(f"API 类型 '{self.api_type}' 未装配 TextHandler。")

    async def prepare_embedding_request(
        self,
        identity: ModelIdentity,
        api_key: str,
        request: EmbeddingRequest,
    ) -> RequestData:
        """准备文本/多模态嵌入请求并委派给 `embedding_handler`。"""
        if self.embedding_handler:
            return await self.embedding_handler.prepare_embedding_request(
                adapter=self,
                identity=identity,
                api_key=api_key,
                request=request,
            )
        raise NotImplementedError(
            f"API 类型 '{self.api_type}' 未装配 EmbeddingHandler，暂不支持向量嵌入。"
        )

    def parse_embedding_response(
        self, response_json: dict[str, Any]
    ) -> list[list[float]]:
        """解析文本嵌入响应并委派给 `embedding_handler`。"""
        if self.embedding_handler:
            return self.embedding_handler.parse_embedding_response(
                adapter=self, response_json=response_json
            )
        raise NotImplementedError(
            f"API 类型 '{self.api_type}' 未装配 EmbeddingHandler。"
        )

    def prepare_rerank_request(
        self,
        identity: ModelIdentity,
        api_key: str,
        request: RerankRequest,
    ) -> RequestData:
        """准备重排请求并委派给 `rerank_handler`。"""
        if self.rerank_handler:
            return self.rerank_handler.prepare_rerank_request(
                adapter=self,
                identity=identity,
                api_key=api_key,
                request=request,
            )
        raise NotImplementedError(
            f"API 类型 '{self.api_type}' 未装配 RerankHandler，暂不支持文本重排。"
        )

    def parse_rerank_response(
        self, response_json: dict[str, Any]
    ) -> list[RerankResult]:
        """解析重排响应并委派给 `rerank_handler`。"""
        if self.rerank_handler:
            return self.rerank_handler.parse_rerank_response(
                adapter=self, response_json=response_json
            )
        raise NotImplementedError(f"API 类型 '{self.api_type}' 未装配 RerankHandler。")

    def prepare_image_request(
        self,
        identity: ModelIdentity,
        api_key: str,
        request: ImageRequest,
    ) -> RequestData:
        """准备图像请求并委派给 `image_handler`。"""
        if self.image_handler:
            return self.image_handler.prepare_image_request(
                adapter=self,
                identity=identity,
                api_key=api_key,
                request=request,
            )
        raise NotImplementedError(
            f"API 类型 '{self.api_type}' 未装配 ImageHandler，暂不支持图像生成。"
        )

    def parse_image_response(self, response_json: dict[str, Any]) -> ResponseData:
        """解析图像响应并委派给 `image_handler`。"""
        if self.image_handler:
            return self.image_handler.parse_image_response(
                adapter=self, response_json=response_json
            )
        raise NotImplementedError(f"API 类型 '{self.api_type}' 未装配 ImageHandler。")

    def prepare_speech_request(
        self,
        identity: ModelIdentity,
        api_key: str,
        request: SpeechRequest,
    ) -> RequestData:
        """准备语音生成请求并委派给 `audio_handler`。"""
        if self.audio_handler:
            return self.audio_handler.prepare_speech_request(
                adapter=self,
                identity=identity,
                api_key=api_key,
                request=request,
            )
        raise NotImplementedError(
            f"API 类型 '{self.api_type}' 未装配 AudioHandler，暂不支持语音生成。"
        )

    async def parse_speech_response(
        self, identity: ModelIdentity, raw_response: httpx.Response
    ) -> AudioResponse:
        """解析语音响应并委派给 `audio_handler`"""
        if self.audio_handler:
            return await self.audio_handler.parse_speech_response(
                adapter=self, identity=identity, raw_response=raw_response
            )
        raise NotImplementedError(f"API 类型 '{self.api_type}' 未装配 AudioHandler。")

    def validate_embedding_response(self, response_json: dict[str, Any]) -> None:
        """验证嵌入接口响应，检测 `error` 并转换为统一异常。"""
        if response_json.get("error"):
            error_info = response_json["error"]
            msg = (
                error_info.get("message", str(error_info))
                if isinstance(error_info, dict)
                else str(error_info)
            )
            raise UpstreamServerException(
                f"嵌入API错误: {msg}",
                details=response_json,
            )

    def get_api_url(self, identity: ModelIdentity, endpoint: str) -> str:
        """拼接最终请求 URL，兼容 `path_prefix` 与端点前后斜杠。"""
        if not identity.api_base:
            raise ConfigurationException(
                f"模型 {identity.model_name} 的 api_base 未设置",
            )

        base_url = identity.api_base.rstrip("/")
        prefix = identity.path_prefix.strip("/") if identity.path_prefix else ""
        ep = endpoint.lstrip("/")

        if prefix:
            return f"{base_url}/{prefix}/{ep}"
        return f"{base_url}/{ep}"

    def get_base_headers(self, api_key: str) -> dict[str, str]:
        """构建默认请求头，包含 UA、JSON 类型与 Bearer 鉴权。"""
        from zhenxun.utils.user_agent import get_user_agent

        headers = get_user_agent()
        headers.update(
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
        )
        return headers

    def validate_response(self, response_json: dict[str, Any]) -> None:
        """统一校验文本/多模态响应并映射平台错误码。"""
        if response_json.get("error"):
            error_info = response_json["error"]

            error_message = str(error_info)
            if isinstance(error_info, dict):
                error_message = error_info.get("message", error_message)
                error_code = error_info.get("code", "unknown")

                if (
                    error_code in ("invalid_api_key", "authentication_failed")
                    or "permission" in error_message.lower()
                ):
                    raise AuthenticationException(
                        f"鉴权失败: {error_message}", details={"api_error": error_info}
                    )
                elif error_code in ("insufficient_quota", "quota_exceeded"):
                    raise QuotaExceededException(
                        f"配额耗尽: {error_message}", details={"api_error": error_info}
                    )
                elif error_code == "rate_limit_exceeded":
                    raise RateLimitException(
                        f"请求限流: {error_message}", details={"api_error": error_info}
                    )
                elif error_code in ("model_not_found", "invalid_model"):
                    raise ConfigurationException(
                        f"模型配置错误: {error_message}",
                        details={"api_error": error_info},
                    )
                elif error_code in (
                    "context_length_exceeded",
                    "max_tokens_exceeded",
                    "1261",
                ):
                    raise ContextLengthExceededException(
                        f"上下文超限: {error_message}",
                        details={"api_error": error_info},
                    )
                elif error_code in ("invalid_request_error", "invalid_parameter"):
                    raise InvalidRequestException(
                        f"请求参数错误: {error_message}",
                        details={"api_error": error_info},
                    )

            raise UpstreamServerException(
                f"API请求报错: {error_message}",
                details={"api_error": error_info},
            )

        if "candidates" in response_json:
            candidates = response_json.get("candidates", [])
            if candidates:
                candidate = candidates[0]
                finish_reason = candidate.get("finishReason")
                if finish_reason in ["SAFETY", "RECITATION"]:
                    raise ContentFilteredException(
                        f"内容被模型安全策略过滤: {finish_reason}",
                        details={
                            "finish_reason": finish_reason,
                        },
                    )

        if not response_json:
            raise UpstreamServerException(
                "API返回空响应",
                details={"response": response_json},
            )

    def handle_http_error(self, response: httpx.Response) -> LLMException | None:
        """
        处理 HTTP 错误响应。
        如果响应状态码表示成功 (200)，返回 None；否则构造 LLMException 供外部捕获。
        """
        if response.status_code == 200:
            return None

        error_text = response.content.decode("utf-8", errors="ignore")
        error_status = ""
        error_msg = error_text
        try:
            error_json = json.loads(error_text)
            if isinstance(error_json, dict) and "error" in error_json:
                error_info = error_json["error"]
                if isinstance(error_info, dict):
                    error_msg = error_info.get("message", error_msg)
                    raw_status = error_info.get("status") or error_info.get("code")
                    error_status = str(raw_status) if raw_status is not None else ""
                elif error_info is not None:
                    error_msg = str(error_info)
                    error_status = error_msg
        except Exception:
            pass

        status_upper = error_status.upper() if error_status else ""
        text_upper = error_text.upper()

        if response.status_code == 400:
            if (
                "FAILED_PRECONDITION" in status_upper
                or "LOCATION IS NOT SUPPORTED" in text_upper
            ):
                return LocationNotSupportedException(
                    "当前地区不支持该服务", details={"response": error_text}
                )
            elif "API_KEY_INVALID" in text_upper or "API KEY NOT VALID" in text_upper:
                return AuthenticationException(
                    "API Key 无效", details={"response": error_text}
                )
            elif (
                status_upper
                in ["1261", "STRING_ABOVE_MAX_LENGTH", "CONTEXT_LENGTH_EXCEEDED"]
                or "EXCEEDS MAX LENGTH" in text_upper
                or "STRING TOO LONG" in text_upper
            ):
                return ContextLengthExceededException(
                    "上下文超长", details={"response": error_text}
                )
            else:
                return InvalidRequestException(
                    f"参数错误: {error_msg}", details={"response": error_text}
                )
        elif response.status_code in [401, 403]:
            if "country" in error_msg.lower() or "unsupported" in error_msg.lower():
                return LocationNotSupportedException(
                    "地区受限", details={"response": error_text}
                )
            else:
                return AuthenticationException(
                    "鉴权失败/权限不足", details={"response": error_text}
                )
        elif response.status_code == 404:
            return ConfigurationException(
                "端点或模型未找到", details={"response": error_text}
            )
        elif response.status_code == 429:
            if (
                "RESOURCE_EXHAUSTED" in status_upper
                or "INSUFFICIENT_QUOTA" in status_upper
                or ("quota" in error_msg.lower() if error_msg else False)
            ):
                return QuotaExceededException(
                    "API 配额耗尽", details={"response": error_text}
                )
            else:
                return RateLimitException(
                    "请求频繁被限流", details={"response": error_text}
                )
        elif response.status_code in [402, 413]:
            return QuotaExceededException(
                "资源耗尽/文件过大", details={"response": error_text}
            )
        elif response.status_code >= 500:
            return UpstreamServerException(
                f"HTTP请求失败: {response.status_code} ({error_status or 'Unknown'})",
                details={
                    "status_code": response.status_code,
                    "response": error_text,
                },
            )

        return UpstreamServerException(
            f"未知网络错误 {response.status_code}: {error_msg}"
        )
