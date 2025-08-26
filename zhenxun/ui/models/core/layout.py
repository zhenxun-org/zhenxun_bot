from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from .base import ContainerComponent, RenderableComponent

__all__ = ["LayoutData", "LayoutItem"]


class LayoutItem(BaseModel):
    """布局中的单个项目，现在持有可渲染组件的数据模型"""

    component: RenderableComponent = Field(..., description="要渲染的组件的数据模型")
    metadata: dict[str, Any] | None = Field(None, description="传递给模板的额外元数据")


class LayoutData(ContainerComponent):
    """布局构建器的数据模型"""

    style_name: str | None = None
    layout_type: str = "column"
    children: list[LayoutItem] = Field(
        default_factory=list, description="要布局的项目列表"
    )
    options: dict[str, Any] = Field(
        default_factory=dict, description="传递给模板的选项"
    )

    @property
    def template_name(self) -> str:
        return f"components/core/layouts/{self.layout_type}"

    def get_extra_css(self, context: Any) -> str:
        """聚合所有子组件的 extra_css。"""
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

    def get_children(self) -> Iterable[RenderableComponent]:
        for item in self.children:
            yield item.component
