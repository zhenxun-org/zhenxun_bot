from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import os
from pathlib import Path
import sys
from typing import Any, Literal

from nonebot import get_driver
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_htmlrender import get_browser
from playwright.__main__ import main
from playwright.async_api import Browser, Page, Playwright, async_playwright

from zhenxun.configs.config import BotConfig
from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils

driver = get_driver()

_playwright: Playwright | None = None
_browser: Browser | None = None


# @driver.on_startup
# async def start_browser():
#     global _playwright
#     global _browser
#     install()
#     await check_playwright_env()
#     _playwright = await async_playwright().start()
#     _browser = await _playwright.chromium.launch()


# @driver.on_shutdown
# async def shutdown_browser():
#     if _browser:
#         await _browser.close()
#     if _playwright:
#         await _playwright.stop()  # type: ignore


# def get_browser() -> Browser:
#     if not _browser:
#         raise RuntimeError("playwright is not initalized")
#     return _browser


def install():
    """自动安装、更新 Chromium"""

    def set_env_variables():
        os.environ["PLAYWRIGHT_DOWNLOAD_HOST"] = (
            "https://npmmirror.com/mirrors/playwright/"
        )
        if BotConfig.system_proxy:
            os.environ["HTTPS_PROXY"] = BotConfig.system_proxy

    def restore_env_variables():
        os.environ.pop("PLAYWRIGHT_DOWNLOAD_HOST", None)
        if BotConfig.system_proxy:
            os.environ.pop("HTTPS_PROXY", None)
        if original_proxy is not None:
            os.environ["HTTPS_PROXY"] = original_proxy

    def try_install_chromium():
        try:
            sys.argv = ["", "install", "chromium"]
            main()
        except SystemExit as e:
            return e.code == 0
        return False

    logger.info("检查 Chromium 更新")

    original_proxy = os.environ.get("HTTPS_PROXY")
    set_env_variables()

    success = try_install_chromium()

    if not success:
        logger.info("Chromium 更新失败，尝试从原始仓库下载，速度较慢")
        os.environ["PLAYWRIGHT_DOWNLOAD_HOST"] = ""
        success = try_install_chromium()

    restore_env_variables()

    if not success:
        raise RuntimeError("未知错误，Chromium 下载失败")


async def check_playwright_env():
    """检查 Playwright 依赖"""
    logger.info("检查 Playwright 依赖")
    try:
        async with async_playwright() as p:
            await p.chromium.launch()
    except Exception as e:
        raise ImportError("加载失败，Playwright 依赖不全，") from e


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
            await ctx.add_cookies(cookies) # type: ignore
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
