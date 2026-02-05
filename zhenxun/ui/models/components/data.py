from typing import Any, Literal

from pydantic import BaseModel, Field

from ..core.base import RenderableComponent

__all__ = ["KpiCard", "Timeline", "TimelineItem"]


class KpiCard(RenderableComponent):
    """一个用于展示关键性能指标（KPI）的统计卡片。"""

    component_type: Literal["kpi_card"] = "kpi_card"
    """组件类型"""
    label: str = Field(..., description="指标的标签或名称")
    """指标的标签或名称"""
    value: Any = Field(..., description="指标的主要数值")
    """指标的主要数值"""
    unit: str | None = Field(default=None, description="数值的单位，可选")
    """数值的单位，可选"""
    change: str | None = Field(
        default=None, description="与上一周期的变化，例如 '+15%' 或 '-100'"
    )
    """与上一周期的变化，例如 '+15%' 或 '-100'"""
    change_type: Literal["positive", "negative", "neutral"] = Field(
        default="neutral", description="变化的类型，用于决定颜色"
    )
    """变化的类型，用于决定颜色"""
    icon_svg: str | None = Field(
        default=None, description="卡片中显示的可选图标 (SVG path data)"
    )
    """卡片中显示的可选图标 (SVG path data)"""

    @property
    def template_name(self) -> str:
        return "components/widgets/kpi_card"


class TimelineItem(BaseModel):
    """时间轴中的单个事件点。"""

    timestamp: str = Field(..., description="显示在时间点旁边的时间或标签")
    """显示在时间点旁边的时间或标签"""
    title: str = Field(..., description="事件的标题")
    """事件的标题"""
    content: str = Field(..., description="事件的详细描述")
    """事件的详细描述"""
    icon: str | None = Field(default=None, description="可选的自定义图标SVG路径")
    """可选的自定义图标SVG路径"""
    color: str | None = Field(default=None, description="可选的自定义颜色，覆盖默认")
    """可选的自定义颜色，覆盖默认"""


class Timeline(RenderableComponent):
    """一个垂直时间轴组件，用于按顺序展示事件。"""

    component_type: Literal["timeline"] = "timeline"
    """组件类型"""
    items: list[TimelineItem] = Field(
        default_factory=list, description="时间轴项目列表"
    )
    """时间轴项目列表"""

    @property
    def template_name(self) -> str:
        return "components/widgets/timeline"
