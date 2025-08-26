from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field

from .base import ContainerComponent, RenderableComponent

__all__ = ["ListData", "ListItem"]


class ListItem(BaseModel):
    """列表中的单个项目，其内容可以是任何可渲染组件。"""

    component: RenderableComponent = Field(..., description="要渲染的组件的数据模型")


class ListData(ContainerComponent):
    """通用列表的数据模型，支持有序和无序列表。"""

    component_type: Literal["list"] = "list"
    items: list[ListItem] = Field(default_factory=list, description="列表项目")
    ordered: bool = Field(default=False, description="是否为有序列表")

    @property
    def template_name(self) -> str:
        return "components/core/list"

    def get_children(self) -> Iterable[RenderableComponent]:
        for item in self.items:
            yield item.component
