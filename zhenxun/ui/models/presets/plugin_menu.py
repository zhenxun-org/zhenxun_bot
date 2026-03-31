from pydantic import BaseModel, Field

from ..core.base import RenderableComponent

__all__ = [
    "PluginMenuCategory",
    "PluginMenuData",
    "PluginMenuItem",
]


class PluginMenuItem(BaseModel):
    """插件菜单中的单个插件项"""

    id: str
    """插件的唯一ID"""
    name: str
    """插件名称"""
    status: int
    """插件状态码: 0:开启, 1:群关, 2:系统关, 3:全局关"""
    has_superuser_help: bool
    """插件是否有超级用户专属帮助"""
    commands: list[str] = Field(default_factory=list, description="插件的主要命令列表")
    """插件的主要命令列表"""


class PluginMenuCategory(BaseModel):
    """插件菜单中的一个分类"""

    name: str
    """插件分类名称"""
    items: list[PluginMenuItem] = Field(..., description="该分类下的插件项列表")
    """该分类下的插件项列表"""


class PluginMenuData(RenderableComponent):
    """通用插件帮助菜单的数据模型"""

    style_name: str | None = None
    """页面样式名称"""
    bot_name: str
    """机器人名称"""
    bot_avatar_url: str
    """机器人头像URL"""
    is_detail: bool
    """是否为详细菜单模式"""
    plugin_count: int
    """总插件数量"""
    active_count: int
    """已启用插件数量"""
    categories: list[PluginMenuCategory]
    """插件分类列表"""

    @property
    def template_name(self) -> str:
        return "pages/core/plugin_menu"
