from typing import Any, Literal
from typing_extensions import Self

from ...models.components.kpi_card import KpiCard
from ..base import BaseBuilder


class KpiCardBuilder(BaseBuilder[KpiCard]):
    """链式构建统计卡片（KPI Card）的辅助类"""

    def __init__(self, label: str, value: Any):
        data_model = KpiCard(label=label, value=value)
        super().__init__(data_model, template_name="components/widgets/kpi_card")

    def with_unit(self, unit: str) -> Self:
        """设置数值的单位"""
        self._data.unit = unit
        return self

    def with_change(
        self, change: str, type: Literal["positive", "negative", "neutral"] = "neutral"
    ) -> Self:
        """设置与上一周期的变化率"""
        self._data.change = change
        self._data.change_type = type
        return self

    def with_icon(self, svg_path: str) -> Self:
        """设置卡片图标 (提供SVG path data)"""
        self._data.icon_svg = svg_path
        return self
