from typing import Any, Generic, Literal, TypeVar
from typing_extensions import Self

from ..models.charts import (
    BaseChartData,
    EChartsAxis,
    EChartsData,
    EChartsGrid,
    EChartsSeries,
    EChartsTitle,
    EChartsTooltip,
)
from .base import BaseBuilder

T_ChartData = TypeVar("T_ChartData", bound=BaseChartData)


class EChartsBuilder(BaseBuilder[EChartsData], Generic[T_ChartData]):
    """
    一个统一的、泛型的 ECharts 图表构建器。
    提供了设置 ECharts `option` 的核心方法，以及一些常用图表的便利方法。
    """

    def __init__(self, template_name: str, title: str):
        model = EChartsData(
            template_path=template_name,
            title=EChartsTitle(text=title),
            grid=None,
            tooltip=None,
            xAxis=None,
            yAxis=None,
            legend=None,
            background_image=None,
        )
        super().__init__(model, template_name=template_name)

    def set_title(
        self, text: str, left: Literal["left", "center", "right"] = "center"
    ) -> Self:
        self._data.title_model = EChartsTitle(text=text, left=left)
        return self

    def set_grid(
        self,
        left: str | None = None,
        right: str | None = None,
        top: str | None = None,
        bottom: str | None = None,
        containLabel: bool = True,
    ) -> Self:
        self._data.grid_model = EChartsGrid(
            left=left, right=right, top=top, bottom=bottom, containLabel=containLabel
        )
        return self

    def set_tooltip(self, trigger: Literal["item", "axis", "none"]) -> Self:
        self._data.tooltip_model = EChartsTooltip(trigger=trigger)
        return self

    def set_x_axis(
        self,
        type: Literal["category", "value", "time", "log"],
        data: list[Any] | None = None,
        show: bool = True,
    ) -> Self:
        self._data.x_axis_model = EChartsAxis(type=type, data=data, show=show)
        return self

    def set_y_axis(
        self,
        type: Literal["category", "value", "time", "log"],
        data: list[Any] | None = None,
        show: bool = True,
    ) -> Self:
        self._data.y_axis_model = EChartsAxis(type=type, data=data, show=show)
        return self

    def add_series(
        self, type: str, data: list[Any], name: str | None = None, **kwargs: Any
    ) -> Self:
        series = EChartsSeries(type=type, data=data, name=name, **kwargs)
        self._data.series_models.append(series)
        return self

    def set_legend(
        self,
        data: list[str],
        orient: Literal["horizontal", "vertical"] = "horizontal",
        left: str = "auto",
    ) -> Self:
        self._data.legend_model = {"data": data, "orient": orient, "left": left}
        return self

    def set_option(self, key: str, value: Any) -> Self:
        """
        [高级] 设置 ECharts `option` 中的一个原始键值对。
        这会覆盖由其他流畅API方法设置的同名配置。
        """
        self._data.raw_options[key] = value
        return self

    def set_background_image(self, image_name: str) -> Self:
        """【兼容】为横向柱状图设置背景图片。"""
        self._data.background_image = image_name
        return self


def bar_chart(
    title: str,
    items: list[tuple[str, int | float]],
    direction: Literal["horizontal", "vertical"] = "horizontal",
) -> EChartsBuilder:
    """便捷工厂函数：创建一个柱状图构建器。"""
    builder = EChartsBuilder("components/charts/bar_chart", title)
    categories = [item[0] for item in items]
    values = [item[1] for item in items]

    if direction == "horizontal":
        builder.set_x_axis(type="value")
        builder.set_y_axis(type="category", data=categories)
        builder.add_series(
            type="bar",
            data=values,
        )
    else:
        builder.set_x_axis(type="category", data=categories)
        builder.set_y_axis(type="value")
        builder.add_series(type="bar", data=values)

    return builder


def pie_chart(title: str, items: list[tuple[str, int | float]]) -> EChartsBuilder:
    """便捷工厂函数：创建一个饼图构建器。"""
    builder = EChartsBuilder("components/charts/pie_chart", title)
    data = [{"name": name, "value": value} for name, value in items]
    legend_data = [item[0] for item in items]

    builder.set_legend(data=legend_data)
    builder.add_series(
        name=title,
        type="pie",
        data=data,
    )
    return builder


def line_chart(
    title: str, categories: list[str], series: list[dict[str, Any]]
) -> EChartsBuilder:
    """便捷工厂函数：创建一个折线图构建器。"""
    builder = EChartsBuilder("components/charts/line_chart", title)

    builder.set_x_axis(type="category", data=categories)
    builder.set_y_axis(type="value")
    for s in series:
        builder.add_series(
            type="line",
            name=s.get("name", ""),
            data=s.get("data", []),
            smooth=s.get("smooth", False),
        )
    return builder


def radar_chart(
    title: str, indicators: list[tuple[str, int | float]], series: list[dict[str, Any]]
) -> EChartsBuilder:
    """便捷工厂函数：创建一个雷达图构建器。"""
    builder = EChartsBuilder("components/charts/radar_chart", title)
    legend_data = [s.get("name", "") for s in series]
    radar_indicators = [{"name": name, "max": max_val} for name, max_val in indicators]

    builder.set_legend(data=legend_data)
    builder.set_option("radar", {"indicator": radar_indicators})
    builder.add_series(type="radar", data=series)
    return builder
