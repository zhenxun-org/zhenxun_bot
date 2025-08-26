from abc import ABC, abstractmethod
from collections.abc import Awaitable, Iterable
from typing import Any

from pydantic import BaseModel

from zhenxun.services.renderer.protocols import Renderable
from zhenxun.utils.pydantic_compat import compat_computed_field, model_dump

__all__ = ["ContainerComponent", "RenderableComponent"]


class RenderableComponent(BaseModel, Renderable):
    """
    所有可渲染UI组件的数据模型基类。

    它继承自 Pydantic 的 `BaseModel` 用于数据校验和结构化，同时实现了 `Renderable`
    协议，确保其能够被 `RendererService` 正确处理。
    它还提供了一些所有组件通用的样式属性，如 `inline_style`, `variant` 等。
    """

    _is_standalone_template: bool = False
    inline_style: dict[str, str] | None = None
    component_css: str | None = None
    extra_classes: list[str] | None = None
    variant: str | None = None

    @property
    def template_name(self) -> str:
        """
        返回用于渲染此组件的Jinja2模板的路径。
        这是一个抽象属性，所有子类都必须覆盖它。
        """
        raise NotImplementedError(
            "Subclasses must implement the 'template_name' property."
        )

    async def prepare(self) -> None:
        """[可选] 生命周期钩子，默认无操作。"""
        pass

    def get_children(self) -> Iterable["RenderableComponent"]:
        """默认实现：非容器组件没有子组件。"""
        return []

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
        """[新增] 一个辅助属性，将内联样式字典转换为CSS字符串"""
        if not self.inline_style:
            return ""
        return "; ".join(f"{k}: {v}" for k, v in self.inline_style.items())

    def get_extra_css(self, context: Any) -> str | Awaitable[str]:
        return ""


class ContainerComponent(RenderableComponent, ABC):
    """
    一个为容器类组件设计的抽象基类，封装了预渲染子组件的通用逻辑。
    """

    @abstractmethod
    def get_children(self) -> Iterable[RenderableComponent]:
        """
        一个抽象方法，子类必须实现它来返回一个可迭代的子组件。
        """
        raise NotImplementedError

    def get_required_scripts(self) -> list[str]:
        """[新增] 聚合所有子组件的脚本依赖。"""
        scripts = set(super().get_required_scripts())
        for child in self.get_children():
            if child:
                scripts.update(child.get_required_scripts())
        return list(scripts)

    def get_required_styles(self) -> list[str]:
        """[新增] 聚合所有子组件的样式依赖。"""
        styles = set(super().get_required_styles())
        for child in self.get_children():
            if child:
                styles.update(child.get_required_styles())
        return list(styles)
