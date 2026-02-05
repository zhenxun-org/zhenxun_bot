import builtins
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal
from typing_extensions import Self

from pydantic import BaseModel, Field

from ...registry import component
from .base import ContainerComponent, Renderable, RenderableComponent

__all__ = [
    "CardData",
    "LayoutData",
    "LayoutItem",
    "ListData",
    "ListItem",
    "NotebookData",
    "NotebookElement",
    "TemplateComponent",
]


class TemplateComponent(RenderableComponent):
    """基于独立模板文件的UI组件"""

    _is_standalone_template: bool = True
    template_path: str | Path = Field(..., description="指向HTML模板文件的路径")  # type: ignore
    """指向HTML模板文件的路径"""
    data: dict[str, Any] = Field(..., description="传递给模板的上下文数据字典")
    """传递给模板的上下文数据字典"""

    @property
    def template_name(self) -> str:
        if isinstance(self.template_path, Path):
            return self.template_path.as_posix()
        return str(self.template_path)

    def get_render_data(self) -> dict[str, Any]:
        return self.data

    def __getattr__(self, name: str) -> Any:
        try:
            return self.data[name]
        except KeyError:
            raise AttributeError(
                f"'{type(self).__name__}' 对象没有属性 '{name}'"
            ) from None


@component(name="card", namespace="core")
class CardData(ContainerComponent):
    """通用卡片的数据模型，可以包含头部、内容和尾部"""

    header: RenderableComponent | None = None
    content: RenderableComponent
    footer: RenderableComponent | None = None

    @property
    def template_name(self) -> str:
        return "components/core/card"

    def get_children(self) -> Iterable["Renderable"]:
        if self.header:
            yield self.header
        if self.content:
            yield self.content
        if self.footer:
            yield self.footer

    def set_header(self, header: "RenderableComponent") -> Self:
        self.header = header
        return self

    def set_footer(self, footer: "RenderableComponent") -> Self:
        self.footer = footer
        return self


class LayoutItem(BaseModel):
    """布局中的单个项目"""

    component: RenderableComponent = Field(..., description="要渲染的组件的数据模型")
    """要渲染的组件的数据模型"""
    metadata: dict[str, Any] | None = Field(None, description="传递给模板的额外元数据")
    """传递给模板的额外元数据"""


class LayoutData(ContainerComponent):
    """布局构建器的数据模型"""

    style_name: str | None = None
    layout_type: str = "column"
    children: list[LayoutItem] = Field(
        default_factory=list, description="要布局的项目列表"
    )
    """要布局的项目列表"""
    options: dict[str, Any] = Field(
        default_factory=dict, description="传递给模板的选项"
    )
    """传递给模板的选项"""

    @property
    def template_name(self) -> str:
        return f"components/core/layouts/{self.layout_type}"

    @classmethod
    def column(
        cls, *, gap: str = "20px", align_items: str = "stretch", **options: Any
    ) -> Self:
        options.update({"gap": gap, "align_items": align_items})
        return cls(layout_type="column", options=options)

    @classmethod
    def row(
        cls, *, gap: str = "10px", align_items: str = "center", **options: Any
    ) -> Self:
        options.update({"gap": gap, "align_items": align_items})
        return cls(layout_type="row", options=options)

    @classmethod
    def grid(cls, columns: int = 2, **options: Any) -> Self:
        options.update({"columns": columns})
        return cls(layout_type="grid", options=options)

    def add_item(
        self,
        component: "RenderableComponent",
        metadata: dict[str, Any] | None = None,
    ) -> Self:
        self.children.append(LayoutItem(component=component, metadata=metadata))
        return self

    def add_option(self, key: str, value: Any) -> Self:
        self.options[key] = value
        return self

    def get_children(self) -> Iterable["Renderable"]:
        for item in self.children:
            if item.component:
                yield item.component

    def get_extra_css(self, context: Any) -> str:
        all_css = []
        if self.component_css:
            all_css.append(self.component_css)
        for item in self.children:
            if (
                item.component
                and hasattr(item.component, "component_css")
                and item.component.component_css
            ):
                all_css.append(item.component.component_css)
        return "\n".join(all_css)


class ListItem(BaseModel):
    """列表中的单个项目"""

    component: RenderableComponent = Field(..., description="要渲染的组件的数据模型")
    """要渲染的组件的数据模型"""


class ListData(ContainerComponent):
    """通用列表的数据模型"""

    component_type: Literal["list"] = "list"
    """组件类型"""
    items: list[ListItem] = Field(default_factory=list, description="列表项目")
    """列表项目"""
    ordered: bool = Field(default=False, description="是否为有序列表")
    """是否为有序列表"""

    @property
    def template_name(self) -> str:
        return "components/core/list"

    def get_children(self) -> Iterable["Renderable"]:
        for item in self.items:
            if item.component:
                yield item.component

    def add_item(self, component: "RenderableComponent") -> Self:
        self.items.append(ListItem(component=component))
        return self

    def set_ordered(self, ordered: bool = True) -> Self:
        self.ordered = ordered
        return self


class NotebookElement(BaseModel):
    """一个 Notebook 页面中的单个元素"""

    type: Literal[
        "heading",
        "paragraph",
        "image",
        "blockquote",
        "code",
        "list",
        "divider",
        "component",
    ]
    """元素类型"""
    text: str | None = None
    """文本内容"""
    level: int | None = None
    """标题级别"""
    src: str | None = None
    """图片链接"""
    caption: str | None = None
    """图片说明"""
    code: str | None = None
    """代码块内容"""
    language: str | None = None
    """代码语言"""
    data: list[str] | None = None
    """列表数据"""
    ordered: bool | None = None
    """是否为有序列表"""
    component: RenderableComponent | None = None
    """可渲染组件"""


class NotebookData(ContainerComponent):
    """Notebook转图片的数据模型"""

    style_name: str | None = None
    elements: list[NotebookElement]

    @property
    def template_name(self) -> str:
        return "components/core/notebook"

    def get_children(self) -> Iterable["Renderable"]:
        for element in self.elements:
            if element.component:
                yield element.component

    def text(self, text: str) -> Self:
        self.elements.append(NotebookElement(type="paragraph", text=text))
        return self

    def head(self, text: str, level: int = 1) -> Self:
        if not 1 <= level <= 4:
            raise ValueError("标题级别必须在1-4之间")
        self.elements.append(NotebookElement(type="heading", text=text, level=level))
        return self

    def image(self, content: str | Path, caption: str | None = None) -> Self:
        src = ""
        if isinstance(content, Path):
            src = content.absolute().as_uri()
        elif content.startswith("base64"):
            src = f"data:image/png;base64,{content.split('base64://', 1)[-1]}"
        else:
            src = content
        self.elements.append(NotebookElement(type="image", src=src, caption=caption))
        return self

    def quote(self, text: str | list[str]) -> Self:
        if isinstance(text, str):
            self.elements.append(NotebookElement(type="blockquote", text=text))
        elif isinstance(text, list):
            for t in text:
                self.elements.append(NotebookElement(type="blockquote", text=t))
        return self

    def code(self, code: str, language: str = "python") -> Self:
        self.elements.append(NotebookElement(type="code", code=code, language=language))
        return self

    def list(self, items: list[str], ordered: bool = False) -> Self:
        self.elements.append(NotebookElement(type="list", data=items, ordered=ordered))
        return self

    def add_divider(self) -> Self:
        self.elements.append(NotebookElement(type="divider"))
        return self

    def add_component(self, component: "RenderableComponent") -> Self:
        self.elements.append(NotebookElement(type="component", component=component))
        return self

    def add_texts(self, texts: builtins.list[str]) -> Self:
        for t in texts:
            self.text(t)
        return self

    def add_quotes(self, quotes: builtins.list[str]) -> Self:
        for q in quotes:
            self.quote(q)
        return self
