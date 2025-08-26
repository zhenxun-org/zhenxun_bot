# File: zhenxun/services/renderer/registry.py

from pathlib import Path
from typing import ClassVar

from zhenxun.services.log import logger


class AssetRegistry:
    """一个独立的、用于存储由插件动态注册的资源的单例服务。"""

    _markdown_styles: ClassVar[dict[str, Path]] = {}

    def register_markdown_style(self, name: str, path: Path):
        """
        为 Markdown 渲染器注册一个具名样式。

        参数:
            name (str): 样式的唯一名称。
            path (Path): 指向该样式的CSS文件路径。
        """
        if name in self._markdown_styles:
            logger.warning(f"Markdown 样式 '{name}' 已被注册，将被覆盖。")
        self._markdown_styles[name] = path
        logger.debug(f"已注册 Markdown 样式 '{name}' -> '{path}'")

    def resolve_markdown_style(self, name: str) -> Path | None:
        """解析已注册的 Markdown 样式。"""
        return self._markdown_styles.get(name)


asset_registry = AssetRegistry()
