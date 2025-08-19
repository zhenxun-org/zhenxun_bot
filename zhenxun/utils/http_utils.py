import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
import os
from pathlib import Path
import time
from typing import Any, ClassVar, cast

import aiofiles
import httpx
from httpx import AsyncClient, AsyncHTTPTransport, HTTPStatusError, Proxy, Response
import nonebot
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TransferSpeedColumn,
)
import ujson as json

from zhenxun.configs.config import BotConfig
from zhenxun.services.log import logger
from zhenxun.utils.decorator.retry import Retry
from zhenxun.utils.exception import AllURIsFailedError
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.user_agent import get_user_agent

from .browser import AsyncPlaywright, BrowserIsNone  # noqa: F401

_SENTINEL = object()

driver = nonebot.get_driver()
_client: AsyncClient | None = None


@PriorityLifecycle.on_startup(priority=0)
async def _():
    """
    在Bot启动时初始化全局httpx客户端。
    """
    global _client
    client_kwargs = {}
    if proxy_url := BotConfig.system_proxy or None:
        try:
            version_parts = httpx.__version__.split(".")
            major = int("".join(c for c in version_parts[0] if c.isdigit()))
            minor = (
                int("".join(c for c in version_parts[1] if c.isdigit()))
                if len(version_parts) > 1
                else 0
            )
            if (major, minor) >= (0, 28):
                client_kwargs["proxy"] = proxy_url
            else:
                client_kwargs["proxies"] = proxy_url
        except (ValueError, IndexError):
            client_kwargs["proxy"] = proxy_url
            logger.warning(
                f"无法解析 httpx 版本 '{httpx.__version__}'，"
                "将默认使用新版 'proxy' 参数语法。"
            )

    _client = get_async_client(
        headers=get_user_agent(),
        follow_redirects=True,
        **client_kwargs,
    )

    logger.info("全局 httpx.AsyncClient 已启动。", "HTTPClient")


@driver.on_shutdown
async def _():
    """
    在Bot关闭时关闭全局httpx客户端。
    """
    if _client:
        await _client.aclose()
        logger.info("全局 httpx.AsyncClient 已关闭。", "HTTPClient")


def get_client() -> AsyncClient:
    """
    获取全局 httpx.AsyncClient 实例。
    """
    global _client
    if not _client:
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            raise RuntimeError("全局 httpx.AsyncClient 未初始化，请检查启动流程。")
        # 在测试环境中创建临时客户端
        logger.warning("在测试环境中创建临时HTTP客户端", "HTTPClient")
        _client = get_async_client(
            headers=get_user_agent(),
            follow_redirects=True,
        )
    return _client


def get_async_client(
    proxies: dict[str, str] | str | None = None,
    proxy: str | None = None,
    verify: bool = False,
    **kwargs,
) -> httpx.AsyncClient:
    """
    [向后兼容] 创建 httpx.AsyncClient 实例的工厂函数。
    此函数完全保留了旧版本的接口，确保现有代码无需修改即可使用。
    """
    transport = kwargs.pop("transport", None) or AsyncHTTPTransport(verify=verify)
    if proxies:
        if isinstance(proxies, str):
            proxies = {"http://": proxies, "https://": proxies}
        http_proxy = proxies.get("http://")
        https_proxy = proxies.get("https://")
        return httpx.AsyncClient(
            mounts={
                "http://": AsyncHTTPTransport(
                    proxy=Proxy(http_proxy) if http_proxy else None
                ),
                "https://": AsyncHTTPTransport(
                    proxy=Proxy(https_proxy) if https_proxy else None
                ),
            },
            transport=transport,
            **kwargs,
        )
    elif proxy:
        return httpx.AsyncClient(
            mounts={
                "http://": AsyncHTTPTransport(proxy=Proxy(proxy)),
                "https://": AsyncHTTPTransport(proxy=Proxy(proxy)),
            },
            transport=transport,
            **kwargs,
        )
    return httpx.AsyncClient(transport=transport, **kwargs)


class AsyncHttpx:
    """
    高性能异步HTTP客户端工具类。

    特性:
    - 全局共享连接池，提升性能
    - 支持临时客户端配置（代理、超时等）
    - 内置重试机制和多URL回退
    - 提供JSON解析和文件下载功能
    """

    CLIENT_KEY: ClassVar[list[str]] = [
        "use_proxy",
        "proxies",
        "proxy",
        "verify",
    ]

    default_proxy: ClassVar[dict[str, str] | None] = (
        {
            "http://": BotConfig.system_proxy,
            "https://": BotConfig.system_proxy,
        }
        if BotConfig.system_proxy
        else None
    )

    @classmethod
    def _prepare_temporary_client_config(cls, client_kwargs: dict) -> dict:
        """
        [向后兼容] 处理旧式的客户端kwargs，将其转换为get_async_client可用的配置。
        主要负责处理 use_proxy 标志，这是为了兼容旧版本代码中使用的 use_proxy 参数。
        """
        final_config = client_kwargs.copy()

        use_proxy = final_config.pop("use_proxy", True)

        if "proxies" not in final_config and "proxy" not in final_config:
            final_config["proxies"] = cls.default_proxy if use_proxy else None
        return final_config

    @classmethod
    def _split_kwargs(cls, kwargs: dict) -> tuple[dict, dict]:
        """[优化] 分离客户端配置和请求参数，使逻辑更清晰。"""
        client_kwargs = {k: v for k, v in kwargs.items() if k in cls.CLIENT_KEY}
        request_kwargs = {k: v for k, v in kwargs.items() if k not in cls.CLIENT_KEY}
        return client_kwargs, request_kwargs

    @classmethod
    @asynccontextmanager
    async def _get_active_client_context(
        cls, client: AsyncClient | None = None, **kwargs
    ) -> AsyncGenerator[AsyncClient, None]:
        """
          内部辅助方法，根据 kwargs 决定并提供一个活动的 HTTP 客户端。
        - 如果 kwargs 中有客户端配置，则创建并返回一个临时客户端。
        - 否则，返回传入的 client 或全局客户端。
        - 自动处理临时客户端的关闭。
        """
        if kwargs:
            logger.debug(f"为单次请求创建临时客户端，配置: {kwargs}")
            temp_client_config = cls._prepare_temporary_client_config(kwargs)
            async with get_async_client(**temp_client_config) as temp_client:
                yield temp_client
        else:
            yield client or get_client()

    @Retry.simple(log_name="内部HTTP请求")
    async def _execute_request_inner(
        self, client: AsyncClient, method: str, url: str, **kwargs
    ) -> Response:
        """
        [内部] 执行单次HTTP请求的私有核心方法，被重试装饰器包裹。
        """
        return await client.request(method, url, **kwargs)

    @classmethod
    async def _single_request(
        cls, method: str, url: str, *, client: AsyncClient | None = None, **kwargs
    ) -> Response:
        """
        执行单次HTTP请求的私有方法，内置了默认的重试逻辑。
        """
        client_kwargs, request_kwargs = cls._split_kwargs(kwargs)

        async with cls._get_active_client_context(
            client=client, **client_kwargs
        ) as active_client:
            response = await cls()._execute_request_inner(
                active_client, method, url, **request_kwargs
            )
            response.raise_for_status()
            return response

    @classmethod
    async def _execute_with_fallbacks(
        cls,
        urls: str | list[str],
        worker: Callable[..., Awaitable[Any]],
        *,
        client: AsyncClient | None = None,
        **kwargs,
    ) -> Any:
        """
        通用执行器，按顺序尝试多个URL，直到成功。

        参数:
            urls: 单个URL或URL列表。
            worker: 一个接受单个URL和其他kwargs并执行请求的协程函数。
            client: 可选的HTTP客户端。
            **kwargs: 传递给worker的额外参数。
        """
        url_list = [urls] if isinstance(urls, str) else urls
        exceptions = []

        for i, url in enumerate(url_list):
            try:
                result = await worker(url, client=client, **kwargs)
                if i > 0:
                    logger.info(
                        f"成功从镜像 '{url}' 获取资源 "
                        f"(在尝试了 {i} 个失败的镜像之后)。",
                        "AsyncHttpx:FallbackExecutor",
                    )
                return result
            except Exception as e:
                exceptions.append(e)
                if url != url_list[-1]:
                    logger.warning(
                        f"Worker '{worker.__name__}' on {url} failed, trying next. "
                        f"Error: {e.__class__.__name__}",
                        "AsyncHttpx:FallbackExecutor",
                    )

        raise AllURIsFailedError(url_list, exceptions)

    @classmethod
    async def get(
        cls,
        url: str | list[str],
        *,
        follow_redirects: bool = True,
        check_status_code: int | None = None,
        client: AsyncClient | None = None,
        **kwargs,
    ) -> Response:
        """发送 GET 请求，并返回第一个成功的响应。

        参数:
            url: 单个请求 URL 或一个 URL 列表。
            follow_redirects: 是否跟随重定向。
            check_status_code: (可选) 若提供，将检查响应状态码是否匹配，否则抛出异常。
            client: (可选) 指定一个活动的HTTP客户端实例。若提供，则忽略
                    `**kwargs`中的客户端配置。
            **kwargs: 其他所有传递给 httpx.get 的参数 (如 `params`, `headers`,
                      `timeout`)。如果包含 `proxies`, `verify` 等客户端配置参数，
                      将创建一个临时客户端。

        返回:
            Response: httpx 的响应对象。

        异常:
            AllURIsFailedError: 当所有提供的URL都请求失败时抛出。
        """

        async def worker(current_url: str, **worker_kwargs) -> Response:
            logger.info(f"开始获取 {current_url}..", "AsyncHttpx:get")
            response = await cls._single_request(
                "GET", current_url, follow_redirects=follow_redirects, **worker_kwargs
            )
            if check_status_code and response.status_code != check_status_code:
                raise HTTPStatusError(
                    f"状态码错误: {response.status_code}!={check_status_code}",
                    request=response.request,
                    response=response,
                )
            return response

        return await cls._execute_with_fallbacks(url, worker, client=client, **kwargs)

    @classmethod
    async def head(
        cls, url: str | list[str], *, client: AsyncClient | None = None, **kwargs
    ) -> Response:
        """发送 HEAD 请求，并返回第一个成功的响应。"""

        async def worker(current_url: str, **worker_kwargs) -> Response:
            return await cls._single_request("HEAD", current_url, **worker_kwargs)

        return await cls._execute_with_fallbacks(url, worker, client=client, **kwargs)

    @classmethod
    async def post(
        cls, url: str | list[str], *, client: AsyncClient | None = None, **kwargs
    ) -> Response:
        """发送 POST 请求，并返回第一个成功的响应。"""

        async def worker(current_url: str, **worker_kwargs) -> Response:
            return await cls._single_request("POST", current_url, **worker_kwargs)

        return await cls._execute_with_fallbacks(url, worker, client=client, **kwargs)

    @classmethod
    async def get_content(
        cls, url: str | list[str], *, client: AsyncClient | None = None, **kwargs
    ) -> bytes:
        """获取指定 URL 的二进制内容。"""
        res = await cls.get(url, client=client, **kwargs)
        return res.content

    @classmethod
    @Retry.api(
        log_name="JSON请求",
        exception=(json.JSONDecodeError,),
        return_on_failure=_SENTINEL,
    )
    async def _request_and_parse_json(
        cls, method: str, url: str, *, client: AsyncClient | None = None, **kwargs
    ) -> Any:
        """
        [私有] 执行单个HTTP请求并解析JSON，用于内部统一处理。
        """
        client_kwargs, request_kwargs = cls._split_kwargs(kwargs)

        async with cls._get_active_client_context(
            client=client, **client_kwargs
        ) as active_client:
            response = await active_client.request(method, url, **request_kwargs)
            response.raise_for_status()
            return response.json()

    @classmethod
    async def get_json(
        cls,
        url: str | list[str],
        *,
        default: Any = None,
        raise_on_failure: bool = False,
        client: AsyncClient | None = None,
        **kwargs,
    ) -> Any:
        """
        发送GET请求并自动解析为JSON，支持重试和多链接尝试。

        参数:
            url: 单个请求 URL 或一个备用 URL 列表。
            default: (可选) 当所有尝试都失败时返回的默认值，默认为None。
            raise_on_failure: (可选) 如果为 True, 当所有尝试失败时将抛出
                              `AllURIsFailedError` 异常, 默认为 False.
            client: (可选) 指定的HTTP客户端。
            **kwargs: 其他所有传递给 httpx.get 的参数。
                      例如 `params`, `headers`, `timeout`等。

        返回:
            Any: 解析后的JSON数据，或在失败时返回 `default` 值。

        异常:
            AllURIsFailedError: 当 `raise_on_failure` 为 True 且所有URL都请求失败时抛出
        """

        async def worker(current_url: str, **worker_kwargs):
            logger.debug(f"开始GET JSON: {current_url}", "AsyncHttpx:get_json")
            return await cls._request_and_parse_json(
                "GET", current_url, **worker_kwargs
            )

        try:
            result = await cls._execute_with_fallbacks(
                url, worker, client=client, **kwargs
            )
            return default if result is _SENTINEL else result
        except AllURIsFailedError as e:
            logger.error(f"所有URL的JSON GET均失败: {e}", "AsyncHttpx:get_json")
            if raise_on_failure:
                raise e
            return default

    @classmethod
    async def post_json(
        cls,
        url: str | list[str],
        *,
        json: Any = None,
        data: Any = None,
        default: Any = None,
        raise_on_failure: bool = False,
        client: AsyncClient | None = None,
        **kwargs,
    ) -> Any:
        """
        发送POST请求并自动解析为JSON，功能与 get_json 类似。

        参数:
            url: 单个请求 URL 或一个备用 URL 列表。
            json: (可选) 作为请求体发送的JSON数据。
            data: (可选) 作为请求体发送的表单数据。
            default: (可选) 当所有尝试都失败时返回的默认值，默认为None。
            raise_on_failure: (可选) 如果为 True, 当所有尝试失败时将抛出
                              AllURIsFailedError 异常, 默认为 False.
            client: (可选) 指定的HTTP客户端。
            **kwargs: 其他所有传递给 httpx.post 的参数。

        返回:
            Any: 解析后的JSON数据，或在失败时返回 `default` 值。
        """
        if json is not None:
            kwargs["json"] = json
        if data is not None:
            kwargs["data"] = data

        async def worker(current_url: str, **worker_kwargs):
            logger.debug(f"开始POST JSON: {current_url}", "AsyncHttpx:post_json")
            return await cls._request_and_parse_json(
                "POST", current_url, **worker_kwargs
            )

        try:
            result = await cls._execute_with_fallbacks(
                url, worker, client=client, **kwargs
            )
            return default if result is _SENTINEL else result
        except AllURIsFailedError as e:
            logger.error(f"所有URL的JSON POST均失败: {e}", "AsyncHttpx:post_json")
            if raise_on_failure:
                raise e
            return default

    @classmethod
    @Retry.api(log_name="文件下载(流式)")
    async def _stream_download(
        cls, url: str, path: Path, *, client: AsyncClient | None = None, **kwargs
    ) -> None:
        """
        执行单个流式下载的私有方法，被重试装饰器包裹。
        """
        client_kwargs, request_kwargs = cls._split_kwargs(kwargs)
        show_progress = request_kwargs.pop("show_progress", False)

        async with cls._get_active_client_context(
            client=client, **client_kwargs
        ) as active_client:
            async with active_client.stream("GET", url, **request_kwargs) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length", 0))

                if show_progress:
                    with Progress(
                        TextColumn(path.name),
                        "[progress.percentage]{task.percentage:>3.0f}%",
                        BarColumn(bar_width=None),
                        DownloadColumn(),
                        TransferSpeedColumn(),
                    ) as progress:
                        task_id = progress.add_task("Download", total=total)
                        async with aiofiles.open(path, "wb") as f:
                            async for chunk in response.aiter_bytes():
                                await f.write(chunk)
                                progress.update(task_id, advance=len(chunk))
                else:
                    async with aiofiles.open(path, "wb") as f:
                        async for chunk in response.aiter_bytes():
                            await f.write(chunk)

    @classmethod
    async def download_file(
        cls,
        url: str | list[str],
        path: str | Path,
        *,
        stream: bool = False,
        show_progress: bool = False,
        client: AsyncClient | None = None,
        **kwargs,
    ) -> bool:
        """下载文件到指定路径。

        说明:
            支持多链接尝试和流式下载（带进度条）。

        参数:
            url: 单个文件 URL 或一个备用 URL 列表。
            path: 文件保存的本地路径。
            stream: (可选) 是否使用流式下载，适用于大文件，默认为 False。
            show_progress: (可选) 当 stream=True 时，是否显示下载进度条。默认为 False。
            client: (可选) 指定的HTTP客户端。
            **kwargs: 其他所有传递给 get() 方法或 httpx.stream() 的参数。

        返回:
            bool: 是否下载成功。
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        async def worker(current_url: str, **worker_kwargs) -> bool:
            if not stream:
                content = await cls.get_content(current_url, **worker_kwargs)
                async with aiofiles.open(path, "wb") as f:
                    await f.write(content)
            else:
                await cls._stream_download(
                    current_url, path, show_progress=show_progress, **worker_kwargs
                )

            logger.info(
                f"下载 {current_url} 成功 -> {path.absolute()}",
                "AsyncHttpx:download",
            )
            return True

        try:
            return await cls._execute_with_fallbacks(
                url, worker, client=client, **kwargs
            )
        except AllURIsFailedError:
            logger.error(
                f"所有URL下载均失败 -> {path.absolute()}", "AsyncHttpx:download"
            )
            return False

    @classmethod
    async def gather_download_file(
        cls,
        url_list: Sequence[list[str] | str],
        path_list: Sequence[str | Path],
        *,
        limit_async_number: int = 5,
        **kwargs,
    ) -> list[bool]:
        """并发下载多个文件，支持为每个文件提供备用镜像链接。

        参数:
            url_list: 包含所有文件下载任务的列表。每个元素可以是：
                      - 一个字符串 (str): 代表该任务的唯一URL。
                      - 一个字符串列表 (list[str]): 代表该任务的多个备用/镜像URL。
            path_list: 与 url_list 对应的文件保存路径列表。
            limit_async_number: (可选) 最大并发下载数，默认为 5。
            **kwargs: 其他所有传递给 download_file() 方法的参数。

        返回:
            list[bool]: 对应每个下载任务是否成功。
        """
        if len(url_list) != len(path_list):
            raise ValueError("URL 列表和路径列表的长度必须相等")

        semaphore = asyncio.Semaphore(limit_async_number)

        async def _download_with_semaphore(
            urls_for_one_path: str | list[str], path: str | Path
        ):
            async with semaphore:
                return await cls.download_file(urls_for_one_path, path, **kwargs)

        tasks = [
            _download_with_semaphore(url_group, path)
            for url_group, path in zip(url_list, path_list)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                url_info = (
                    url_list[i]
                    if isinstance(url_list[i], str)
                    else ", ".join(url_list[i])
                )
                logger.error(f"并发下载任务 ({url_info}) 时发生错误", e=result)
                final_results.append(False)
            else:
                final_results.append(cast(bool, result))

        return final_results

    @classmethod
    async def get_fastest_mirror(cls, url_list: list[str]) -> list[str]:
        """测试并返回最快的镜像地址。

        参数:
            url_list: 需要测试的镜像 URL 列表。

        返回:
            list[str]: 按从快到慢的顺序包含了所有可用的 URL。
        """
        assert url_list

        async def head_mirror(client: type[AsyncHttpx], url: str) -> dict[str, Any]:
            begin_time = time.time()

            response = await client.head(url=url, timeout=6)

            elapsed_time = (time.time() - begin_time) * 1000
            content_length = int(response.headers.get("content-length", 0))

            return {
                "url": url,
                "elapsed_time": elapsed_time,
                "content_length": content_length,
            }

        logger.debug(f"开始获取最快镜像，可能需要一段时间... | URL列表：{url_list}")
        results = await asyncio.gather(
            *(head_mirror(cls, url) for url in url_list),
            return_exceptions=True,
        )
        _results: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.warning(f"获取镜像失败，错误：{result}")
            else:
                logger.debug(f"获取镜像成功，结果：{result}")
                _results.append(result)
        _results = sorted(iter(_results), key=lambda r: r["elapsed_time"])
        return [result["url"] for result in _results]

    @classmethod
    @asynccontextmanager
    async def temporary_client(cls, **kwargs) -> AsyncGenerator[AsyncClient, None]:
        """
        创建一个临时的、可配置的HTTP客户端上下文，并直接返回该客户端实例。

        参数:
            **kwargs: 所有传递给 `httpx.AsyncClient` 构造函数的参数。
                      例如: `proxies`, `headers`, `verify`, `timeout`,
                      `follow_redirects`。

        返回:
            httpx.AsyncClient: 一个配置好的、临时的客户端实例。
        """
        async with get_async_client(**kwargs) as client:
            yield client
