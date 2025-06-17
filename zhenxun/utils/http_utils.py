import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
import time
from typing import Any, ClassVar, Literal, cast

import aiofiles
import httpx
from httpx import AsyncHTTPTransport, HTTPStatusError, Proxy, Response
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_htmlrender import get_browser
from playwright.async_api import Page
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TransferSpeedColumn,
)

from zhenxun.configs.config import BotConfig
from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.user_agent import get_user_agent

CLIENT_KEY = ["use_proxy", "proxies", "verify", "headers"]


def get_async_client(
    proxies: dict[str, str] | None = None, verify: bool = False, **kwargs
) -> httpx.AsyncClient:
    transport = kwargs.pop("transport", None) or AsyncHTTPTransport(verify=verify)
    if proxies:
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
    return httpx.AsyncClient(transport=transport, **kwargs)


class AsyncHttpx:
    default_proxy: ClassVar[dict[str, str] | None] = (
        {
            "http://": BotConfig.system_proxy,
            "https://": BotConfig.system_proxy,
        }
        if BotConfig.system_proxy
        else None
    )

    @classmethod
    @asynccontextmanager
    async def _create_client(
        cls,
        *,
        use_proxy: bool = True,
        proxies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        verify: bool = False,
        **kwargs,
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        """创建一个私有的、配置好的 httpx.AsyncClient 上下文管理器。

        说明:
            此方法用于内部统一创建客户端，处理代理和请求头逻辑，减少代码重复。

        参数:
            use_proxy: 是否使用在类中定义的默认代理。
            proxies: 手动指定的代理，会覆盖默认代理。
            headers: 需要合并到客户端的自定义请求头。
            verify: 是否验证 SSL 证书。
            **kwargs: 其他所有传递给 httpx.AsyncClient 的参数。

        返回:
            AsyncGenerator[httpx.AsyncClient, None]: 生成器。
        """
        proxies_to_use = proxies or (cls.default_proxy if use_proxy else None)

        final_headers = get_user_agent()
        if headers:
            final_headers.update(headers)

        async with get_async_client(
            proxies=proxies_to_use, verify=verify, headers=final_headers, **kwargs
        ) as client:
            yield client

    @classmethod
    async def get(
        cls,
        url: str | list[str],
        *,
        check_status_code: int | None = None,
        **kwargs,
    ) -> Response:  # sourcery skip: use-assigned-variable
        """发送 GET 请求，并返回第一个成功的响应。

        说明:
            本方法是 httpx.get 的高级包装，增加了多链接尝试、自动重试和统一的代理管理。
            如果提供 URL 列表，它将依次尝试直到成功为止。

        参数:
            url: 单个请求 URL 或一个 URL 列表。
            check_status_code: (可选) 若提供，将检查响应状态码是否匹配，否则抛出异常。
            **kwargs: 其他所有传递给 httpx.get 的参数
                    (如 `params`, `headers`, `timeout`等)。

        返回:
            Response: Response
        """
        urls = [url] if isinstance(url, str) else url
        last_exception = None
        for current_url in urls:
            try:
                logger.info(f"开始获取 {current_url}..")
                client_kwargs = {k: v for k, v in kwargs.items() if k in CLIENT_KEY}
                for key in CLIENT_KEY:
                    kwargs.pop(key, None)
                async with cls._create_client(**client_kwargs) as client:
                    response = await client.get(current_url, **kwargs)

                if check_status_code and response.status_code != check_status_code:
                    raise HTTPStatusError(
                        f"状态码错误: {response.status_code}!={check_status_code}",
                        request=response.request,
                        response=response,
                    )
                return response
            except Exception as e:
                last_exception = e
                if current_url != urls[-1]:
                    logger.warning(f"获取 {current_url} 失败, 尝试下一个", e=e)

        raise last_exception or Exception("所有URL都获取失败")

    @classmethod
    async def head(cls, url: str, **kwargs) -> Response:
        """发送 HEAD 请求。

        说明:
            本方法是对 httpx.head 的封装，通常用于检查资源的元信息（如大小、类型）。

        参数:
            url: 请求的 URL。
            **kwargs: 其他所有传递给 httpx.head 的参数
                        (如 `headers`, `timeout`, `allow_redirects`)。

        返回:
            Response: Response
        """
        client_kwargs = {k: v for k, v in kwargs.items() if k in CLIENT_KEY}
        for key in CLIENT_KEY:
            kwargs.pop(key, None)
        async with cls._create_client(**client_kwargs) as client:
            return await client.head(url, **kwargs)

    @classmethod
    async def post(cls, url: str, **kwargs) -> Response:
        """发送 POST 请求。

        说明:
            本方法是对 httpx.post 的封装，提供了统一的代理和客户端管理。

        参数:
            url: 请求的 URL。
            **kwargs: 其他所有传递给 httpx.post 的参数
                        (如 `data`, `json`, `content` 等)。

        返回:
            Response: Response。
        """
        client_kwargs = {k: v for k, v in kwargs.items() if k in CLIENT_KEY}
        for key in CLIENT_KEY:
            kwargs.pop(key, None)
        async with cls._create_client(**client_kwargs) as client:
            return await client.post(url, **kwargs)

    @classmethod
    async def get_content(cls, url: str, **kwargs) -> bytes:
        """获取指定 URL 的二进制内容。

        说明:
            这是一个便捷方法，等同于调用 get() 后再访问 .content 属性。

        参数:
            url: 请求的 URL。
            **kwargs: 所有传递给 get() 方法的参数。

        返回:
            bytes: 响应内容的二进制字节流 (bytes)。
        """
        res = await cls.get(url, **kwargs)
        return res.content

    @classmethod
    async def download_file(
        cls,
        url: str | list[str],
        path: str | Path,
        *,
        stream: bool = False,
        **kwargs,
    ) -> bool:
        """下载文件到指定路径。

        说明:
            支持多链接尝试和流式下载（带进度条）。

        参数:
            url: 单个文件 URL 或一个备用 URL 列表。
            path: 文件保存的本地路径。
            stream: (可选) 是否使用流式下载，适用于大文件，默认为 False。
            **kwargs: 其他所有传递给 get() 方法或 httpx.stream() 的参数。

        返回:
            bool: 是否下载成功。
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        urls = [url] if isinstance(url, str) else url

        for current_url in urls:
            try:
                if not stream:
                    response = await cls.get(current_url, **kwargs)
                    response.raise_for_status()
                    async with aiofiles.open(path, "wb") as f:
                        await f.write(response.content)
                else:
                    async with cls._create_client(**kwargs) as client:
                        stream_kwargs = {
                            k: v
                            for k, v in kwargs.items()
                            if k not in ["use_proxy", "proxy", "verify"]
                        }
                        async with client.stream(
                            "GET", current_url, **stream_kwargs
                        ) as response:
                            response.raise_for_status()
                            total = int(response.headers.get("Content-Length", 0))

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

                logger.info(f"下载 {current_url} 成功 -> {path.absolute()}")
                return True

            except Exception as e:
                logger.warning(f"下载 {current_url} 失败，尝试下一个。错误: {e}")

        logger.error(f"所有URL {urls} 下载均失败 -> {path.absolute()}")
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

        说明:
            使用 asyncio.Semaphore 来控制并发请求的数量。
            对于 url_list 中的每个元素，如果它是一个列表，则会依次尝试直到下载成功。

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
                # download_file 返回的是 bool，可以直接附加
                final_results.append(cast(bool, result))

        return final_results

    @classmethod
    async def get_fastest_mirror(cls, url_list: list[str]) -> list[str]:
        """测试并返回最快的镜像地址。

        说明:
            通过并发发送 HEAD 请求来测试每个 URL 的响应时间和可用性，并按响应速度排序。

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


class AsyncPlaywright:
    @classmethod
    @asynccontextmanager
    async def new_page(
        cls, cookies: list[dict[str, Any]] | dict[str, Any] | None = None, **kwargs
    ) -> AsyncGenerator[Page, None]:
        """获取一个新页面

        参数:
            cookies: cookies
        """
        browser = await get_browser()
        ctx = await browser.new_context(**kwargs)
        if cookies:
            if isinstance(cookies, dict):
                cookies = [cookies]
            await ctx.add_cookies(cookies)  # type: ignore
        page = await ctx.new_page()
        try:
            yield page
        finally:
            await page.close()
            await ctx.close()

    @classmethod
    async def screenshot(
        cls,
        url: str,
        path: Path | str,
        element: str | list[str],
        *,
        wait_time: int | None = None,
        viewport_size: dict[str, int] | None = None,
        wait_until: (
            Literal["domcontentloaded", "load", "networkidle"] | None
        ) = "networkidle",
        timeout: float | None = None,
        type_: Literal["jpeg", "png"] | None = None,
        user_agent: str | None = None,
        cookies: list[dict[str, Any]] | dict[str, Any] | None = None,
        **kwargs,
    ) -> UniMessage | None:
        """截图，该方法仅用于简单快捷截图，复杂截图请操作 page

        参数:
            url: 网址
            path: 存储路径
            element: 元素选择
            wait_time: 等待截取超时时间
            viewport_size: 窗口大小
            wait_until: 等待类型
            timeout: 超时限制
            type_: 保存类型
            user_agent: user_agent
            cookies: cookies
        """
        if viewport_size is None:
            viewport_size = {"width": 2560, "height": 1080}
        if isinstance(path, str):
            path = Path(path)
        wait_time = wait_time * 1000 if wait_time else None
        element_list = [element] if isinstance(element, str) else element
        async with cls.new_page(
            cookies,
            viewport=viewport_size,
            user_agent=user_agent,
            **kwargs,
        ) as page:
            await page.goto(url, timeout=timeout, wait_until=wait_until)
            card = page
            for e in element_list:
                if not card:
                    return None
                card = await card.wait_for_selector(e, timeout=wait_time)
            if card:
                await card.screenshot(path=path, timeout=timeout, type=type_)
                return MessageUtils.build_message(path)
        return None


class BrowserIsNone(Exception):
    pass
