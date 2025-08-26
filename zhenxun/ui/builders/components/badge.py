from typing import Literal

from ...models.components.badge import Badge
from ..base import BaseBuilder


class BadgeBuilder(BaseBuilder[Badge]):
    """链式构建徽章组件的辅助类"""

    def __init__(
        self,
        text: str,
        color_scheme: Literal[
            "primary", "success", "warning", "error", "info"
        ] = "info",
    ):
        data_model = Badge(text=text, color_scheme=color_scheme)
        super().__init__(data_model, template_name="components/widgets/badge")

    def set_color_scheme(
        self, color_scheme: Literal["primary", "success", "warning", "error", "info"]
    ) -> "BadgeBuilder":
        """设置徽章的颜色方案。"""
        self._data.color_scheme = color_scheme
        return self
