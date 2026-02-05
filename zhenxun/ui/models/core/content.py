from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Literal
from typing_extensions import Self

import aiofiles
from anyio import Path as AsyncPath
from pydantic import BaseModel, Field, PrivateAttr

from zhenxun.services.log import logger
from zhenxun.ui.models.components.feedback import ProgressBar

from .base import ContainerComponent, RenderableComponent

__all__ = [
    "BaseCell",
    "CodeElement",
    "ComponentCell",
    "ComponentElement",
    "DetailsData",
    "DetailsItem",
    "HeadingElement",
    "ImageCell",
    "ImageElement",
    "ListElement",
    "ListItemElement",
    "MarkdownData",
    "MarkdownElement",
    "ProgressBarCell",
    "QuoteElement",
    "RawHtmlElement",
    "RichTextCell",
    "StatusBadgeCell",
    "TableCell",
    "TableData",
    "TableElement",
    "TextCell",
    "TextData",
    "TextElement",
    "TextSpan",
]


class TextSpan(BaseModel):
    """单个富文本片段的数据模型"""

    text: str
    """文本内容"""
    bold: bool = False
    """是否加粗"""
    italic: bool = False
    """是否斜体"""
    underline: bool = False
    """是否下划线"""
    strikethrough: bool = False
    """是否删除线"""
    code: bool = False
    """是否为等宽代码样式"""
    color: str | None = None
    """文本颜色 (CSS color)"""
    font_size: str | None = None
    """字体大小 (CSS font-size)"""
    font_family: str | None = None
    """字体族 (CSS font-family)"""


class TextData(RenderableComponent):
    """轻量级富文本组件的数据模型"""

    spans: list[TextSpan] = Field(default_factory=list, description="文本片段列表")
    """文本片段列表"""
    align: Literal["left", "right", "center"] = Field(
        "left", description="整体文本对齐方式"
    )
    """整体文本对齐方式"""

    @property
    def template_name(self) -> str:
        return "components/core/text"

    def set_alignment(self, align: Literal["left", "right", "center"]) -> Self:
        self.align = align
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
        self.spans.append(span)
        return self


class DetailsItem(BaseModel):
    label: str = Field(..., description="项目的标签/键")
    """项目的标签/键"""
    value: Any = Field(..., description="项目的值")
    """项目的值"""


class DetailsData(RenderableComponent):
    """描述列表（键值对）的数据模型"""

    title: str | None = Field(None, description="列表的可选标题")
    """列表的可选标题"""
    items: list[DetailsItem] = Field(default_factory=list, description="键值对项目列表")
    """键值对项目列表"""

    @property
    def template_name(self) -> str:
        return "components/core/details"

    def add_item(self, label: str, value: Any) -> Self:
        self.items.append(DetailsItem(label=label, value=str(value)))
        return self


class MarkdownElement(BaseModel, ABC):
    @abstractmethod
    def to_markdown(self) -> str:
        pass


class TextElement(MarkdownElement):
    type: Literal["text"] = "text"
    """元素类型"""
    text: str
    """文本内容"""

    def to_markdown(self) -> str:
        return self.text


class HeadingElement(MarkdownElement):
    type: Literal["heading"] = "heading"
    """元素类型"""
    text: str
    """标题文本"""
    level: int = Field(..., ge=1, le=6)
    """标题级别 (1-6)"""

    def to_markdown(self) -> str:
        return f"{'#' * self.level} {self.text}"


class ImageElement(MarkdownElement):
    type: Literal["image"] = "image"
    src: str
    alt: str = "image"

    def to_markdown(self) -> str:
        return f"![{self.alt}]({self.src})"


class CodeElement(MarkdownElement):
    type: Literal["code"] = "code"
    code: str
    language: str = ""

    def to_markdown(self) -> str:
        return f"```{self.language}\n{self.code}\n```"


class RawHtmlElement(MarkdownElement):
    type: Literal["raw_html"] = "raw_html"
    html: str

    def to_markdown(self) -> str:
        return self.html


class TableElement(MarkdownElement):
    type: Literal["table"] = "table"
    headers: list[str]
    rows: list[list[str]]
    alignments: list[Literal["left", "center", "right"]] | None = None

    def to_markdown(self) -> str:
        header_row = "| " + " | ".join(self.headers) + " |"
        if self.alignments:
            align_map = {"left": ":---", "center": ":---:", "right": "---:"}
            separator_row = (
                "| "
                + " | ".join([align_map.get(a, "---") for a in self.alignments])
                + " |"
            )
        else:
            separator_row = "| " + " | ".join(["---"] * len(self.headers)) + " |"
        data_rows = "\n".join(
            "| " + " | ".join(map(str, row)) + " |" for row in self.rows
        )
        return f"{header_row}\n{separator_row}\n{data_rows}"


class ContainerElement(MarkdownElement):
    content: list[MarkdownElement] = Field(default_factory=list)


class QuoteElement(ContainerElement):
    type: Literal["quote"] = "quote"

    def to_markdown(self) -> str:
        inner_md = "\n".join(part.to_markdown() for part in self.content)
        return "\n".join([f"> {line}" for line in inner_md.split("\n")])


class ListItemElement(ContainerElement):
    def to_markdown(self) -> str:
        return "\n".join(part.to_markdown() for part in self.content)


class ListElement(ContainerElement):
    type: Literal["list"] = "list"
    ordered: bool = False

    def to_markdown(self) -> str:
        lines = []
        for i, item in enumerate(self.content):
            if isinstance(item, ListItemElement):
                prefix = f"{i + 1}." if self.ordered else "*"
                item_content = item.to_markdown()
                lines.append(f"{prefix} {item_content}")
        return "\n".join(lines)


class ComponentElement(MarkdownElement):
    type: Literal["component"] = "component"
    component: RenderableComponent

    def to_markdown(self) -> str:
        return ""


class MarkdownData(ContainerComponent):
    """
    Markdown组件数据模型。

    支持链式调用构建内容，例如:
    ui.markdown("").text("hello").code("print(1)")
    """

    style_name: str | None = None
    elements: list[MarkdownElement] = Field(default_factory=list)
    """Markdown元素列表"""
    width: int = 800
    """渲染区域宽度"""
    css_path: str | None = None
    """自定义CSS文件路径"""
    _context_stack: list[Any] = PrivateAttr(default_factory=list)

    @property
    def template_name(self) -> str:
        return "components/core/markdown"

    async def get_extra_css(self, context: Any) -> str:
        css_parts = []
        if self.component_css:
            css_parts.append(self.component_css)

        if self.css_path:
            css_file = Path(self.css_path)
            if await AsyncPath(css_file).is_file():
                async with aiofiles.open(css_file, encoding="utf-8") as f:
                    css_parts.append(await f.read())
            else:
                logger.warning(f"Markdown自定义CSS文件不存在: {self.css_path}")
        else:
            style_name = self.style_name or "light"
            css_path = await context.theme_manager.resolve_markdown_style_path(
                style_name, context
            )
            if css_path and css_path.exists():
                async with aiofiles.open(css_path, encoding="utf-8") as f:
                    css_parts.append(await f.read())

        return "\n".join(css_parts)

    def set_width(self, width: int) -> Self:
        self.width = width
        return self

    def set_css_path(self, css_path: str) -> Self:
        self.css_path = css_path
        return self

    def _append_element(self, element: MarkdownElement) -> Self:
        if self._context_stack:
            self._context_stack[-1].content.append(element)
        else:
            self.elements.append(element)
        return self

    def text(self, text: str) -> Self:
        return self._append_element(TextElement(text=text))

    def head(self, text: str, level: int = 1) -> Self:
        return self._append_element(HeadingElement(text=text, level=level))

    def image(self, content: str | Path, alt: str = "image") -> Self:
        src = ""
        if isinstance(content, Path):
            src = content.absolute().as_uri()
        elif content.startswith("base64://"):
            src = f"data:image/png;base64,{content.split('base64://', 1)[-1]}"
        else:
            src = content
        return self._append_element(ImageElement(src=src, alt=alt))

    def code(self, code: str, language: str = "") -> Self:
        return self._append_element(CodeElement(code=code, language=language))

    def table(
        self,
        headers: list[str],
        rows: list[list[str]],
        alignments: list[Any] | None = None,
    ) -> Self:
        return self._append_element(
            TableElement(headers=headers, rows=rows, alignments=alignments)
        )

    def add_divider(self) -> Self:
        return self._append_element(RawHtmlElement(html="---"))

    def add_component(self, component: "RenderableComponent") -> Self:
        return self._append_element(ComponentElement(component=component))

    class _ContextManager:
        def __init__(self, model: "MarkdownData", element: Any):
            self.model = model
            self.element = element

        def __enter__(self):
            self.model._context_stack.append(self.element)
            return self.model

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.model._context_stack.pop()

    def quote(self) -> AbstractContextManager["MarkdownData"]:
        element = QuoteElement()
        self._append_element(element)
        return self._ContextManager(self, element)

    def list(self, ordered: bool = False) -> AbstractContextManager["MarkdownData"]:
        element = ListElement(ordered=ordered)
        self._append_element(element)
        return self._ContextManager(self, element)

    def list_item(self) -> AbstractContextManager["MarkdownData"]:
        if not self._context_stack or not isinstance(
            self._context_stack[-1], ListElement
        ):
            raise TypeError("list_item() 只能在 list() 上下文中使用。")
        element = ListItemElement()
        self._context_stack[-1].content.append(element)
        return self._ContextManager(self, element)


class BaseCell(BaseModel):
    type: str


class TextCell(BaseCell):
    type: Literal["text"] = "text"  # type: ignore
    """单元格类型"""
    content: str
    """文本内容"""
    bold: bool = False
    """是否加粗"""
    color: str | None = None
    """文本颜色"""


class ImageCell(BaseCell):
    type: Literal["image"] = "image"  # type: ignore
    """单元格类型"""
    src: str
    """图片链接"""
    width: int = 40
    """显示宽度"""
    height: int = 40
    """显示高度"""
    shape: Literal["square", "circle"] = "square"
    """图片形状"""
    alt: str = "image"
    """替换文本"""


class StatusBadgeCell(BaseCell):
    type: Literal["badge"] = "badge"  # type: ignore
    """单元格类型"""
    text: str
    """徽章文本"""
    status_type: Literal["ok", "error", "warning", "info", "success"] = "info"
    """状态类型，决定颜色"""


class ProgressBarCell(BaseCell, ProgressBar):
    type: Literal["progress_bar"] = "progress_bar"  # type: ignore


class RichTextCell(BaseCell):
    type: Literal["rich_text"] = "rich_text"  # type: ignore
    """单元格类型"""
    spans: list[TextSpan] = Field(default_factory=list)
    """富文本片段列表"""
    direction: Literal["column", "row"] = Field("column")
    """排列方向"""
    gap: str = "4px"
    """项目间距"""


class ComponentCell(BaseCell):
    type: str = "component"
    component: RenderableComponent


TableCell = (
    TextCell
    | ImageCell
    | StatusBadgeCell
    | ProgressBarCell
    | RichTextCell
    | ComponentCell
    | str
    | int
    | float
    | None
)


class TableData(RenderableComponent):
    style_name: str | None = None
    title: str
    """表格标题"""
    tip: str | None = None
    """标题旁的提示文本"""
    headers: list[str] = Field(default_factory=list)
    """表格头字段列表"""
    rows: list[list[TableCell]] = Field(default_factory=list)
    """数据行列表"""
    column_alignments: list[Literal["left", "center", "right"]] | None = None
    """各列的对齐方式"""
    column_widths: list[str | int] | None = None
    """各列的宽度限制"""

    @property
    def template_name(self) -> str:
        return "components/core/table"

    def set_headers(self, headers: list[str]) -> Self:
        """设置表格标题行"""
        self.headers = headers
        return self

    def set_column_alignments(
        self, alignments: list[Literal["left", "center", "right"]]
    ) -> Self:
        """设置列对齐方式"""
        self.column_alignments = alignments
        return self

    def set_column_widths(self, widths: list[str | int]) -> Self:
        """设置列宽度"""
        self.column_widths = widths
        return self

    def _normalize_cell(self, cell_data: Any) -> BaseCell:
        """将任意数据标准化为 TableCell 类型"""
        if isinstance(cell_data, BaseCell):
            return cell_data
        if isinstance(cell_data, str | int | float):
            return TextCell(content=str(cell_data))
        if cell_data is None:
            return TextCell(content="")
        return TextCell(content=str(cell_data))

    def add_row(self, row: list[Any]) -> Self:
        """添加单行数据"""
        normalized_row = [self._normalize_cell(cell) for cell in row]
        self.rows.append(normalized_row)  # type: ignore
        return self

    def add_rows(self, rows: list[list[Any]]) -> Self:
        """批量添加多行数据"""
        for row in rows:
            self.add_row(row)
        return self
