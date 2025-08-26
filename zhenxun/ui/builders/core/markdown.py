from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from ...models.core.markdown import (
    CodeElement,
    ComponentElement,
    HeadingElement,
    ImageElement,
    ListElement,
    ListItemElement,
    MarkdownData,
    MarkdownElement,
    QuoteElement,
    RawHtmlElement,
    RenderableComponent,
    TableElement,
    TextElement,
)
from ..base import BaseBuilder

__all__ = ["MarkdownBuilder"]


class MarkdownBuilder(BaseBuilder[MarkdownData]):
    """链式构建Markdown图片的辅助类，支持上下文管理和组合。"""

    def __init__(self):
        data_model = MarkdownData(elements=[], width=800, css_path=None)
        super().__init__(data_model, template_name="components/core/markdown")
        self._parts: list[MarkdownElement] = []
        self._width: int = 800
        self._css_path: str | None = None
        self._context_stack: list[QuoteElement | ListElement | ListItemElement] = []

    def _append_element(self, element: MarkdownElement):
        """内部方法，根据上下文将元素添加到正确的位置。"""
        if self._context_stack:
            self._context_stack[-1].content.append(element)
        else:
            self._parts.append(element)
        return self

    def text(self, text: str) -> "MarkdownBuilder":
        """添加Markdown文本"""
        self._append_element(TextElement(text=text))
        return self

    def head(self, text: str, level: int = 1) -> "MarkdownBuilder":
        """添加Markdown标题"""
        self._append_element(HeadingElement(text=text, level=level))
        return self

    def image(self, content: str | Path, alt: str = "image") -> "MarkdownBuilder":
        """添加Markdown图片"""
        src = ""
        if isinstance(content, Path):
            src = content.absolute().as_uri()
        elif content.startswith("base64://"):
            src = f"data:image/png;base64,{content.split('base64://', 1)[-1]}"
        else:
            src = content
        self._append_element(ImageElement(src=src, alt=alt))
        return self

    def code(self, code: str, language: str = "") -> "MarkdownBuilder":
        """添加Markdown代码块"""
        self._append_element(CodeElement(code=code, language=language))
        return self

    def table(
        self,
        headers: list[str],
        rows: list[list[str]],
        alignments: list[Any] | None = None,
    ) -> "MarkdownBuilder":
        """添加Markdown表格"""
        self._append_element(
            TableElement(headers=headers, rows=rows, alignments=alignments)
        )
        return self

    def add_component(
        self, component: "BaseBuilder | RenderableComponent"
    ) -> "MarkdownBuilder":
        """添加一个UI组件（如图表、卡片等）。"""
        component_data = (
            component.build() if isinstance(component, BaseBuilder) else component
        )
        self._append_element(ComponentElement(component=component_data))
        return self

    def add_builder(self, builder: "MarkdownBuilder") -> "MarkdownBuilder":
        """将另一个builder的内容组合进来。"""
        if self._context_stack:
            self._context_stack[-1].content.extend(builder._parts)
        else:
            self._parts.extend(builder._parts)
        return self

    def quote(self) -> AbstractContextManager["MarkdownBuilder"]:
        """创建一个引用块上下文。"""
        return self._context_for(QuoteElement())

    def list(self, ordered: bool = False) -> AbstractContextManager["MarkdownBuilder"]:
        """创建一个列表上下文。"""
        return self._context_for(ListElement(ordered=ordered))

    def list_item(self) -> AbstractContextManager["MarkdownBuilder"]:
        """在列表上下文中创建一个列表项。"""
        if not self._context_stack or not isinstance(
            self._context_stack[-1], ListElement
        ):
            raise TypeError("list_item() 只能在 list() 上下文中使用。")
        return self._context_for(ListItemElement())

    class _ContextManager:
        def __init__(
            self,
            builder: "MarkdownBuilder",
            element: QuoteElement | ListElement | ListItemElement,
        ):
            self.builder = builder
            self.element = element

        def __enter__(self):
            self.builder._context_stack.append(self.element)
            return self.builder

        def __exit__(self, exc_type, exc_val, exc_tb):
            del exc_type, exc_val, exc_tb
            self.builder._context_stack.pop()

    def _context_for(
        self, element: QuoteElement | ListElement | ListItemElement
    ) -> AbstractContextManager["MarkdownBuilder"]:
        self._append_element(element)
        return self._ContextManager(self, element)

    def set_width(self, width: int) -> "MarkdownBuilder":
        """设置图片宽度"""
        self._width = width
        return self

    def set_css_path(self, css_path: str) -> "MarkdownBuilder":
        """设置CSS样式路径"""
        self._css_path = css_path
        return self

    def add_divider(self) -> "MarkdownBuilder":
        """添加一条标准的 Markdown 分割线。"""
        self._append_element(RawHtmlElement(html="---"))
        return self

    def build(self) -> MarkdownData:
        """
        构建并返回 MarkdownData 模型实例。
        """
        self._data.elements = self._parts
        self._data.width = self._width
        self._data.css_path = self._css_path
        return super().build()
