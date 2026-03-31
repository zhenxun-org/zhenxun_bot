"""
LLM 模型实现类

包含 LLM 模型的抽象基类和具体实现，负责与各种 AI 提供商的 API 交互。
"""

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Awaitable, Callable
import json
import re
import time
from typing import Any, Literal, TypeVar, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field

from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.log_sanitizer import sanitize_for_logging
from zhenxun.utils.pydantic_compat import dump_json_safely

from .adapters.base import BaseAdapter, RequestData, process_image_data
from .config import LLMGenerationConfig
from .config.generation import LLMEmbeddingConfig
from .config.providers import get_llm_config
from .core import (
    KeyStatusStore,
    LLMHttpClient,
    RetryConfig,
    _should_retry_llm_error,
    http_client_manager,
)
from .types import (
    LLMErrorCode,
    LLMException,
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    ModelDetail,
    ProviderConfig,
    ToolChoice,
)
from .types.capabilities import ModelCapabilities, ModelModality

T = TypeVar("T", bound=BaseModel)


def _sanitize_request_headers(headers: dict[str, Any]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    sensitive_parts = ("authorization", "token", "api-key", "api_key", "secret")
    for key, value in headers.items():
        key_text = str(key)
        value_text = str(value)
        lowered = key_text.lower()
        if any(part in lowered for part in sensitive_parts):
            if " " in value_text:
                prefix = value_text.split(" ", 1)[0]
                sanitized[key_text] = f"{prefix} ***"
            else:
                sanitized[key_text] = "***"
        else:
            sanitized[key_text] = value_text
    return sanitized


class LLMContext(BaseModel):
    """LLM 执行上下文，用于在中间件管道中传递请求状态"""

    messages: list[LLMMessage]
    config: LLMGenerationConfig | LLMEmbeddingConfig
    tools: list[Any] | None
    tool_choice: str | dict[str, Any] | ToolChoice | None
    timeout: float | None
    extra: dict[str, Any] = Field(default_factory=dict)
    request_type: Literal["generation", "embedding"] = "generation"
    runtime_state: dict[str, Any] = Field(
        default_factory=dict,
        description="中间件运行时的临时状态存储(api_key, retry_count等)",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


NextCall = Callable[[LLMContext], Awaitable[LLMResponse]]
LLMMiddleware = Callable[[LLMContext, NextCall], Awaitable[LLMResponse]]


class BaseLLMMiddleware(ABC):
    """LLM 中间件抽象基类"""

    @abstractmethod
    async def __call__(self, context: LLMContext, next_call: NextCall) -> LLMResponse:
        """
        执行中间件逻辑

        Args:
            context: 请求上下文，包含配置和运行时状态
            next_call: 调用链中的下一个处理函数

        Returns:
            LLMResponse: 模型响应结果
        """
        pass


class LLMModelBase(ABC):
    """LLM模型抽象基类"""

    @abstractmethod
    async def generate_response(
        self,
        messages: list[LLMMessage],
        config: LLMGenerationConfig | None = None,
        tools: list[Any] | None = None,
        tool_choice: str | dict[str, Any] | ToolChoice | None = None,
        timeout: float | None = None,
    ) -> LLMResponse:
        """生成高级响应"""
        pass

    @abstractmethod
    async def generate_embeddings(
        self,
        texts: list[str],
        config: LLMEmbeddingConfig,
    ) -> list[list[float]]:
        """生成文本嵌入向量"""
        pass


class LLMModel(LLMModelBase):
    """LLM 模型实现类"""

    def __init__(
        self,
        provider_config: ProviderConfig,
        model_detail: ModelDetail,
        key_store: KeyStatusStore,
        http_client: LLMHttpClient,
        capabilities: ModelCapabilities,
        config_override: LLMGenerationConfig | None = None,
    ):
        self.provider_config = provider_config
        self.model_detail = model_detail
        self.key_store = key_store
        self.http_client: LLMHttpClient = http_client
        self.capabilities = capabilities
        self._generation_config = config_override

        self.provider_name = provider_config.name
        self.api_type = provider_config.api_type
        self.api_base = provider_config.api_base
        self.api_keys = (
            [provider_config.api_key]
            if isinstance(provider_config.api_key, str)
            else provider_config.api_key
        )
        self.model_name = model_detail.model_name
        self.temperature = model_detail.temperature
        self.max_tokens = model_detail.max_tokens

        self._is_closed = False
        self._ref_count = 0
        self._middlewares: list[LLMMiddleware] = []

    def _has_modality(self, modality: ModelModality, is_input: bool = True) -> bool:
        target_set = (
            self.capabilities.input_modalities
            if is_input
            else self.capabilities.output_modalities
        )
        return modality in target_set

    @property
    def can_process_images(self) -> bool:
        """检查模型是否支持图片作为输入。"""
        return self._has_modality(ModelModality.IMAGE)

    @property
    def can_process_video(self) -> bool:
        """检查模型是否支持视频作为输入。"""
        return self._has_modality(ModelModality.VIDEO)

    @property
    def can_process_audio(self) -> bool:
        """检查模型是否支持音频作为输入。"""
        return self._has_modality(ModelModality.AUDIO)

    @property
    def can_generate_images(self) -> bool:
        """检查模型是否支持生成图片。"""
        return self._has_modality(ModelModality.IMAGE, is_input=False)

    @property
    def can_generate_audio(self) -> bool:
        """检查模型是否支持生成音频 (TTS)。"""
        return self._has_modality(ModelModality.AUDIO, is_input=False)

    @property
    def is_embedding_model(self) -> bool:
        """检查这是否是一个嵌入模型。"""
        return self.capabilities.is_embedding_model

    def add_middleware(self, middleware: LLMMiddleware) -> None:
        """注册一个中间件到处理管道的最外层"""
        self._middlewares.append(middleware)

    def _build_pipeline(self) -> NextCall:
        """
        构建完整的中间件调用链。顺序为：
        用户自定义中间件 -> Retry -> Logging -> KeySelection -> Network (终结者)
        """
        from .adapters import get_adapter_for_api_type

        client_settings = get_llm_config().client_settings
        retry_config = RetryConfig(
            max_retries=client_settings.max_retries,
            retry_delay=client_settings.retry_delay,
        )
        adapter = get_adapter_for_api_type(self.api_type)

        network_middleware = NetworkRequestMiddleware(self, adapter)

        async def terminal_handler(ctx: LLMContext) -> LLMResponse:
            async def _noop(_: LLMContext) -> LLMResponse:
                raise RuntimeError("NetworkRequestMiddleware 不应调用 next_call")

            return await network_middleware(ctx, _noop)

        def _wrap(middleware: LLMMiddleware, next_call: NextCall) -> NextCall:
            async def _handler(inner_ctx: LLMContext) -> LLMResponse:
                return await middleware(inner_ctx, next_call)

            return _handler

        handler: NextCall = terminal_handler
        handler = _wrap(
            KeySelectionMiddleware(self.key_store, self.provider_name, self.api_keys),
            handler,
        )
        handler = _wrap(
            LoggingMiddleware(self.provider_name, self.model_name),
            handler,
        )
        handler = _wrap(
            RetryMiddleware(retry_config, self.key_store),
            handler,
        )

        for middleware in reversed(self._middlewares):
            handler = _wrap(middleware, handler)

        return handler

    def _get_effective_api_type(self) -> str:
        """
        获取实际生效的 API 类型。
        主要用于 Smart 模式下，判断日志净化应该使用哪种格式。
        """
        if self.api_type != "smart":
            return self.api_type

        if self.model_detail.api_type:
            return self.model_detail.api_type
        if (
            "gemini" in self.model_name.lower()
            and "openai" not in self.model_name.lower()
        ):
            return "gemini"
        return "openai"

    async def _get_http_client(self) -> LLMHttpClient:
        """获取HTTP客户端"""
        if self.http_client.is_closed:
            logger.debug(
                f"LLMModel {self.provider_name}/{self.model_name} 的 HTTP 客户端已关闭,"
                "正在获取新的客户端"
            )
            self.http_client = await http_client_manager.get_client(
                self.provider_config
            )
        return self.http_client

    async def _select_api_key(self, failed_keys: set[str] | None = None) -> str:
        """选择可用的API密钥（使用轮询策略）"""
        if not self.api_keys:
            raise LLMException(
                f"提供商 {self.provider_name} 没有配置API密钥",
                code=LLMErrorCode.NO_AVAILABLE_KEYS,
            )

        selected_key = await self.key_store.get_next_available_key(
            self.provider_name, self.api_keys, failed_keys
        )

        if not selected_key:
            raise LLMException(
                f"提供商 {self.provider_name} 的所有API密钥当前都不可用",
                code=LLMErrorCode.NO_AVAILABLE_KEYS,
                details={
                    "total_keys": len(self.api_keys),
                    "failed_keys": len(failed_keys or set()),
                },
            )

        return selected_key

    async def close(self):
        """标记模型实例的当前使用周期结束"""
        if self._is_closed:
            return
        self._is_closed = True
        logger.debug(
            f"LLMModel实例的使用周期已结束: {self} (共享HTTP客户端状态不受影响)"
        )

    async def __aenter__(self):
        if self._is_closed:
            logger.debug(
                f"Re-entering context for closed LLMModel {self}. "
                f"Resetting _is_closed to False."
            )
            self._is_closed = False
        self._check_not_closed()
        self._ref_count += 1
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        _ = exc_type, exc_val, exc_tb
        self._ref_count -= 1
        if self._ref_count <= 0:
            self._ref_count = 0
            await self.close()

    def _check_not_closed(self):
        """检查实例是否已关闭"""
        if self._is_closed:
            raise RuntimeError(f"LLMModel实例已关闭: {self}")

    async def _execute_core_generation(self, context: LLMContext) -> LLMResponse:
        """
        [内核] 执行核心生成逻辑：构建管道并执行。
        此方法作为中间件管道的终点被调用。
        """
        pipeline_handler = self._build_pipeline()
        return await pipeline_handler(context)

    async def generate_response(
        self,
        messages: list[LLMMessage],
        config: LLMGenerationConfig | None = None,
        tools: list[Any] | None = None,
        tool_choice: str | dict[str, Any] | ToolChoice | None = None,
        timeout: float | None = None,
    ) -> LLMResponse:
        """
        生成高级响应 (支持中间件管道)。
        """
        self._check_not_closed()

        if self._generation_config and config:
            final_request_config = self._generation_config.merge_with(config)
        elif config:
            final_request_config = config
        else:
            final_request_config = self._generation_config or LLMGenerationConfig()

        normalized_tools: list[Any] | None = None
        if tools:
            if isinstance(tools, dict):
                normalized_tools = list(tools.values())
            elif isinstance(tools, list):
                normalized_tools = tools
            else:
                normalized_tools = [tools]

        context = LLMContext(
            messages=messages,
            config=final_request_config,
            tools=normalized_tools,
            tool_choice=tool_choice,
            timeout=timeout,
        )

        return await self._execute_core_generation(context)

    async def generate_embeddings(
        self,
        texts: list[str],
        config: LLMEmbeddingConfig | None = None,
    ) -> list[list[float]]:
        """生成文本嵌入向量"""
        self._check_not_closed()
        if not texts:
            return []

        final_config = config or LLMEmbeddingConfig()

        context = LLMContext(
            messages=[],
            config=final_config,
            tools=None,
            tool_choice=None,
            timeout=None,
            request_type="embedding",
            extra={"texts": texts},
        )

        pipeline = self._build_pipeline()
        response = await pipeline(context)
        embeddings = (
            response.cache_info.get("embeddings") if response.cache_info else None
        )
        if embeddings is None:
            raise LLMException(
                "嵌入请求未返回 embeddings 数据",
                code=LLMErrorCode.EMBEDDING_FAILED,
            )
        return embeddings

    def __str__(self) -> str:
        status = "closed" if self._is_closed else "active"
        return f"LLMModel({self.provider_name}/{self.model_name}, {status})"

    def __repr__(self) -> str:
        status = "closed" if self._is_closed else "active"
        return (
            f"LLMModel(provider={self.provider_name}, model={self.model_name}, "
            f"api_type={self.api_type}, status={status})"
        )


class RetryMiddleware(BaseLLMMiddleware):
    """
    重试中间件：处理异常捕获与重试循环
    """

    def __init__(self, retry_config: RetryConfig, key_store: KeyStatusStore):
        self.retry_config = retry_config
        self.key_store = key_store

    async def __call__(self, context: LLMContext, next_call: NextCall) -> LLMResponse:
        last_exception: Exception | None = None
        total_attempts = self.retry_config.max_retries + 1

        for attempt in range(total_attempts):
            try:
                context.runtime_state["attempt"] = attempt + 1
                return await next_call(context)

            except LLMException as e:
                last_exception = e
                api_key = context.runtime_state.get("api_key")

                if api_key:
                    status_code = e.details.get("status_code")
                    error_msg = f"({e.code.name}) {e.message}"
                    await self.key_store.record_failure(api_key, status_code, error_msg)

                if not _should_retry_llm_error(
                    e, attempt, self.retry_config.max_retries
                ):
                    raise e

                if attempt == total_attempts - 1:
                    raise e

                wait_time = self.retry_config.retry_delay
                if self.retry_config.exponential_backoff:
                    wait_time *= 2**attempt

                logger.warning(
                    f"请求失败，{wait_time:.2f}秒后重试"
                    f" (第{attempt + 1}/{self.retry_config.max_retries}次重试): {e}"
                )
                await asyncio.sleep(wait_time)

            except Exception as e:
                logger.error(f"非预期异常，停止重试: {e}", e=e)
                raise e

        if last_exception:
            raise last_exception
        raise LLMException("重试循环异常结束")


class KeySelectionMiddleware(BaseLLMMiddleware):
    """
    密钥选择中间件：负责轮询获取可用 API Key
    """

    def __init__(
        self, key_store: KeyStatusStore, provider_name: str, api_keys: list[str]
    ):
        self.key_store = key_store
        self.provider_name = provider_name
        self.api_keys = api_keys
        self._failed_keys: set[str] = set()

    async def __call__(self, context: LLMContext, next_call: NextCall) -> LLMResponse:
        selected_key = await self.key_store.get_next_available_key(
            self.provider_name, self.api_keys, exclude_keys=self._failed_keys
        )

        if not selected_key:
            raise LLMException(
                f"提供商 {self.provider_name} 无可用 API Key",
                code=LLMErrorCode.NO_AVAILABLE_KEYS,
            )

        context.runtime_state["api_key"] = selected_key

        try:
            response = await next_call(context)
            return response
        except LLMException as e:
            self._failed_keys.add(selected_key)
            masked = f"{selected_key[:8]}..."
            if isinstance(e.details, dict):
                e.details["api_key"] = masked
            raise e


class LoggingMiddleware(BaseLLMMiddleware):
    """
    日志中间件：负责请求和响应的日志记录与脱敏
    """

    def __init__(
        self, provider_name: str, model_name: str, log_context: str = "Generation"
    ):
        self.provider_name = provider_name
        self.model_name = model_name
        self.log_context = log_context

    async def __call__(self, context: LLMContext, next_call: NextCall) -> LLMResponse:
        attempt = context.runtime_state.get("attempt", 1)
        api_key = context.runtime_state.get("api_key", "unknown")
        masked_key = f"{api_key[:8]}..."

        logger.info(
            f"🌐 发起LLM请求 (尝试 {attempt}) - {self.provider_name}/{self.model_name} "
            f"[{self.log_context}] Key: {masked_key}"
        )

        try:
            start_time = time.monotonic()
            response = await next_call(context)
            duration = (time.monotonic() - start_time) * 1000
            logger.info(f"🎯 LLM响应成功 [{self.log_context}] 耗时: {duration:.2f}ms")
            return response
        except Exception as e:
            logger.error(f"❌ 请求异常 [{self.log_context}]: {type(e).__name__} - {e}")
            raise e


class NetworkRequestMiddleware(BaseLLMMiddleware):
    """
    网络请求中间件：执行 Adapter 转换和 HTTP 请求
    """

    def __init__(self, model_instance: "LLMModel", adapter: "BaseAdapter"):
        self.model = model_instance
        self.http_client = model_instance.http_client
        self.adapter = adapter
        self.key_store = model_instance.key_store

    async def __call__(self, context: LLMContext, next_call: NextCall) -> LLMResponse:
        api_key = context.runtime_state["api_key"]

        request_data: RequestData
        gen_config: LLMGenerationConfig | None = None
        embed_config: LLMEmbeddingConfig | None = None

        if context.request_type == "embedding":
            embed_config = cast(LLMEmbeddingConfig, context.config)
            texts = (context.extra or {}).get("texts", [])
            request_data = self.adapter.prepare_embedding_request(
                model=self.model,
                api_key=api_key,
                texts=texts,
                config=embed_config,
            )
        else:
            gen_config = cast(LLMGenerationConfig, context.config)
            request_data = await self.adapter.prepare_advanced_request(
                model=self.model,
                api_key=api_key,
                messages=context.messages,
                config=gen_config,
                tools=context.tools,
                tool_choice=context.tool_choice,
            )

        masked_key = (
            f"{api_key[:8]}...{api_key[-4:] if len(api_key) > 12 else '***'}"
            if api_key
            else "N/A"
        )
        logger.debug(f"🔑 API密钥: {masked_key}")
        logger.debug(f"📡 请求URL: {request_data.url}")
        logger.debug(f"📋 请求头: {_sanitize_request_headers(request_data.headers)}")

        if self.model.api_type == "smart":
            effective_type = self.model._get_effective_api_type()
            sanitizer_req_context = f"{effective_type}_request"
        else:
            sanitizer_req_context = self.adapter.log_sanitization_context
        sanitized_body = sanitize_for_logging(
            request_data.body, context=sanitizer_req_context
        )

        if request_data.files and isinstance(sanitized_body, dict):
            file_info: list[str] = []
            file_count = 0
            if isinstance(request_data.files, list):
                file_count = len(request_data.files)
                for key, value in request_data.files:
                    filename = (
                        value[0]
                        if isinstance(value, tuple) and len(value) > 0
                        else "..."
                    )
                    file_info.append(f"{key}='{filename}'")
            elif isinstance(request_data.files, dict):
                file_count = len(request_data.files)
                file_info = list(request_data.files.keys())

            sanitized_body["[MULTIPART_FILES]"] = f"Count: {file_count} | {file_info}"

        request_body_str = dump_json_safely(
            sanitized_body, ensure_ascii=False, indent=2
        )
        logger.debug(f"📦 请求体: {request_body_str}")

        start_time = time.monotonic()
        try:
            http_response = await self.http_client.post(
                request_data.url,
                headers=request_data.headers,
                content=dump_json_safely(request_data.body, ensure_ascii=False)
                if not request_data.files
                else None,
                data=request_data.body if request_data.files else None,
                files=request_data.files,
                timeout=context.timeout,
            )

            logger.debug(f"📥 响应状态码: {http_response.status_code}")

            if exception := self.adapter.handle_http_error(http_response):
                error_text = http_response.content.decode("utf-8", errors="ignore")
                logger.debug(f"💥 完整错误响应: {error_text}")
                await self.key_store.record_failure(
                    api_key, http_response.status_code, error_text
                )
                raise exception

            response_bytes = await http_response.aread()
            logger.debug(f"📦 响应体已完整读取 ({len(response_bytes)} bytes)")

            response_json = json.loads(response_bytes)

            sanitizer_resp_context = sanitizer_req_context.replace(
                "_request", "_response"
            )
            if sanitizer_resp_context == sanitizer_req_context:
                sanitizer_resp_context = f"{sanitizer_req_context}_response"

            sanitized_response = sanitize_for_logging(
                response_json, context=sanitizer_resp_context
            )
            response_json_str = json.dumps(
                sanitized_response, ensure_ascii=False, indent=2
            )
            logger.debug(f"📋 响应JSON: {response_json_str}")

            if context.request_type == "embedding":
                self.adapter.validate_embedding_response(response_json)
                embeddings = self.adapter.parse_embedding_response(response_json)
                latency = (time.monotonic() - start_time) * 1000
                await self.key_store.record_success(api_key, latency)

                return LLMResponse(
                    text="",
                    raw_response=response_json,
                    cache_info={"embeddings": embeddings},
                )

            response_data = self.adapter.parse_response(
                self.model, response_json, is_advanced=True
            )

            should_rescue_image = (
                gen_config
                and gen_config.validation_policy
                and gen_config.validation_policy.get("require_image")
            )
            if (
                should_rescue_image
                and not response_data.images
                and response_data.text
                and gen_config
            ):
                markdown_matches = re.findall(
                    r"(!?\[.*?\]\((https?://[^\)]+)\))", response_data.text
                )
                if markdown_matches:
                    logger.info(
                        f"检测到 {len(markdown_matches)} "
                        "个资源链接，尝试自动下载并清洗。"
                    )
                    if response_data.images is None:
                        response_data.images = []

                    downloaded_urls = set()
                    for full_tag, url in markdown_matches:
                        try:
                            if url not in downloaded_urls:
                                content = await AsyncHttpx.get_content(url)
                                response_data.images.append(process_image_data(content))
                                downloaded_urls.add(url)
                            response_data.text = response_data.text.replace(
                                full_tag, ""
                            )
                        except Exception as exc:
                            logger.warning(
                                f"自动下载生成的图片失败: {url}, 错误: {exc}"
                            )
                    response_data.text = response_data.text.strip()

            latency = (time.monotonic() - start_time) * 1000
            await self.key_store.record_success(api_key, latency)

            response_tool_calls: list[LLMToolCall] = []
            if response_data.tool_calls:
                for tc_data in response_data.tool_calls:
                    if isinstance(tc_data, LLMToolCall):
                        response_tool_calls.append(tc_data)
                    elif isinstance(tc_data, dict):
                        try:
                            response_tool_calls.append(LLMToolCall(**tc_data))
                        except Exception:
                            pass

            final_response = LLMResponse(
                text=response_data.text,
                content_parts=response_data.content_parts,
                usage_info=response_data.usage_info,
                images=response_data.images,
                raw_response=response_data.raw_response,
                tool_calls=response_tool_calls if response_tool_calls else None,
                code_executions=response_data.code_executions,
                grounding_metadata=response_data.grounding_metadata,
                cache_info=response_data.cache_info,
                thought_text=response_data.thought_text,
                thought_signature=response_data.thought_signature,
            )

            if context.request_type == "generation" and gen_config:
                if gen_config.response_validator:
                    try:
                        gen_config.response_validator(final_response)
                    except Exception as exc:
                        raise LLMException(
                            f"响应内容未通过自定义验证器: {exc}",
                            code=LLMErrorCode.API_RESPONSE_INVALID,
                            details={"validator_error": str(exc)},
                            cause=exc,
                        ) from exc

                policy = gen_config.validation_policy
                if policy:
                    effective_type = self.model._get_effective_api_type()
                    if policy.get("require_image") and not final_response.images:
                        if effective_type == "gemini" and response_data.raw_response:
                            usage_metadata = response_data.raw_response.get(
                                "usageMetadata", {}
                            )
                            prompt_token_details = usage_metadata.get(
                                "promptTokensDetails", []
                            )
                            prompt_had_image = any(
                                detail.get("modality") == "IMAGE"
                                for detail in prompt_token_details
                            )

                            if prompt_had_image:
                                raise LLMException(
                                    "响应验证失败：模型接收了图片输入但未生成图片。",
                                    code=LLMErrorCode.API_RESPONSE_INVALID,
                                    details={
                                        "policy": policy,
                                        "text_response": final_response.text,
                                        "raw_response": response_data.raw_response,
                                    },
                                )
                            else:
                                logger.debug(
                                    "Gemini提示词中未包含图片，跳过图片要求重试。"
                                )
                        else:
                            raise LLMException(
                                "响应验证失败：要求返回图片但未找到图片数据。",
                                code=LLMErrorCode.API_RESPONSE_INVALID,
                                details={
                                    "policy": policy,
                                    "text_response": final_response.text,
                                },
                            )

            return final_response

        except Exception as e:
            if isinstance(e, LLMException):
                raise e

            logger.error(f"解析响应失败或发生未知错误: {e}")

            if not isinstance(e, httpx.NetworkError | httpx.TimeoutException):
                await self.key_store.record_failure(api_key, None, str(e))

            raise LLMException(
                f"网络请求异常: {type(e).__name__} - {e}",
                code=LLMErrorCode.API_REQUEST_FAILED,
                details={"api_key": masked_key},
                cause=e,
            )
