"""
LLM 核心基础设施模块

包含执行 LLM 请求所需的底层组件，如 HTTP 客户端、API Key 存储和智能重试逻辑。
"""

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel

from zhenxun.services.log import logger
from zhenxun.utils.user_agent import get_user_agent

from .types import ProviderConfig
from .types.exceptions import LLMErrorCode, LLMException


class HttpClientConfig(BaseModel):
    """HTTP客户端配置"""

    timeout: int = 180
    max_connections: int = 100
    max_keepalive_connections: int = 20
    proxy: str | None = None


class LLMHttpClient:
    """LLM服务专用HTTP客户端"""

    def __init__(self, config: HttpClientConfig | None = None):
        self.config = config or HttpClientConfig()
        self._client: httpx.AsyncClient | None = None
        self._active_requests = 0
        self._lock = asyncio.Lock()

    async def _ensure_client_initialized(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            async with self._lock:
                if self._client is None or self._client.is_closed:
                    logger.debug(
                        f"LLMHttpClient: Initializing new httpx.AsyncClient "
                        f"with config: {self.config}"
                    )
                    headers = get_user_agent()
                    limits = httpx.Limits(
                        max_connections=self.config.max_connections,
                        max_keepalive_connections=self.config.max_keepalive_connections,
                    )
                    timeout = httpx.Timeout(self.config.timeout)

                    client_kwargs = {}
                    if self.config.proxy:
                        try:
                            version_parts = httpx.__version__.split(".")
                            major = int(
                                "".join(c for c in version_parts[0] if c.isdigit())
                            )
                            minor = (
                                int("".join(c for c in version_parts[1] if c.isdigit()))
                                if len(version_parts) > 1
                                else 0
                            )
                            if (major, minor) >= (0, 28):
                                client_kwargs["proxy"] = self.config.proxy
                            else:
                                client_kwargs["proxies"] = self.config.proxy
                        except (ValueError, IndexError):
                            client_kwargs["proxies"] = self.config.proxy
                            logger.warning(
                                f"无法解析 httpx 版本 '{httpx.__version__}'，"
                                "LLM模块将默认使用旧版 'proxies' 参数语法。"
                            )

                    self._client = httpx.AsyncClient(
                        headers=headers,
                        limits=limits,
                        timeout=timeout,
                        follow_redirects=True,
                        **client_kwargs,
                    )
        if self._client is None:
            raise LLMException(
                "HTTP client failed to initialize.", LLMErrorCode.CONFIGURATION_ERROR
            )
        return self._client

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        client = await self._ensure_client_initialized()
        async with self._lock:
            self._active_requests += 1
        try:
            return await client.post(url, **kwargs)
        finally:
            async with self._lock:
                self._active_requests -= 1

    async def close(self):
        async with self._lock:
            if self._client and not self._client.is_closed:
                logger.debug(
                    f"LLMHttpClient: Closing with config: {self.config}. "
                    f"Active requests: {self._active_requests}"
                )
                if self._active_requests > 0:
                    logger.warning(
                        f"LLMHttpClient: Closing while {self._active_requests} "
                        f"requests are still active."
                    )
                await self._client.aclose()
            self._client = None
        logger.debug(f"LLMHttpClient for config {self.config} definitively closed.")

    @property
    def is_closed(self) -> bool:
        return self._client is None or self._client.is_closed


class LLMHttpClientManager:
    """管理 LLMHttpClient 实例的工厂和池"""

    def __init__(self):
        self._clients: dict[tuple[int, str | None], LLMHttpClient] = {}
        self._lock = asyncio.Lock()

    def _get_client_key(
        self, provider_config: ProviderConfig
    ) -> tuple[int, str | None]:
        return (provider_config.timeout, provider_config.proxy)

    async def get_client(self, provider_config: ProviderConfig) -> LLMHttpClient:
        key = self._get_client_key(provider_config)
        async with self._lock:
            client = self._clients.get(key)
            if client and not client.is_closed:
                logger.debug(
                    f"LLMHttpClientManager: Reusing existing LLMHttpClient "
                    f"for key: {key}"
                )
                return client

            if client and client.is_closed:
                logger.debug(
                    f"LLMHttpClientManager: Found a closed client for key {key}. "
                    f"Creating a new one."
                )

            logger.debug(
                f"LLMHttpClientManager: Creating new LLMHttpClient for key: {key}"
            )
            http_client_config = HttpClientConfig(
                timeout=provider_config.timeout, proxy=provider_config.proxy
            )
            new_client = LLMHttpClient(config=http_client_config)
            self._clients[key] = new_client
            return new_client

    async def shutdown(self):
        async with self._lock:
            logger.info(
                f"LLMHttpClientManager: Shutting down. "
                f"Closing {len(self._clients)} client(s)."
            )
            close_tasks = [
                client.close()
                for client in self._clients.values()
                if client and not client.is_closed
            ]
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)
            self._clients.clear()
        logger.info("LLMHttpClientManager: Shutdown complete.")


http_client_manager = LLMHttpClientManager()


async def create_llm_http_client(
    timeout: int = 180,
    proxy: str | None = None,
) -> LLMHttpClient:
    """
    创建LLM HTTP客户端

    参数:
        timeout: 超时时间（秒）。
        proxy: 代理服务器地址。

    返回:
        LLMHttpClient: HTTP客户端实例。
    """
    config = HttpClientConfig(timeout=timeout, proxy=proxy)
    return LLMHttpClient(config)


class RetryConfig:
    """重试配置"""

    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        exponential_backoff: bool = True,
        key_rotation: bool = True,
    ):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.exponential_backoff = exponential_backoff
        self.key_rotation = key_rotation


async def with_smart_retry(
    func,
    *args,
    retry_config: RetryConfig | None = None,
    key_store: "KeyStatusStore | None" = None,
    provider_name: str | None = None,
    **kwargs: Any,
) -> Any:
    """
    智能重试装饰器 - 支持Key轮询和错误分类

    参数:
        func: 要重试的异步函数。
        *args: 传递给函数的位置参数。
        retry_config: 重试配置。
        key_store: API密钥状态存储。
        provider_name: 提供商名称。
        **kwargs: 传递给函数的关键字参数。

    返回:
        Any: 函数执行结果。
    """
    config = retry_config or RetryConfig()
    last_exception: Exception | None = None
    failed_keys: set[str] = set()

    for attempt in range(config.max_retries + 1):
        try:
            if config.key_rotation and "failed_keys" in func.__code__.co_varnames:
                kwargs["failed_keys"] = failed_keys

            return await func(*args, **kwargs)

        except LLMException as e:
            last_exception = e

            if e.code in [
                LLMErrorCode.API_KEY_INVALID,
                LLMErrorCode.API_QUOTA_EXCEEDED,
            ]:
                if hasattr(e, "details") and e.details and "api_key" in e.details:
                    failed_keys.add(e.details["api_key"])
                    if key_store and provider_name:
                        await key_store.record_failure(
                            e.details["api_key"], e.details.get("status_code")
                        )

            should_retry = _should_retry_llm_error(e, attempt, config.max_retries)
            if not should_retry:
                logger.error(f"不可重试的错误，停止重试: {e}")
                raise

            if attempt < config.max_retries:
                wait_time = config.retry_delay
                if config.exponential_backoff:
                    wait_time *= 2**attempt
                logger.warning(
                    f"请求失败，{wait_time}秒后重试 (第{attempt + 1}次): {e}"
                )
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"重试{config.max_retries}次后仍然失败: {e}")

        except Exception as e:
            last_exception = e
            logger.error(f"非LLM异常，停止重试: {e}")
            raise LLMException(
                f"操作失败: {e}",
                code=LLMErrorCode.GENERATION_FAILED,
                cause=e,
            )

    if last_exception:
        raise last_exception
    else:
        raise RuntimeError("重试函数未能正常执行且未捕获到异常")


def _should_retry_llm_error(
    error: LLMException, attempt: int, max_retries: int
) -> bool:
    """判断LLM错误是否应该重试"""
    non_retryable_errors = {
        LLMErrorCode.MODEL_NOT_FOUND,
        LLMErrorCode.CONTEXT_LENGTH_EXCEEDED,
        LLMErrorCode.USER_LOCATION_NOT_SUPPORTED,
        LLMErrorCode.CONFIGURATION_ERROR,
    }

    if error.code in non_retryable_errors:
        return False

    retryable_errors = {
        LLMErrorCode.API_REQUEST_FAILED,
        LLMErrorCode.API_TIMEOUT,
        LLMErrorCode.API_RATE_LIMITED,
        LLMErrorCode.API_RESPONSE_INVALID,
        LLMErrorCode.RESPONSE_PARSE_ERROR,
        LLMErrorCode.GENERATION_FAILED,
        LLMErrorCode.CONTENT_FILTERED,
        LLMErrorCode.API_KEY_INVALID,
        LLMErrorCode.API_QUOTA_EXCEEDED,
    }

    if error.code in retryable_errors:
        if error.code == LLMErrorCode.API_QUOTA_EXCEEDED:
            return attempt < min(2, max_retries)
        elif error.code == LLMErrorCode.CONTENT_FILTERED:
            return attempt < min(1, max_retries)
        return True

    return False


class KeyStatusStore:
    """API Key 状态管理存储 - 优化版本，支持轮询和负载均衡"""

    def __init__(self):
        self._key_status: dict[str, bool] = {}
        self._key_usage_count: dict[str, int] = {}
        self._key_last_used: dict[str, float] = {}
        self._provider_key_index: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def get_next_available_key(
        self,
        provider_name: str,
        api_keys: list[str],
        exclude_keys: set[str] | None = None,
    ) -> str | None:
        """
        获取下一个可用的API密钥（轮询策略）

        参数:
            provider_name: 提供商名称。
            api_keys: API密钥列表。
            exclude_keys: 要排除的密钥集合。

        返回:
            str | None: 可用的API密钥，如果没有可用密钥则返回None。
        """
        if not api_keys:
            return None

        exclude_keys = exclude_keys or set()
        available_keys = [
            key
            for key in api_keys
            if key not in exclude_keys and self._key_status.get(key, True)
        ]

        if not available_keys:
            return api_keys[0] if api_keys else None

        async with self._lock:
            current_index = self._provider_key_index.get(provider_name, 0)

            selected_key = available_keys[current_index % len(available_keys)]

            self._provider_key_index[provider_name] = (current_index + 1) % len(
                available_keys
            )

            import time

            self._key_usage_count[selected_key] = (
                self._key_usage_count.get(selected_key, 0) + 1
            )
            self._key_last_used[selected_key] = time.time()

            logger.debug(
                f"轮询选择API密钥: {self._get_key_id(selected_key)} "
                f"(使用次数: {self._key_usage_count[selected_key]})"
            )

            return selected_key

    async def record_success(self, api_key: str):
        """记录成功使用"""
        async with self._lock:
            self._key_status[api_key] = True
        logger.debug(f"记录API密钥成功使用: {self._get_key_id(api_key)}")

    async def record_failure(self, api_key: str, status_code: int | None):
        """
        记录失败使用

        参数:
            api_key: API密钥。
            status_code: HTTP状态码。
        """
        key_id = self._get_key_id(api_key)
        async with self._lock:
            if status_code in [401, 403]:
                self._key_status[api_key] = False
                logger.warning(
                    f"API密钥认证失败，标记为不可用: {key_id} (状态码: {status_code})"
                )
            else:
                logger.debug(f"记录API密钥失败使用: {key_id} (状态码: {status_code})")

    async def reset_key_status(self, api_key: str):
        """重置密钥状态（用于恢复机制）"""
        async with self._lock:
            self._key_status[api_key] = True
        logger.info(f"重置API密钥状态: {self._get_key_id(api_key)}")

    async def get_key_stats(self, api_keys: list[str]) -> dict[str, dict]:
        """
        获取密钥使用统计

        参数:
            api_keys: API密钥列表。

        返回:
            dict[str, dict]: 密钥统计信息字典。
        """
        stats = {}
        async with self._lock:
            for key in api_keys:
                key_id = self._get_key_id(key)
                stats[key_id] = {
                    "available": self._key_status.get(key, True),
                    "usage_count": self._key_usage_count.get(key, 0),
                    "last_used": self._key_last_used.get(key, 0),
                }
        return stats

    def _get_key_id(self, api_key: str) -> str:
        """获取API密钥的标识符（用于日志）"""
        if len(api_key) <= 8:
            return api_key
        return f"{api_key[:4]}...{api_key[-4:]}"


key_store = KeyStatusStore()
