from abc import ABC, abstractmethod
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field

from zhenxun.utils.pydantic_compat import model_dump

from .core.base import RenderableComponent


class EChartsTitle(BaseModel):
    text: str
    left: Literal["left", "center", "right"] = "center"


class EChartsAxis(BaseModel):
    type: Literal["category", "value", "time", "log"]
    data: list[Any] | None = None
    show: bool = True


class EChartsSeries(BaseModel):
    type: str
    data: list[Any]
    name: str | None = None
    label: dict[str, Any] | None = None
    itemStyle: dict[str, Any] | None = None
    barMaxWidth: int | None = None
    smooth: bool | None = None


class EChartsTooltip(BaseModel):
    trigger: Literal["item", "axis", "none"] = "item"


class EChartsGrid(BaseModel):
    left: str | None = None
    right: str | None = None
    top: str | None = None
    bottom: str | None = None
    containLabel: bool = True


class BaseChartData(RenderableComponent, ABC):
    """所有图表数据模型的基类"""

    style_name: str | None = None
    chart_id: str = Field(default_factory=lambda: f"chart-{uuid.uuid4().hex}")

    echarts_options: dict[str, Any] | None = None

    @abstractmethod
    def build_option(self) -> dict[str, Any]:
        """将 Pydantic 模型序列化为 ECharts 的 option 字典。"""
        raise NotImplementedError

    def get_render_data(self) -> dict[str, Any]:
        """为图表组件定制渲染数据，动态构建最终的 option 对象。"""
        dumped_data = model_dump(self, exclude={"template_path"})
        if hasattr(self, "build_option"):
            dumped_data["option"] = self.build_option()
        return dumped_data

    def get_required_scripts(self) -> list[str]:
        """声明此组件需要 ECharts 库。"""
        return ["js/echarts.min.js"]


class EChartsData(BaseChartData):
    """统一的 ECharts 图表数据模型"""

    template_path: str = Field(..., exclude=True)
    title_model: EChartsTitle | None = Field(None, alias="title")
    grid_model: EChartsGrid | None = Field(None, alias="grid")
    tooltip_model: EChartsTooltip | None = Field(None, alias="tooltip")
    x_axis_model: EChartsAxis | None = Field(None, alias="xAxis")
    y_axis_model: EChartsAxis | None = Field(None, alias="yAxis")
    series_models: list[EChartsSeries] = Field(default_factory=list, alias="series")
    legend_model: dict[str, Any] | None = Field(default_factory=dict, alias="legend")
    raw_options: dict[str, Any] = Field(
        default_factory=dict, description="用于 set_option 的原始覆盖选项"
    )

    background_image: str | None = Field(
        None, description="【兼容】用于横向柱状图的背景图片"
    )

    def build_option(self) -> dict[str, Any]:
        """将 Pydantic 模型序列化为 ECharts 的 option 字典。"""
        option: dict[str, Any] = {}
        key_map = {
            "title": "title_model",
            "grid": "grid_model",
            "tooltip": "tooltip_model",
            "xAxis": "x_axis_model",
            "yAxis": "y_axis_model",
            "series": "series_models",
            "legend": "legend_model",
        }
        for echarts_key, model_attr in key_map.items():
            model_instance = getattr(self, model_attr, None)
            if model_instance:
                if isinstance(model_instance, list):
                    option[echarts_key] = [
                        model_dump(m, exclude_none=True) for m in model_instance
                    ]
                elif isinstance(model_instance, BaseModel):
                    option[echarts_key] = model_dump(model_instance, exclude_none=True)
                else:
                    option[echarts_key] = model_instance
        option.update(self.raw_options)
        return option

    @property
    def title(self) -> str:
        """为模板提供一个简单的字符串标题，保持向后兼容性。"""
        return self.title_model.text if self.title_model else ""

    @property
    def template_name(self) -> str:
        return self.template_path
