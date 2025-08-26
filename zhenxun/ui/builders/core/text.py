from typing import Literal
from typing_extensions import Self

from ...models.core.text import TextData, TextSpan
from ..base import BaseBuilder


class TextBuilder(BaseBuilder[TextData]):
    """链式构建轻量级富文本组件的辅助类"""

    def __init__(self, text: str = ""):
        data_model = TextData(spans=[], align="left")
        super().__init__(data_model, template_name="components/core/text")
        if text:
            self.add_span(text)

    def set_alignment(self, align: Literal["left", "right", "center"]) -> Self:
        """设置整个文本块的对齐方式"""
        self._data.align = align
        return self

    def add_span(
        self,
        text: str,
        *,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
        strikethrough: bool = False,
        code: bool = False,
        color: str | None = None,
        font_size: str | int | None = None,
        font_family: str | None = None,
    ) -> Self:
        """
        添加一个带有样式的文本片段。

        参数:
            text: 文本内容。
            bold: 是否加粗。
            italic: 是否斜体。
            underline: 是否有下划线。
            strikethrough: 是否有删除线。
            code: 是否渲染为代码样式。
            color: 文本颜色 (e.g., '#ff0000', 'red')。
            font_size: 字体大小 (e.g., 16, '1.2em', '12px')。
            font_family: 字体族。
        """
        font_size_str = f"{font_size}px" if isinstance(font_size, int) else font_size
        span = TextSpan(
            text=text,
            bold=bold,
            italic=italic,
            underline=underline,
            strikethrough=strikethrough,
            code=code,
            color=color,
            font_size=font_size_str,
            font_family=font_family,
        )
        self._data.spans.append(span)
        return self
