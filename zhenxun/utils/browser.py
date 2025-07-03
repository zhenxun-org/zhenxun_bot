from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_htmlrender import get_browser
from playwright.async_api import Page

from zhenxun.utils.message import MessageUtils


class BrowserIsNone(Exception):
    pass


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
