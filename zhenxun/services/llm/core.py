"""
LLM 核心基础设施模块

包含执行 LLM 请求所需的底层组件，如 HTTP 客户端、API Key 存储和智能重试逻辑。
"""

import asyncio
from dataclasses import asdict, dataclass
from enum import IntEnum
import json
import os
import time
from typing import Any

import aiofiles
import httpx
import nonebot
from pydantic import BaseModel

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger
from zhenxun.utils.user_agent import get_user_agent

from .types import ProviderConfig
from .types.exceptions import LLMErrorCode, LLMException

driver = nonebot.get_driver()


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


class KeyStatus(IntEnum):
    """用于排序和展示的密钥状态枚举"""

    DISABLED = 0
    ERROR = 1
    COOLDOWN = 2
    WARNING = 3
    HEALTHY = 4
    UNUSED = 5


@dataclass
class KeyStats:
    """单个API Key的详细状态和统计信息"""

    cooldown_until: float = 0.0
    success_count: int = 0
    failure_count: int = 0
    total_latency: float = 0.0
    last_error_info: str | None = None

    @property
    def is_available(self) -> bool:
        """检查Key当前是否可用"""
        return time.time() >= self.cooldown_until

    @property
    def avg_latency(self) -> float:
        """计算平均延迟"""
        return (
            self.total_latency / self.success_count if self.success_count > 0 else 0.0
        )

    @property
    def success_rate(self) -> float:
        """计算成功率"""
        total = self.success_count + self.failure_count
        return self.success_count / total * 100 if total > 0 else 100.0

    @property
    def status(self) -> KeyStatus:
        """根据当前统计数据动态计算状态"""
        now = time.time()
        cooldown_left = max(0, self.cooldown_until - now)

        if cooldown_left > 31536000 - 60:
            return KeyStatus.DISABLED
        if cooldown_left > 0:
            return KeyStatus.COOLDOWN

        total_calls = self.success_count + self.failure_count
        if total_calls == 0:
            return KeyStatus.UNUSED

        if self.success_rate < 80:
            return KeyStatus.ERROR

        if total_calls >= 5 and self.avg_latency > 15000:
            return KeyStatus.WARNING

        return KeyStatus.HEALTHY

    @property
    def suggested_action(self) -> str:
        """根据状态给出建议操作"""
        status_actions = {
            KeyStatus.DISABLED: "更换Key",
            KeyStatus.ERROR: "检查网络/重置",
            KeyStatus.COOLDOWN: "等待/重置",
            KeyStatus.WARNING: "观察",
            KeyStatus.HEALTHY: "-",
            KeyStatus.UNUSED: "-",
        }
        return status_actions.get(self.status, "未知")


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

    model_instance = next((arg for arg in args if hasattr(arg, "api_keys")), None)
    all_provider_keys = model_instance.api_keys if model_instance else []

    for attempt in range(config.max_retries + 1):
        try:
            if config.key_rotation and "failed_keys" in func.__code__.co_varnames:
                kwargs["failed_keys"] = failed_keys

            start_time = time.monotonic()
            result = await func(*args, **kwargs)
            latency = (time.monotonic() - start_time) * 1000

            if key_store and isinstance(result, tuple) and len(result) == 2:
                final_result, api_key_used = result
                if api_key_used:
                    await key_store.record_success(api_key_used, latency)
                return final_result
            else:
                return result

        except LLMException as e:
            last_exception = e
            api_key_in_use = e.details.get("api_key")

            if api_key_in_use:
                failed_keys.add(api_key_in_use)
                if key_store and provider_name and len(all_provider_keys) > 1:
                    status_code = e.details.get("status_code")
                    error_message = f"({e.code.name}) {e.message}"
                    await key_store.record_failure(
                        api_key_in_use, status_code, error_message
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
                    f"请求失败，{wait_time:.2f}秒后重试 (第{attempt + 1}次): {e}"
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
    """API Key 状态管理存储 - 支持持久化"""

    def __init__(self):
        self._key_stats: dict[str, KeyStats] = {}
        self._provider_key_index: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._file_path = DATA_PATH / "llm" / "key_status.json"

    async def initialize(self):
        """从文件异步加载密钥状态，在应用启动时调用"""
        async with self._lock:
            if not self._file_path.exists():
                logger.info("未找到密钥状态文件，将使用内存状态启动。")
                return

            try:
                logger.info(f"正在从 {self._file_path} 加载密钥状态...")
                async with aiofiles.open(self._file_path, encoding="utf-8") as f:
                    content = await f.read()
                    if not content:
                        logger.warning("密钥状态文件为空。")
                        return
                    data = json.loads(content)

                for key, stats_dict in data.items():
                    self._key_stats[key] = KeyStats(**stats_dict)

                logger.info(f"成功加载 {len(self._key_stats)} 个密钥的状态。")

            except json.JSONDecodeError:
                logger.error(f"密钥状态文件 {self._file_path} 格式错误，无法解析。")
            except Exception as e:
                logger.error(f"加载密钥状态文件时发生错误: {e}", e=e)

    async def _save_to_file_internal(self):
        """
        [内部方法] 将当前密钥状态安全地写入JSON文件。
        假定调用方已持有锁。
        """
        data_to_save = {key: asdict(stats) for key, stats in self._key_stats.items()}

        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._file_path.with_suffix(".json.tmp")

            async with aiofiles.open(temp_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data_to_save, ensure_ascii=False, indent=2))

            if self._file_path.exists():
                self._file_path.unlink()
            os.rename(temp_path, self._file_path)
            logger.debug("密钥状态已成功持久化到文件。")
        except Exception as e:
            logger.error(f"保存密钥状态到文件失败: {e}", e=e)

    async def shutdown(self):
        """在应用关闭时安全地保存状态"""
        async with self._lock:
            await self._save_to_file_internal()
        logger.info("KeyStatusStore 已在关闭前保存状态。")

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

        async with self._lock:
            for key in api_keys:
                if key not in self._key_stats:
                    self._key_stats[key] = KeyStats()

            available_keys = [
                key
                for key in api_keys
                if key not in exclude_keys and self._key_stats[key].is_available
            ]

            if not available_keys:
                return api_keys[0]

            current_index = self._provider_key_index.get(provider_name, 0)
            selected_key = available_keys[current_index % len(available_keys)]
            self._provider_key_index[provider_name] = current_index + 1

            total_usage = (
                self._key_stats[selected_key].success_count
                + self._key_stats[selected_key].failure_count
            )
            logger.debug(
                f"轮询选择API密钥: {self._get_key_id(selected_key)} "
                f"(使用次数: {total_usage})"
            )
            return selected_key

    async def record_success(self, api_key: str, latency: float):
        """记录成功使用，并持久化"""
        async with self._lock:
            stats = self._key_stats.setdefault(api_key, KeyStats())
            stats.cooldown_until = 0.0
            stats.success_count += 1
            stats.total_latency += latency
            stats.last_error_info = None
            await self._save_to_file_internal()
        logger.debug(
            f"记录API密钥成功使用: {self._get_key_id(api_key)}, 延迟: {latency:.2f}ms"
        )

    async def record_failure(
        self, api_key: str, status_code: int | None, error_message: str
    ):
        """
        记录失败使用，并设置冷却时间

        参数:
            api_key: API密钥。
            status_code: HTTP状态码。
            error_message: 错误信息。
        """
        key_id = self._get_key_id(api_key)
        now = time.time()
        cooldown_duration = 300

        if status_code in [401, 403, 404]:
            cooldown_duration = 31536000
            log_level = "error"
            log_message = f"API密钥认证/权限/路径错误，将永久禁用: {key_id}"
        elif status_code == 429:
            cooldown_duration = 60
            log_level = "warning"
            log_message = f"API密钥被限流，冷却60秒: {key_id}"
        else:
            log_level = "warning"
            log_message = f"API密钥遇到临时性错误，冷却{cooldown_duration}秒: {key_id}"

        async with self._lock:
            stats = self._key_stats.setdefault(api_key, KeyStats())
            stats.cooldown_until = now + cooldown_duration
            stats.failure_count += 1
            stats.last_error_info = error_message[:256]
            await self._save_to_file_internal()

        getattr(logger, log_level)(log_message)

    async def reset_key_status(self, api_key: str):
        """重置密钥状态，并持久化"""
        async with self._lock:
            stats = self._key_stats.setdefault(api_key, KeyStats())
            stats.cooldown_until = 0.0
            stats.last_error_info = None
            await self._save_to_file_internal()
        logger.info(f"重置API密钥状态: {self._get_key_id(api_key)}")

    async def get_key_stats(self, api_keys: list[str]) -> dict[str, dict]:
        """
        获取密钥使用统计，并计算出用于展示的派生数据。

        参数:
            api_keys: API密钥列表。

        返回:
            dict[str, dict]: 包含丰富状态和统计信息的密钥字典。
        """
        stats_dict = {}
        now = time.time()
        async with self._lock:
            for key in api_keys:
                key_id = self._get_key_id(key)
                stats = self._key_stats.get(key, KeyStats())

                stats_dict[key_id] = {
                    "status_enum": stats.status,
                    "cooldown_seconds_left": max(0, stats.cooldown_until - now),
                    "total_calls": stats.success_count + stats.failure_count,
                    "success_count": stats.success_count,
                    "failure_count": stats.failure_count,
                    "success_rate": stats.success_rate,
                    "avg_latency": stats.avg_latency,
                    "last_error": stats.last_error_info,
                    "suggested_action": stats.suggested_action,
                }
        return stats_dict

    def _get_key_id(self, api_key: str) -> str:
        """获取API密钥的标识符（用于日志）"""
        if len(api_key) <= 8:
            return api_key
        return f"{api_key[:4]}...{api_key[-4:]}"


key_store = KeyStatusStore()


@driver.on_shutdown
async def _shutdown_key_store():
    await key_store.shutdown()
