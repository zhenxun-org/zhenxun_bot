from typing import Literal

from pydantic import BaseModel, Field

from ..core.base import RenderableComponent

__all__ = ["Timeline", "TimelineItem"]


class TimelineItem(BaseModel):
    """时间轴中的单个事件点。"""

    timestamp: str = Field(..., description="显示在时间点旁边的时间或标签")
    title: str = Field(..., description="事件的标题")
    content: str = Field(..., description="事件的详细描述")
    icon: str | None = Field(default=None, description="可选的自定义图标SVG路径")
    color: str | None = Field(default=None, description="可选的自定义颜色，覆盖默认")


class Timeline(RenderableComponent):
    """一个垂直时间轴组件，用于按顺序展示事件。"""

    component_type: Literal["timeline"] = "timeline"
    items: list[TimelineItem] = Field(
        default_factory=list, description="时间轴项目列表"
    )

    @property
    def template_name(self) -> str:
        return "components/widgets/timeline"
