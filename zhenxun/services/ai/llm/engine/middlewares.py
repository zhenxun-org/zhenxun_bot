import asyncio
import hashlib
import json
import re
import time
from typing import Any, ClassVar, cast

from aiocache import SimpleMemoryCache
import httpx

from zhenxun.services.ai.core.exceptions import (
    ConfigurationException,
    LLMException,
    NetworkTimeoutException,
    UpstreamServerException,
)
from zhenxun.services.ai.core.messages import (
    AudioPart,
    AudioResponse,
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    FilePart,
    ImagePart,
    ImageRequest,
    ImageResponse,
    RerankRequest,
    RerankResponse,
    SpeechRequest,
    TextPart,
    VideoPart,
)
from zhenxun.services.ai.core.models import (
    LLMContext,
    ModelCapabilities,
    ModelIdentity,
    ModelModality,
)
from zhenxun.services.ai.core.options import (
    GenerationConfig,
)
from zhenxun.services.ai.core.protocols.middleware import LLMMiddleware, NextCall
from zhenxun.services.ai.llm.adapters.base import (
    BaseAdapter,
    RequestData,
    process_image_data,
)
from zhenxun.services.ai.llm.system.models import RetryConfig
from zhenxun.services.ai.llm.system.network import (
    HealthManager,
    LLMHttpClient,
)
from zhenxun.services.ai.utils.logger import log_llm as logger
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.log_sanitizer import sanitize_for_logging
from zhenxun.utils.pydantic_compat import (
    dump_json_safely,
    model_copy,
    model_dump,
    parse_as,
)

_LLM_API_CACHE = SimpleMemoryCache(namespace="zhenxun_llm_api_cache")


class MiddlewarePipeline:
    """中间件管线组装器"""

    def __init__(self):
        self.middlewares: list[LLMMiddleware] = []

    def add_middleware(self, middleware: LLMMiddleware) -> None:
        """按顺序追加中间件，先加入的将处在调用链的最外层"""
        self.middlewares.append(middleware)

    def build(self, terminal_handler: NextCall[Any, Any]) -> NextCall[Any, Any]:
        handler = terminal_handler
        for middleware in reversed(self.middlewares):

            def _wrap(
                mw: LLMMiddleware[Any, Any], next_c: NextCall[Any, Any]
            ) -> NextCall[Any, Any]:
                async def _handler(context: LLMContext[Any, Any]) -> Any:
                    return await mw(context, next_c)

                return _handler

            handler = _wrap(middleware, handler)
        return handler


class LLMCacheMiddleware:
    """
    大模型极速缓存中间件：
    只在开发者显式配置了 __cache_ttl__ 时生效。
    拦截高成本的 API 网络请求，直接返回本地缓存。
    """

    _RESPONSE_TYPE_MAP: ClassVar[dict[type, type]] = {
        ChatRequest: ChatResponse,
        EmbeddingRequest: EmbeddingResponse,
        ImageRequest: ImageResponse,
        SpeechRequest: AudioResponse,
        RerankRequest: RerankResponse,
    }

    def __init__(self, model_name: str):
        self.model_name = model_name

    def _generate_cache_key(self, context: LLMContext[Any, Any]) -> str:
        """构造绝对纯净的请求哈希键，剔除时间戳等干扰项"""
        payload = {
            "model": self.model_name,
            "type": type(context.request).__name__,
            "request": context.request.get_cache_hash_payload(),
        }

        json_str = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.md5(json_str.encode("utf-8")).hexdigest()

    async def __call__(
        self, context: LLMContext[Any, Any], next_call: NextCall[Any, Any]
    ) -> Any:
        ttl = None
        if hasattr(context.request, "config") and context.request.config:
            ttl = getattr(context.request.config, "custom_kwargs", {}).get(
                "__cache_ttl__"
            )

        if ttl is None:
            return await next_call(context)

        cache_key = self._generate_cache_key(context)
        cached_data = await _LLM_API_CACHE.get(cache_key)

        if cached_data is not None:
            logger.debug(
                f"命中本地极速缓存 - "
                f"model: {self.model_name}, type: {type(context.request).__name__}"
            )

            response_type = self._RESPONSE_TYPE_MAP.get(type(context.request))
            if response_type:
                cached_resp = parse_as(response_type, cached_data)
            else:
                cached_resp = cached_data

            if isinstance(cached_resp, ChatResponse):
                cached_resp.usage_info = {
                    "is_cache_hit": True,
                    "total_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "promptTokenCount": 0,
                    "candidatesTokenCount": 0,
                    "totalTokenCount": 0,
                }

            return cached_resp

        response = await next_call(context)

        await _LLM_API_CACHE.set(cache_key, model_dump(response), ttl=ttl)

        return response


class FailoverAndRetryMiddleware:
    """
    故障转移与重试中间件：
    结合了密钥轮询 (Key Selection) 与异常退避重试 (Retry) 逻辑。
    """

    def __init__(
        self,
        retry_config: RetryConfig,
        health_manager: HealthManager,
        provider_name: str,
        api_keys: list[str],
    ):
        self.retry_config = retry_config
        self.health_manager = health_manager
        self.provider_name = provider_name
        self.api_keys = api_keys
        self._failed_keys: set[str] = set()

    def _raise_with_masked_key(self, e: LLMException, api_key: str) -> None:
        """辅助函数：掩码 API Key 并原样抛出异常，防止密钥泄露"""
        masked = f"{api_key[:8]}..." if api_key else "unknown"
        if isinstance(e.details, dict):
            e.details["api_key"] = masked
        raise e.with_traceback(None) from None

    async def __call__(
        self, context: LLMContext[Any, Any], next_call: NextCall[Any, Any]
    ) -> Any:
        last_exception: Exception | None = None
        is_routed = context.request.extra.get("_is_routed_call", False)
        max_retries = 0 if is_routed else self.retry_config.max_retries
        total_attempts = max_retries + 1

        for attempt in range(total_attempts):
            selected_key = await self.health_manager.get_next_available_key(
                self.provider_name,
                self.api_keys,
                exclude_keys=self._failed_keys,
                strict_mode=is_routed,
            )

            if not selected_key:
                raise ConfigurationException(
                    f"提供商 {self.provider_name} 无可用 API Key"
                )

            context.runtime_state["api_key"] = selected_key
            context.runtime_state["provider_name"] = self.provider_name
            try:
                context.runtime_state["attempt"] = attempt + 1
                return await next_call(context)

            except LLMException as e:
                last_exception = e

                await self.health_manager.record_key_failure(
                    self.provider_name, selected_key, e
                )

                if e.should_rotate_key:
                    self._failed_keys.add(selected_key)

                if not e.is_retryable:
                    self._raise_with_masked_key(e, selected_key)

                if attempt == total_attempts - 1:
                    self._raise_with_masked_key(e, selected_key)

                wait_time = self.retry_config.retry_delay
                if self.retry_config.exponential_backoff:
                    wait_time *= 2**attempt

                logger.warning(
                    f"请求失败，{wait_time:.2f}秒后重试"
                    f" (第{attempt + 1}/{max_retries}次重试): {e}"
                )
                await asyncio.sleep(wait_time)

            except Exception as e:
                logger.error(f"非预期异常，停止重试: {e}", e=e)
                raise e.with_traceback(None) from None

        if last_exception:
            raise last_exception.with_traceback(None) from None
        raise LLMException("重试循环异常结束").with_traceback(None) from None


class LoggingMiddleware:
    """
    日志中间件：
    职责归位后，统一负责 HTTP Payload 的生成、安全脱敏以及完整生命周期的日志记录。
    """

    def __init__(
        self,
        provider_name: str,
        model_name: str,
        adapter: BaseAdapter,
        identity: ModelIdentity,
        log_context: str = "Generation",
    ):
        self.provider_name = provider_name
        self.model_name = model_name
        self.adapter = adapter
        self.identity = identity
        self.log_context = log_context

    async def __call__(
        self, context: LLMContext[Any, Any], next_call: NextCall[Any, Any]
    ) -> Any:
        attempt = context.runtime_state.get("attempt", 1)
        api_key = context.runtime_state.get("api_key", "unknown")
        masked_key = f"{api_key[:8]}..."

        logger.info(
            f"🌐 发起LLM请求 (尝试 {attempt}) - {self.provider_name}/{self.model_name} "
            f"[{self.log_context}] Key: {masked_key}"
        )

        request_data = await self.adapter.prepare_payload(
            identity=self.identity,
            api_key=api_key,
            request=context.request,
        )
        context.runtime_state["request_data"] = request_data

        logger.debug(f"📡 请求URL: {request_data.url}")
        logger.debug(f"📋 请求头: {dict(request_data.headers)}")

        if self.identity.api_type == "smart":
            from zhenxun.services.ai.llm.adapters.factory import SmartAdapter

            smart_adapter = cast(SmartAdapter, self.adapter)
            delegate_adapter = smart_adapter._get_delegate_adapter(self.identity)
            sanitizer_req_context = f"{delegate_adapter.api_type}_request"
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

        try:
            start_time = time.monotonic()
            response = await next_call(context)
            duration = (time.monotonic() - start_time) * 1000
            logger.debug(f"🎯 LLM响应成功 [{self.log_context}] 耗时: {duration:.2f}ms")
            return response
        except Exception as e:
            raise e.with_traceback(None) from None


class HttpExecutionMiddleware:
    """
    终端 HTTP 执行中间件：
    只负责将上游构建好的 Payload 发送出去，并拦截纯粹的 HTTP 网络故障。
    """

    def __init__(
        self,
        http_client: LLMHttpClient,
        identity: ModelIdentity,
        health_manager: HealthManager,
        adapter: BaseAdapter,
    ):
        self.http_client = http_client
        self.identity = identity
        self.health_manager = health_manager
        self.adapter = adapter

    async def __call__(
        self, context: LLMContext[Any, Any], next_call: NextCall[Any, Any]
    ) -> Any:
        api_key = context.runtime_state["api_key"]
        provider_name = self.identity.provider_name
        route_id = f"{self.identity.provider_name}/{self.identity.model_name}"

        request_data: RequestData = context.runtime_state["request_data"]

        if context.cancellation_token:
            context.cancellation_token.raise_if_cancelled()

        start_time = time.monotonic()
        try:
            method = getattr(request_data, "method", "POST").upper()
            req_kwargs = {
                "headers": request_data.headers,
                "timeout": context.request.timeout,
            }

            if method in ("POST", "PUT", "PATCH"):
                if request_data.files:
                    req_kwargs["data"] = request_data.body
                    req_kwargs["files"] = request_data.files
                else:
                    req_kwargs["content"] = json.dumps(
                        request_data.body, ensure_ascii=False
                    )
            elif method == "GET" and request_data.body:
                req_kwargs["params"] = request_data.body

            post_task = asyncio.create_task(
                self.http_client.request(method, request_data.url, **req_kwargs)
            )

            if context.cancellation_token:
                context.cancellation_token.link_future(post_task)

            raw_engine_output = await post_task

            logger.debug(f"📥 HTTP响应状态码: {raw_engine_output.status_code}")
            if exception := self.adapter.handle_http_error(raw_engine_output):
                error_text = raw_engine_output.content.decode("utf-8", errors="ignore")
                logger.debug(f"💥 完整错误响应: {error_text}")
                raise exception.with_traceback(None) from None

            latency = (time.monotonic() - start_time) * 1000
            await self.health_manager.record_key_success(provider_name, api_key)
            await self.health_manager.record_route_success(route_id, latency)

            return await self.adapter.parse_payload(
                identity=self.identity,
                request=context.request,
                raw_response=raw_engine_output,
            )

        except asyncio.CancelledError:
            logger.warning(f"网络请求已被取消: {request_data.url}")
            raise
        except httpx.TimeoutException as e:
            await self.health_manager.record_route_failure(route_id, e)
            raise NetworkTimeoutException(f"HTTP请求超时: {e}", cause=e)
        except httpx.NetworkError as e:
            await self.health_manager.record_route_failure(route_id, e)
            raise UpstreamServerException(f"网络连接中断: {e}", cause=e)
        except LLMException as e:
            if e.should_failover:
                await self.health_manager.record_route_failure(route_id, e)
            raise e.with_traceback(None) from None
        except Exception as e:
            logger.error(f"解析响应失败或发生未知错误: {e}")
            masked_key = (
                f"{api_key[:8]}...{api_key[-4:] if len(api_key) > 12 else '***'}"
                if api_key
                else "N/A"
            )
            raise UpstreamServerException(
                f"网络请求异常: {type(e).__name__} - {e}",
                details={"api_key": masked_key},
                cause=e,
            ).with_traceback(None) from None


class ModalityFilterMiddleware:
    """模态过滤中间件：负责自动剔除当前模型不支持的多模态输入"""

    def __init__(self, model_name: str, capabilities: ModelCapabilities):
        self.model_name = model_name
        self.capabilities = capabilities

    async def __call__(
        self, context: LLMContext[Any, Any], next_call: NextCall[Any, Any]
    ) -> Any:
        request = context.request
        if isinstance(request, ChatRequest):
            filtered_messages = []
            _warned_image, _warned_audio, _warned_video = False, False, False
            for msg in request.messages:
                new_content = []
                for part in msg.content:
                    if (
                        isinstance(part, ImagePart)
                        and ModelModality.IMAGE
                        not in self.capabilities.input_modalities
                    ):
                        if not _warned_image:
                            logger.warning(
                                f"模型 {self.model_name} 不支持图像输入，"
                                "已自动将图片替换为占位符"
                            )
                            _warned_image = True
                        new_content.append(TextPart(text="<图片>"))
                    elif (
                        isinstance(part, AudioPart)
                        and ModelModality.AUDIO
                        not in self.capabilities.input_modalities
                    ):
                        if not _warned_audio:
                            logger.warning(
                                f"模型 {self.model_name} 不支持音频输入，"
                                "已自动将音频替换为占位符"
                            )
                            _warned_audio = True
                        new_content.append(TextPart(text="<音频>"))
                    elif (
                        isinstance(part, VideoPart)
                        and ModelModality.VIDEO
                        not in self.capabilities.input_modalities
                    ):
                        if not _warned_video:
                            logger.warning(
                                f"模型 {self.model_name} 不支持视频输入，"
                                "已自动将视频替换为占位符"
                            )
                            _warned_video = True
                        new_content.append(TextPart(text="<视频>"))
                    elif (
                        isinstance(part, FilePart)
                        and ModelModality.FILE not in self.capabilities.input_modalities
                    ):
                        new_content.append(TextPart(text="<文件>"))
                    else:
                        new_content.append(part)

                filtered_messages.append(
                    model_copy(msg, update={"content": new_content})
                )
            context.request = model_copy(
                request, update={"messages": filtered_messages}
            )
        return await next_call(context)


class ConfigMergeMiddleware:
    """配置合并中间件：合并覆盖配置，统一填充默认参数"""

    def __init__(self, generation_config: GenerationConfig | None):
        self.generation_config = generation_config

    async def __call__(
        self, context: LLMContext[Any, Any], next_call: NextCall[Any, Any]
    ) -> Any:
        request = context.request
        updates = {}

        if hasattr(request, "tools") and getattr(request, "tools", None) is not None:
            tools = getattr(request, "tools")
            updates["tools"] = (
                list(tools.values())
                if isinstance(tools, dict)
                else (tools if isinstance(tools, list) else [tools])
            )

        if hasattr(request, "config"):
            req_config = getattr(request, "config", None)
            if isinstance(req_config, GenerationConfig) and self.generation_config:
                updates["config"] = self.generation_config.merge_with(req_config)
            elif (
                req_config is None
                and self.generation_config
                and hasattr(request, "messages")
            ):
                updates["config"] = self.generation_config

        if updates:
            context.request = model_copy(request, update=updates)

        return await next_call(context)


class ResponseRescueMiddleware:
    """响应挽救中间件：对于没有按要求返回图片链接的模型，尝试进行正则兜底下载"""

    async def __call__(
        self, context: LLMContext[Any, Any], next_call: NextCall[Any, Any]
    ) -> Any:
        response = await next_call(context)
        request = context.request

        if isinstance(request, ChatRequest) and isinstance(response, ChatResponse):
            gen_config = request.config
            policy = gen_config.validation_policy if gen_config else None
            should_rescue_image = policy and policy.get("require_image")
            if (
                should_rescue_image
                and not response.images
                and response.text
                and gen_config
            ):
                markdown_matches = re.findall(
                    r"(!?\[.*?\]\((https?://[^\)]+)\))", response.text
                )
                if markdown_matches:
                    logger.info(
                        f"检测到 {len(markdown_matches)} 个链接，尝试自动下载清洗。"
                    )
                    current_text = response.text
                    other_parts = [
                        p for p in response.content_parts if not isinstance(p, TextPart)
                    ]
                    downloaded_urls = set()
                    for full_tag, url in markdown_matches:
                        try:
                            if url not in downloaded_urls:
                                content = await AsyncHttpx.get_content(url)
                                processed = process_image_data(content)
                                if isinstance(processed, bytes):
                                    img_part = ImagePart(raw=processed)
                                else:
                                    img_part = ImagePart(path=processed)
                                other_parts.append(img_part)
                                downloaded_urls.add(url)
                            current_text = current_text.replace(full_tag, "")
                        except Exception as exc:
                            logger.warning(f"自动下载图片失败: {url}, 错误: {exc}")
                    response.content_parts = [
                        TextPart(text=current_text.strip()),
                        *other_parts,
                    ]
        return response


class OutputValidationMiddleware:
    """输出验证中间件：负责策略校验与自定义格式验证）"""

    async def __call__(
        self, context: LLMContext[Any, Any], next_call: NextCall[Any, Any]
    ) -> Any:
        response = await next_call(context)
        request = context.request

        if isinstance(request, ChatRequest) and isinstance(response, ChatResponse):
            gen_config = request.config
            if not gen_config:
                return response

            if gen_config.response_validator:
                try:
                    gen_config.response_validator(response)
                except Exception as exc:
                    raise LLMException(
                        f"响应内容未通过自定义验证器: {exc}",
                        details={"validator_error": str(exc)},
                    ).with_traceback(None) from None

            policy = gen_config.validation_policy
            if policy and policy.get("require_image") and not response.images:
                prompt_had_image = any(
                    isinstance(p, ImagePart)
                    for msg in request.messages
                    for p in msg.content
                )
                if not prompt_had_image:
                    logger.debug("提示词中未包含图片，跳过要求图片返回的重试特判。")
                else:
                    raise LLMException(
                        "响应验证失败：要求返回图片但未找到图片数据。",
                        details={"policy": policy, "text_response": response.text},
                    )
        return response
