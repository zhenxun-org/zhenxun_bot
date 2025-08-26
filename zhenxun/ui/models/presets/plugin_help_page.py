from pydantic import BaseModel

from ..core.base import RenderableComponent

__all__ = [
    "HelpCategory",
    "HelpItem",
    "PluginHelpPageData",
]


class HelpItem(BaseModel):
    """帮助菜单中的单个功能项"""

    name: str
    description: str
    usage: str


class HelpCategory(BaseModel):
    """帮助菜单中的一个功能类别"""

    title: str
    icon_svg_path: str
    items: list[HelpItem]


class PluginHelpPageData(RenderableComponent):
    """通用插件帮助页面的数据模型"""

    style_name: str | None = None
    bot_nickname: str
    page_title: str
    categories: list[HelpCategory]

    @property
    def template_name(self) -> str:
        return "pages/core/plugin_help_page"
