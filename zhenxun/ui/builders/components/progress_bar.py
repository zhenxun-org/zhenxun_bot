from typing import Literal

from ...models.components.progress_bar import ProgressBar
from ..base import BaseBuilder


class ProgressBarBuilder(BaseBuilder[ProgressBar]):
    """链式构建进度条组件的辅助类"""

    def __init__(
        self,
        progress: float,
        label: str | None = None,
        color_scheme: Literal[
            "primary", "success", "warning", "error", "info"
        ] = "primary",
        animated: bool = False,
    ):
        data_model = ProgressBar(
            progress=progress,
            label=label,
            color_scheme=color_scheme,
            animated=animated,
        )
        super().__init__(data_model, template_name="components/widgets/progress_bar")

    def set_label(self, label: str) -> "ProgressBarBuilder":
        """设置进度条上显示的文本。"""
        self._data.label = label
        return self

    def set_color_scheme(
        self, color_scheme: Literal["primary", "success", "warning", "error", "info"]
    ) -> "ProgressBarBuilder":
        """设置进度条的颜色方案。"""
        self._data.color_scheme = color_scheme
        return self

    def set_animated(self, animated: bool = True) -> "ProgressBarBuilder":
        """设置进度条是否显示动画效果。"""
        self._data.animated = animated
        return self
