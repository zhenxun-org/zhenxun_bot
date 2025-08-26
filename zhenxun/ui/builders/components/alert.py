from typing import Literal
from typing_extensions import Self

from ...models.components.alert import Alert
from ..base import BaseBuilder


class AlertBuilder(BaseBuilder[Alert]):
    """链式构建提示/标注框组件的辅助类"""

    def __init__(
        self,
        title: str,
        content: str,
        type: Literal["info", "success", "warning", "error"] = "info",
    ):
        data_model = Alert(title=title, content=content, type=type)
        super().__init__(data_model, template_name="components/widgets/alert")

    def hide_icon(self) -> Self:
        """隐藏提示框的默认图标"""
        self._data.show_icon = False
        return self
