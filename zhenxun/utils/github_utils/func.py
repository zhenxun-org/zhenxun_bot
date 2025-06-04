from aiocache import cached

from zhenxun.utils.http_utils import AsyncHttpx

from .const import (
    ARCHIVE_URL_FORMAT,
    RAW_CONTENT_FORMAT,
    RELEASE_ASSETS_FORMAT,
    RELEASE_SOURCE_FORMAT,
)


async def __get_fastest_formats(formats: dict[str, str]) -> list[str]:
    sorted_urls = await AsyncHttpx.get_fastest_mirror(list(formats.keys()))
    if not sorted_urls:
        raise Exception("无法获取任意GitHub资源加速地址，请检查网络")
    return [formats[url] for url in sorted_urls]


@cached()
async def get_fastest_raw_formats() -> list[str]:
    """获取最快的raw下载地址格式"""
    formats: dict[str, str] = {
        "https://raw.githubusercontent.com/": RAW_CONTENT_FORMAT,
        "https://ghproxy.cc/": f"https://ghproxy.cc/{RAW_CONTENT_FORMAT}",
        "https://gh-proxy.com/": f"https://gh-proxy.com/{RAW_CONTENT_FORMAT}",
        "https://cdn.jsdelivr.net/": "https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}",
    }
    return await __get_fastest_formats(formats)


@cached()
async def get_fastest_archive_formats() -> list[str]:
    """获取最快的归档下载地址格式"""
    formats: dict[str, str] = {
        "https://github.com/": ARCHIVE_URL_FORMAT,
        "https://ghproxy.cc/": f"https://ghproxy.cc/{ARCHIVE_URL_FORMAT}",
        "https://gh-proxy.com/": f"https://gh-proxy.com/{ARCHIVE_URL_FORMAT}",
    }
    return await __get_fastest_formats(formats)


@cached()
async def get_fastest_release_formats() -> list[str]:
    """获取最快的发行版资源下载地址格式"""
    formats: dict[str, str] = {
        "https://objects.githubusercontent.com/": RELEASE_ASSETS_FORMAT,
        "https://ghproxy.cc/": f"https://ghproxy.cc/{RELEASE_ASSETS_FORMAT}",
        "https://gh-proxy.com/": f"https://gh-proxy.com/{RELEASE_ASSETS_FORMAT}",
    }
    return await __get_fastest_formats(formats)


@cached()
async def get_fastest_release_source_formats() -> list[str]:
    """获取最快的发行版源码下载地址格式"""
    formats: dict[str, str] = {
        "https://codeload.github.com/": RELEASE_SOURCE_FORMAT,
        "https://p.102333.xyz/": f"https://p.102333.xyz/{RELEASE_SOURCE_FORMAT}",
    }
    return await __get_fastest_formats(formats)
