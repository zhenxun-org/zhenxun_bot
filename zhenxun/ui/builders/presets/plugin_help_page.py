from ...models.presets.plugin_help_page import (
    HelpCategory,
    PluginHelpPageData,
)
from ..base import BaseBuilder


class PluginHelpPageBuilder(BaseBuilder[PluginHelpPageData]):
    """链式构建插件帮助页面的辅助类"""

    def __init__(self, bot_nickname: str, page_title: str):
        self._data = PluginHelpPageData(
            bot_nickname=bot_nickname, page_title=page_title, categories=[]
        )

        super().__init__(self._data, template_name="pages/core/plugin_help_page")

    def add_category(self, category: HelpCategory) -> "PluginHelpPageBuilder":
        """添加一个帮助分类"""
        self._data.categories.append(category)
        return self

    def add_categories(self, categories: list[HelpCategory]) -> "PluginHelpPageBuilder":
        """批量添加帮助分类"""
        for category in categories:
            self.add_category(category)
        return self
