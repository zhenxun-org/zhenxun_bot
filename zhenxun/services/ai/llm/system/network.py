"""
LLM 核心基础设施模块

包含执行 LLM 请求所需的底层组件，如 HTTP 客户端、API Key 存储和智能重试逻辑。
"""

import asyncio
import json
import os
from pathlib import Path
import time
from typing import Any

import aiofiles
import httpx
import nonebot

from zhenxun.configs.config import BotConfig
from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.ai.config import ProviderConfig
from zhenxun.services.ai.core.exceptions import (
    AuthenticationException,
    ConfigurationException,
    LocationNotSupportedException,
    QuotaExceededException,
    RateLimitException,
)
from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import model_dump, parse_as
from zhenxun.utils.user_agent import get_user_agent

from .models import (
    CircuitBreakerPolicy,
    GlobalHealthState,
    HttpClientConfig,
    KeyHealthStatus,
    ProviderHealthStatus,
    RouteHealthState,
    RouteHealthStatus,
)

driver = nonebot.get_driver()


class LLMHttpClient:
    """[内部 API] LLM 服务专用异步 HTTP 客户端封装。"""

    def __init__(self, config: HttpClientConfig | None = None):
        """初始化 LLM 服务专用 HTTP 客户端"""
        self.config = config or HttpClientConfig()
        self._client: httpx.AsyncClient | None = None
        self._active_requests = 0
        self._lock = asyncio.Lock()

    async def _ensure_client_initialized(self) -> httpx.AsyncClient:
        """确保底层的 AsyncClient 已完成初始化"""
        if self._client is None or self._client.is_closed:
            async with self._lock:
                if self._client is None or self._client.is_closed:
                    logger.debug(
                        f"LLMHttpClient: 正在初始化新的 httpx.AsyncClient "
                        f"配置: {self.config}"
                    )
                    headers = get_user_agent()
                    limits = httpx.Limits(
                        max_connections=self.config.max_connections,
                        max_keepalive_connections=self.config.max_keepalive_connections,
                    )
                    timeout = httpx.Timeout(self.config.timeout)

                    client_kwargs = {}
                    if BotConfig.system_proxy:
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
                                client_kwargs["proxy"] = BotConfig.system_proxy
                            else:
                                client_kwargs["proxies"] = BotConfig.system_proxy
                        except (ValueError, IndexError):
                            client_kwargs["proxies"] = BotConfig.system_proxy
                            logger.warning(
                                f"无法解析 httpx version '{httpx.__version__}'，"
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
            raise ConfigurationException(
                "HTTP 客户端初始化失败。",
            )
        return self._client

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """发送异步 HTTP 请求"""
        client = await self._ensure_client_initialized()
        async with self._lock:
            self._active_requests += 1
        try:
            return await client.request(method, url, **kwargs)
        finally:
            async with self._lock:
                self._active_requests -= 1

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """发送异步 POST 请求"""
        return await self.request("POST", url, **kwargs)

    async def close(self):
        """安全关闭 HTTP 客户端并释放连接池"""
        async with self._lock:
            if self._client and not self._client.is_closed:
                logger.debug(
                    f"LLMHttpClient: 正在关闭，配置: {self.config}. "
                    f"活跃请求数: {self._active_requests}"
                )
                if self._active_requests > 0:
                    logger.warning(
                        f"LLMHttpClient: 关闭时仍有 {self._active_requests} "
                        f"个请求处于活跃状态。"
                    )
                await self._client.aclose()
            self._client = None
        logger.debug(f"配置为 {self.config} 的 LLMHttpClient 已完全关闭。")

    @property
    def is_closed(self) -> bool:
        """检查底层客户端是否已关闭"""
        return self._client is None or self._client.is_closed


class LLMHttpClientManager:
    """[内部 API] 负责管理与复用 LLMHttpClient 连接池。"""

    def __init__(self):
        """初始化客户端管理器"""
        self._clients: dict[tuple[str, str, int], LLMHttpClient] = {}
        self._lock = asyncio.Lock()

    def _get_client_key(self, provider_config: ProviderConfig) -> tuple[str, str, int]:
        """获取客户端唯一缓存标识"""
        api_base = provider_config.api_base or ""
        return (provider_config.api_type, api_base, provider_config.timeout)

    async def get_client(self, provider_config: ProviderConfig) -> LLMHttpClient:
        """获取或创建指定配置的 HTTP 客户端实例"""
        key = self._get_client_key(provider_config)
        async with self._lock:
            client = self._clients.get(key)
            if client and not client.is_closed:
                logger.debug(
                    f"LLMHttpClientManager: 复用现有的 LLMHttpClient 密钥: {key}"
                )
                return client

            if client and client.is_closed:
                logger.debug(
                    f"LLMHttpClientManager: 发现密钥 {key} 对应的客户端已关闭。"
                    f"正在创建新的客户端。"
                )

            logger.debug(f"LLMHttpClientManager: 为密钥 {key} 创建新的 LLMHttpClient")
            http_client_config = HttpClientConfig(timeout=provider_config.timeout)
            new_client = LLMHttpClient(config=http_client_config)
            self._clients[key] = new_client
            return new_client

    async def shutdown(self):
        """关闭所有托管的客户端连接池"""
        async with self._lock:
            logger.info(
                f"LLMHttpClientManager: 正在关闭。关闭 {len(self._clients)} 个客户端。"
            )
            close_tasks = [
                client.close()
                for client in self._clients.values()
                if client and not client.is_closed
            ]
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)
            self._clients.clear()
        logger.info("LLMHttpClientManager: 关闭完成。")


http_client_manager = LLMHttpClientManager()


async def create_llm_http_client(
    timeout: int = 180,
) -> LLMHttpClient:
    """创建并返回一个新的 HTTP 客户端"""
    config = HttpClientConfig(timeout=timeout)
    return LLMHttpClient(config)


class HealthStatePersister:
    """后台异步持久化管理器"""

    def __init__(self, state: GlobalHealthState, file_path: Path):
        """初始化持久化管理器"""
        self.state = state
        self.file_path = file_path
        self._is_dirty = False
        self._lock = asyncio.Lock()
        self._watchdog_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self):
        """启动后台异步保存定时任务"""
        if self._watchdog_task is None or self._watchdog_task.done():
            self._stop_event.clear()
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    def mark_dirty(self):
        """标记内存状态已脏，需要存盘"""
        self._is_dirty = True

    async def _watchdog_loop(self):
        """后台循环检测并持久化脏数据"""
        while not self._stop_event.is_set():
            await asyncio.sleep(5)
            if self._is_dirty:
                await self.force_save()

    async def force_save(self):
        """强制将内存中的状态同步写入磁盘文件"""
        if not self._is_dirty:
            return
        async with self._lock:
            self._is_dirty = False

            data_to_save = model_dump(self.state)
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = self.file_path.with_suffix(".json.tmp")
                async with aiofiles.open(temp_path, "w", encoding="utf-8") as f:
                    await f.write(
                        json.dumps(data_to_save, ensure_ascii=False, indent=2)
                    )
                if self.file_path.exists():
                    self.file_path.unlink()
                os.rename(temp_path, self.file_path)
            except Exception as e:
                logger.error(f"保存密钥状态到文件失败: {e}", e=e)

    async def stop(self):
        """停止后台任务并保存所有脏状态"""
        self._stop_event.set()
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        await self.force_save()


class KeyRotationManager:
    """专门负责 API Key 的负载均衡与冷却状态维护"""

    def __init__(self, state: GlobalHealthState):
        """初始化 API Key 轮询管理器"""
        self.state = state
        self._provider_key_index: dict[str, int] = {}

    def get_next_available_key(
        self,
        provider_name: str,
        api_keys: list[str],
        exclude_keys: set[str] | None = None,
        strict_mode: bool = False,
    ) -> str | None:
        """轮询策略获取下一个健康可用的 API Key"""
        if not api_keys:
            return None

        exclude_keys = exclude_keys or set()

        provider_state = self.state.providers.setdefault(
            provider_name, ProviderHealthStatus()
        )
        for key in api_keys:
            if key not in provider_state.api_keys:
                provider_state.api_keys[key] = KeyHealthStatus()

        now = time.time()
        available_keys = [
            key
            for key in api_keys
            if key not in exclude_keys
            and provider_state.api_keys[key].cooldown_until <= now
        ]

        if not available_keys:
            if strict_mode:
                return None
            return api_keys[0]

        current_index = self._provider_key_index.get(provider_name, 0)
        selected_key = available_keys[current_index % len(available_keys)]
        self._provider_key_index[provider_name] = current_index + 1

        stats = provider_state.api_keys[selected_key]
        total_usage = stats.successes + stats.failures
        logger.debug(f"轮询选择API密钥 (使用次数: {total_usage})")
        return selected_key

    def record_key_success(self, provider_name: str, api_key: str):
        """记录指定 API Key 调用成功状态"""
        provider_state = self.state.providers.setdefault(
            provider_name, ProviderHealthStatus()
        )
        stats = provider_state.api_keys.setdefault(api_key, KeyHealthStatus())
        stats.cooldown_until = 0.0
        stats.successes += 1
        stats.status = "HEALTHY"
        stats.last_error = None

    def record_key_failure(
        self,
        provider_name: str,
        api_key: str,
        exception: Exception,
        policy: CircuitBreakerPolicy,
    ):
        """记录并处理指定 API Key 的调用失败冷却"""
        now = time.time()
        cooldown_duration = 0

        error_message = str(exception)

        if isinstance(exception, LocationNotSupportedException):
            provider_state = self.state.providers.setdefault(
                provider_name, ProviderHealthStatus()
            )
            stats = provider_state.api_keys.setdefault(api_key, KeyHealthStatus())
            stats.failures += 1
            stats.last_error = error_message[:256]
            return

        if isinstance(exception, QuotaExceededException):
            cooldown_duration = policy.quota_error_cooldown
        elif isinstance(exception, AuthenticationException):
            cooldown_duration = policy.auth_error_cooldown
        elif isinstance(exception, RateLimitException):
            cooldown_duration = policy.rate_limit_cooldown

        provider_state = self.state.providers.setdefault(
            provider_name, ProviderHealthStatus()
        )
        stats = provider_state.api_keys.setdefault(api_key, KeyHealthStatus())
        if cooldown_duration > 0:
            stats.cooldown_until = now + cooldown_duration
            stats.status = (
                "COOLDOWN"
                if cooldown_duration < policy.auth_error_cooldown
                else "DISABLED"
            )
        stats.failures += 1
        stats.last_error = error_message[:256]

    def reset_key_status(self, provider_name: str, api_key: str):
        """重置 API Key 状态为健康"""
        provider_state = self.state.providers.setdefault(
            provider_name, ProviderHealthStatus()
        )
        stats = provider_state.api_keys.setdefault(api_key, KeyHealthStatus())
        stats.cooldown_until = 0.0
        stats.last_error = None
        stats.status = "HEALTHY"


class CircuitBreakerManager:
    """专门负责模型路由级别的熔断与探活 (无锁化设计)"""

    def __init__(self, state: GlobalHealthState):
        """初始化路由级熔断管理器"""
        self.state = state

    def is_route_healthy(self, route_name: str, strict_mode: bool = True) -> bool:
        """检查路由节点的健康与熔断状态"""
        stats = self.state.routes.get(route_name)
        if not stats:
            return True

        if stats.state == RouteHealthState.CLOSED:
            return True

        if stats.state == RouteHealthState.OPEN:
            if time.time() >= stats.cooldown_until:
                stats.state = RouteHealthState.HALF_OPEN
                logger.info(
                    f"🔄 [Route-Level] 节点 '{route_name}' "
                    "冷却期结束，进入 HALF_OPEN 半开试探状态。"
                )
                return True
            if not strict_mode:
                logger.debug(
                    f"👉 [Route-Level] 节点 '{route_name}' 处于熔断状态(OPEN)，"
                    "但因非严格模式(单模型直调)，强制放行执行探活。"
                )
                return True
            return False

        if stats.state == RouteHealthState.HALF_OPEN:
            if not strict_mode:
                return True
            return False

        return True

    def record_route_success(self, route_name: str, latency: float):
        """记录路由请求成功并尝试闭合熔断器"""
        stats = self.state.routes.setdefault(route_name, RouteHealthStatus())
        stats.successes += 1
        total = stats.successes + stats.failures
        stats.success_rate = (stats.successes / total) * 100

        if stats.latency_ema == 0.0:
            stats.latency_ema = latency
        else:
            stats.latency_ema = 0.2 * latency + 0.8 * stats.latency_ema

        if stats.state != RouteHealthState.CLOSED:
            logger.info(
                f"✅ [Route-Level] 节点 '{route_name}' "
                "试探成功！已完全恢复健康状态 (CLOSED)。"
            )
            stats.state = RouteHealthState.CLOSED
            stats.cooldown_until = 0.0

        stats.last_error = None

    def record_route_failure(
        self, route_name: str, exception: Exception, policy: CircuitBreakerPolicy
    ):
        """记录路由失败并开启熔断状态"""
        now = time.time()
        cooldown_duration = policy.server_error_cooldown

        stats = self.state.routes.setdefault(route_name, RouteHealthStatus())
        stats.failures += 1
        total = stats.successes + stats.failures
        stats.success_rate = (stats.successes / total) * 100

        stats.state = RouteHealthState.OPEN
        stats.cooldown_until = now + cooldown_duration
        stats.last_error = str(exception)[:256]

    def get_best_fallback_route(self, route_names: list[str]) -> str:
        """选择处于熔断冷却最少或最健康的备选路由"""
        def get_cooldown(name: str) -> float:
            stats = self.state.routes.get(name)
            return stats.cooldown_until if stats else 0.0

        return sorted(route_names, key=get_cooldown)[0]


class HealthManager:
    """全局 AI 健康与遥测门面"""

    def __init__(self):
        """初始化健康监测与遥测门面"""
        self.state = GlobalHealthState()
        self._file_path = DATA_PATH / "ai" / "api_key.json"
        self._persister: HealthStatePersister | None = None
        self._key_manager = KeyRotationManager(self.state)
        self._circuit_manager = CircuitBreakerManager(self.state)
        self.policy = CircuitBreakerPolicy()

    async def initialize(self):
        """从本地文件异步加载遥测状态"""
        if not self._file_path.exists():
            logger.debug("未找到遥测状态文件，将使用内存状态启动。")
        else:
            try:
                import aiofiles

                logger.debug(f"正在从 {self._file_path} 加载密钥状态...")
                async with aiofiles.open(self._file_path, encoding="utf-8") as f:
                    content = await f.read()
                    if content:
                        self.state = parse_as(GlobalHealthState, json.loads(content))
                        self._key_manager.state = self.state
                        self._circuit_manager.state = self.state
                total_keys = sum(
                    len(provider.api_keys) for provider in self.state.providers.values()
                )
                logger.debug(f"成功加载 {total_keys} 个密钥的状态。")
            except json.JSONDecodeError:
                logger.error(f"遥测状态文件 {self._file_path} 格式错误，无法解析。")
            except Exception as e:
                logger.error(f"加载遥测状态文件时发生错误: {e}", e=e)

        self._persister = HealthStatePersister(self.state, self._file_path)
        self._persister.start()

    async def shutdown(self):
        """在应用关闭时安全地持久化健康状态"""
        if self._persister:
            await self._persister.stop()
        logger.debug("HealthManager 已在关闭前保存遥测状态。")

    async def get_next_available_key(
        self,
        provider_name: str,
        api_keys: list[str],
        exclude_keys: set[str] | None = None,
        strict_mode: bool = False,
    ) -> str | None:
        """路由获取下一个可用 API Key"""
        return self._key_manager.get_next_available_key(
            provider_name, api_keys, exclude_keys, strict_mode
        )

    def is_route_healthy(self, route_name: str, strict_mode: bool = True) -> bool:
        """路由判断指定模型路由是否健康"""
        return self._circuit_manager.is_route_healthy(route_name, strict_mode)

    async def record_route_success(self, route_name: str, latency: float):
        """路由记录模型请求成功"""
        self._circuit_manager.record_route_success(route_name, latency)
        if self._persister:
            self._persister.mark_dirty()

    async def record_route_failure(self, route_name: str, exception: Exception):
        """路由记录模型请求失败并记录熔断"""
        self._circuit_manager.record_route_failure(route_name, exception, self.policy)
        if self._persister:
            self._persister.mark_dirty()
        logger.warning(
            f"🚨 [Route-Level] 节点 '{route_name}' 发生服务端故障，"
            f"已触发熔断 (OPEN)。错误: {exception}"
        )

    def get_best_fallback_route(self, route_names: list[str]) -> str:
        """路由获取最佳备选健康节点"""
        return self._circuit_manager.get_best_fallback_route(route_names)

    async def record_key_success(self, provider_name: str, api_key: str):
        """路由记录 API Key 成功状态"""
        self._key_manager.record_key_success(provider_name, api_key)
        if self._persister:
            self._persister.mark_dirty()

    async def record_key_failure(
        self,
        provider_name: str,
        api_key: str,
        exception: Exception,
    ):
        """路由记录 API Key 失败冷却状态"""
        self._key_manager.record_key_failure(
            provider_name, api_key, exception, self.policy
        )
        if self._persister:
            self._persister.mark_dirty()
        key_id = self._get_key_id(api_key)
        logger.debug(f"API Key {key_id} 发生失败: {exception}")

    async def reset_key_status(self, provider_name: str, api_key: str):
        """路由重置 API Key 的健康状态"""
        self._key_manager.reset_key_status(provider_name, api_key)
        if self._persister:
            self._persister.mark_dirty()
        logger.info(f"重置API密钥状态: {self._get_key_id(api_key)}")

    def _get_key_id(self, api_key: str) -> str:
        """脱敏获取用于日志的 API Key 摘要 ID"""
        if len(api_key) <= 8:
            return api_key
        return f"{api_key[:4]}...{api_key[-4:]}"


health_manager = HealthManager()


@driver.on_shutdown
async def _shutdown_health_manager():
    await health_manager.shutdown()
