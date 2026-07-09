"""
LLM 模型实现类

包含 LLM 模型的抽象基类和具体实现，负责与各种 AI 提供商的 API 交互。
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from zhenxun.services.ai.config import ProviderConfig, get_llm_config
from zhenxun.services.ai.core.exceptions import ConfigurationException
from zhenxun.services.ai.core.messages import (
    AudioResponse,
    BaseRequest,
    ChatRequest,
    ChatResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    ImageRequest,
    ImageResponse,
    RerankRequest,
    RerankResponse,
    SpeechRequest,
)
from zhenxun.services.ai.core.models import (
    CancellationToken,
    LLMContext,
    ModelCapabilities,
    ModelDetail,
    ModelIdentity,
)
from zhenxun.services.ai.core.options import (
    GenerationConfig,
)
from zhenxun.services.ai.core.protocols.llm import (
    SupportsChat,
    SupportsImageGeneration,
    SupportsReranking,
    SupportsSpeechSynthesis,
    SupportsTextEmbedding,
)
from zhenxun.services.ai.core.protocols.middleware import LLMMiddleware
from zhenxun.services.ai.llm.adapters.factory import get_adapter_for_api_type
from zhenxun.services.ai.llm.system.models import RetryConfig
from zhenxun.services.ai.llm.system.network import HealthManager, LLMHttpClient
from zhenxun.services.ai.utils.logger import log_llm as logger

from .middlewares import (
    ConfigMergeMiddleware,
    FailoverAndRetryMiddleware,
    HttpExecutionMiddleware,
    LLMCacheMiddleware,
    LoggingMiddleware,
    MiddlewarePipeline,
    ModalityFilterMiddleware,
    OutputValidationMiddleware,
    ResponseRescueMiddleware,
)

T = TypeVar("T", bound=BaseModel)


class LLMModel(
    SupportsChat,
    SupportsTextEmbedding,
    SupportsSpeechSynthesis,
    SupportsReranking,
    SupportsImageGeneration,
):
    """LLM 模型实现类"""

    def __init__(
        self,
        provider_config: ProviderConfig,
        model_detail: ModelDetail,
        health_manager: HealthManager,
        http_client: LLMHttpClient,
        capabilities: ModelCapabilities,
        config_override: GenerationConfig | None = None,
    ):
        self.provider_config = provider_config
        self.model_detail = model_detail
        self.health_manager = health_manager
        self.http_client: LLMHttpClient = http_client
        self.capabilities = capabilities
        self._generation_config = config_override

        self.provider_name = provider_config.name
        self.api_type = model_detail.api_type or provider_config.api_type
        self.api_base = provider_config.api_base
        self.path_prefix = model_detail.path_prefix
        self.api_keys = (
            [provider_config.api_key]
            if isinstance(provider_config.api_key, str)
            else provider_config.api_key
        )
        self.model_name = model_detail.model_name
        self.temperature = model_detail.temperature
        self.max_output_tokens = model_detail.max_output_tokens

        self._is_closed = False
        self._ref_count = 0
        self.identity = ModelIdentity(
            provider_name=self.provider_name,
            model_name=self.model_name,
            api_type=self.api_type,
            api_base=self.api_base,
            path_prefix=self.path_prefix,
            capabilities=self.capabilities,
            generation_config=self._generation_config,
        )

        self.pipeline = MiddlewarePipeline()
        self._setup_default_pipeline()

    def add_middleware(self, middleware: LLMMiddleware) -> None:
        """注册一个中间件到处理管道的最外层"""
        self.pipeline.add_middleware(middleware)

    def _setup_default_pipeline(self) -> None:
        client_settings = get_llm_config().client_settings
        retry_config = RetryConfig(
            max_retries=client_settings.max_retries,
            retry_delay=client_settings.retry_delay,
        )
        adapter = get_adapter_for_api_type(self.api_type)

        self.pipeline.add_middleware(LLMCacheMiddleware(self.model_name))
        self.pipeline.add_middleware(ConfigMergeMiddleware(self._generation_config))
        self.pipeline.add_middleware(
            ModalityFilterMiddleware(self.model_name, self.capabilities)
        )
        self.pipeline.add_middleware(
            FailoverAndRetryMiddleware(
                retry_config, self.health_manager, self.provider_name, self.api_keys
            )
        )
        self.pipeline.add_middleware(OutputValidationMiddleware())
        self.pipeline.add_middleware(ResponseRescueMiddleware())
        self.pipeline.add_middleware(
            LoggingMiddleware(
                self.provider_name, self.model_name, adapter, self.identity
            )
        )

    async def _select_api_key(self, failed_keys: set[str] | None = None) -> str:
        """选择可用的API密钥（使用轮询策略）"""
        if not self.api_keys:
            raise ConfigurationException(
                f"提供商 {self.provider_name} 没有配置API密钥",
            )

        selected_key = await self.health_manager.get_next_available_key(
            self.provider_name, self.api_keys, failed_keys
        )

        if not selected_key:
            raise ConfigurationException(
                f"提供商 {self.provider_name} 的所有API密钥当前都不可用",
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

    async def invoke(
        self,
        request: BaseRequest,
        cancellation_token: CancellationToken | None = None,
    ) -> Any:
        """
        大一统命令执行核心入口 (Command Pattern)。
        整合所有中间件执行管线，屏蔽具体模态差异。
        """
        self._check_not_closed()

        context = LLMContext(
            request=request,
            cancellation_token=cancellation_token,
        )
        adapter = get_adapter_for_api_type(self.api_type)
        execution_middleware = HttpExecutionMiddleware(
            http_client=self.http_client,
            identity=self.identity,
            health_manager=self.health_manager,
            adapter=adapter,
        )

        async def terminal_handler(ctx: LLMContext[Any, Any]) -> Any:
            async def _noop(c: LLMContext[Any, Any]) -> Any:
                raise RuntimeError("HttpExecutionMiddleware 不应调用 next_call")

            return await execution_middleware(ctx, _noop)

        handler = self.pipeline.build(terminal_handler)
        return await handler(context)

    async def generate_response(
        self,
        request: ChatRequest,
        cancellation_token: CancellationToken | None = None,
    ) -> ChatResponse:
        return await self.invoke(request, cancellation_token)

    async def generate_embeddings(
        self,
        request: EmbeddingRequest,
    ) -> EmbeddingResponse:
        return await self.invoke(request)

    async def rerank(
        self,
        request: RerankRequest,
    ) -> RerankResponse:
        return await self.invoke(request)

    async def generate_image(
        self,
        request: ImageRequest,
    ) -> ImageResponse:
        return await self.invoke(request)

    async def generate_speech(
        self,
        request: SpeechRequest,
    ) -> AudioResponse:
        return await self.invoke(request)

    def __str__(self) -> str:
        status = "closed" if self._is_closed else "active"
        return f"LLMModel({self.provider_name}/{self.model_name}, {status})"

    def __repr__(self) -> str:
        status = "closed" if self._is_closed else "active"
        return (
            f"LLMModel(provider={self.provider_name}, model={self.model_name}, "
            f"api_type={self.api_type}, status={status})"
        )
