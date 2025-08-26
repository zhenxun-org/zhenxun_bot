from typing import Any, Literal

from pydantic import Field

from ..core.base import RenderableComponent

__all__ = ["KpiCard"]


class KpiCard(RenderableComponent):
    """一个用于展示关键性能指标（KPI）的统计卡片。"""

    component_type: Literal["kpi_card"] = "kpi_card"
    label: str = Field(..., description="指标的标签或名称")
    value: Any = Field(..., description="指标的主要数值")
    unit: str | None = Field(default=None, description="数值的单位，可选")
    change: str | None = Field(
        default=None, description="与上一周期的变化，例如 '+15%' 或 '-100'"
    )
    change_type: Literal["positive", "negative", "neutral"] = Field(
        default="neutral", description="变化的类型，用于决定颜色"
    )
    icon_svg: str | None = Field(
        default=None, description="卡片中显示的可选图标 (SVG path data)"
    )

    @property
    def template_name(self) -> str:
        return "components/widgets/kpi_card"
