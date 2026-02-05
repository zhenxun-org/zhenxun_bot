from abc import ABC
from collections.abc import Awaitable, Iterable
from typing import Any
from typing_extensions import Self

from pydantic import VERSION as PYDANTIC_VERSION
from pydantic import BaseModel, Field

from zhenxun.services.renderer.types import Renderable
from zhenxun.utils.pydantic_compat import compat_computed_field, model_dump

__all__ = ["ContainerComponent", "RenderableComponent"]


def _iter_renderables(obj: Any) -> Iterable["Renderable"]:
    """
    递归遍历对象，查找所有 Renderable 实例。
    支持列表、字典以及嵌套的 Pydantic 模型。
    """
    if isinstance(obj, Renderable):
        yield obj
    elif isinstance(obj, list | tuple):
        for item in obj:
            yield from _iter_renderables(item)
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _iter_renderables(value)
    elif isinstance(obj, BaseModel):
        if PYDANTIC_VERSION.startswith("1"):
            fields = obj.__fields__
        else:
            fields = obj.model_fields  # type: ignore

        for field_name in fields:
            value = getattr(obj, field_name)
            yield from _iter_renderables(value)


class RenderableComponent(BaseModel, Renderable):
    """
    所有可渲染UI组件的数据模型基类。
    提供通用的样式属性（如内联样式、CSS类、变体）和链式调用方法。
    """

    _is_standalone_template: bool = False
    """标记此组件是否为独立模板"""
    inline_style: dict[str, str] | None = None
    """应用于组件根元素的内联CSS样式"""
    component_css: str | None = None
    """注入到页面的额外CSS字符串"""
    extra_classes: list[str] | None = None
    """应用于组件根元素的额外CSS类名列表"""
    variant: str | None = None
    """组件的变体/皮肤名称"""
    style_name: str | None = None
    """组件的样式名称"""
    is_page: bool = False
    """标记此组件是否为完整页面(自带html/body), 渲染时将跳过通用包装器"""
    template_path: str | None = Field(
        default=None, description="动态覆盖的模板路径", exclude=True
    )
    """动态覆盖的模板路径，若设置则优先于 template_name 属性"""

    @property
    def template_name(self) -> str:
        """
        返回用于渲染此组件的 Jinja2 模板路径。
        """
        return ""

    def with_style(self, style_name: str) -> Self:
        """
        设置组件样式名称。

        参数:
            style_name: 样式名称，通常对应主题中的一组CSS定义
        """
        self.style_name = style_name
        return self

    def with_variant(self, variant: str) -> Self:
        """
        设置组件变体（皮肤）。

        参数:
            variant: 变体名称，用于加载不同的模板或样式集
        """
        self.variant = variant
        return self

    def with_classes(self, *classes: str) -> Self:
        """
        添加 CSS 类名。

        参数:
            *classes: 一个或多个 CSS 类名
        """
        if self.extra_classes is None:
            self.extra_classes = []
        self.extra_classes.extend(classes)
        return self

    def with_inline_style(self, style: dict[str, str]) -> Self:
        """
        设置内联 CSS 样式。

        参数:
            style: 样式键值对字典 (e.g. {'color': 'red'})
        """
        if self.inline_style is None:
            self.inline_style = {}
        self.inline_style.update(style)
        return self

    def with_component_css(self, css: str) -> Self:
        """
        注入自定义 CSS 代码块。

        参数:
            css: CSS 代码字符串
        """
        self.component_css = css
        return self

    def update(self, **kwargs) -> Self:
        """批量更新组件属性。"""
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        return self

    def build(self) -> Self:
        """
        返回组件自身（兼容 Builder 模式调用）。
        """
        return self

    async def prepare(self) -> None:
        """[生命周期] 渲染前的异步准备步骤。"""
        pass

    def get_children(self) -> Iterable["Renderable"]:
        """获取所有子组件的迭代器。"""
        if PYDANTIC_VERSION.startswith("1"):
            fields = self.__fields__
        else:
            fields = self.model_fields  # type: ignore

        for field_name in fields:
            value = getattr(self, field_name)
            yield from _iter_renderables(value)

    def get_required_scripts(self) -> list[str]:
        """[可选] 返回此组件所需的JS脚本路径列表 (相对于assets目录)。"""
        return []

    def get_required_styles(self) -> list[str]:
        """[可选] 返回此组件所需的CSS样式表路径列表 (相对于assets目录)。"""
        return []

    def get_render_data(self) -> dict[str, Any | Awaitable[Any]]:
        """默认实现，返回模型自身的数据字典。"""
        return model_dump(
            self, exclude={"inline_style", "component_css", "inline_style_str"}
        )

    @compat_computed_field
    def inline_style_str(self) -> str:
        """一个辅助属性，将内联样式字典转换为CSS字符串"""
        if not self.inline_style:
            return ""
        return "; ".join(f"{k}: {v}" for k, v in self.inline_style.items())

    def get_extra_css(self, context: Any) -> str | Awaitable[str]:
        return self.component_css or ""


class ContainerComponent(RenderableComponent, ABC):
    """
    一个为容器类组件设计的抽象基类，封装了预渲染子组件的通用逻辑。
    """

    def get_required_scripts(self) -> list[str]:
        """聚合所有子组件的脚本依赖。"""
        scripts = set(super().get_required_scripts())
        for child in self.get_children():
            if child:
                scripts.update(child.get_required_scripts())
        return list(scripts)

    def get_required_styles(self) -> list[str]:
        """聚合所有子组件的样式依赖。"""
        styles = set(super().get_required_styles())
        for child in self.get_children():
            if child:
                styles.update(child.get_required_styles())
        return list(styles)
