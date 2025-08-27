from typing import Any

from pydantic import BaseModel, Field

from .base import RenderableComponent


class DetailsItem(BaseModel):
    """描述列表中的单个项目"""

    label: str = Field(..., description="项目的标签/键")
    value: Any = Field(..., description="项目的值")


class DetailsData(RenderableComponent):
    """描述列表（键值对）的数据模型"""

    title: str | None = Field(None, description="列表的可选标题")
    items: list[DetailsItem] = Field(default_factory=list, description="键值对项目列表")

    @property
    def template_name(self) -> str:
        return "components/core/details"
