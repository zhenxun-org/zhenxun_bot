from typing import Literal

from pydantic import Field

from ...registry import component
from ..core.base import RenderableComponent

__all__ = ["Alert", "Badge", "ProgressBar"]


@component(name="alert", namespace="core")
class Alert(RenderableComponent):
    """一个带样式的提示框组件，用于显示重要信息。"""

    component_type: Literal["alert"] = "alert"
    """组件类型"""
    type: Literal["info", "success", "warning", "error"] = Field(
        default="info", description="提示框的类型，决定了颜色和图标"
    )
    """提示框的类型，决定了颜色和图标"""
    title: str = Field(..., description="提示框的标题")
    """提示框的标题"""
    content: str = Field(..., description="提示框的主要内容")
    """提示框的主要内容"""
    show_icon: bool = Field(default=True, description="是否显示与类型匹配的图标")
    """是否显示与类型匹配的图标"""

    @property
    def template_name(self) -> str:
        return "components/widgets/alert"


class Badge(RenderableComponent):
    """一个简单的徽章组件，用于显示状态或标签。"""

    component_type: Literal["badge"] = "badge"
    """组件类型"""
    text: str = Field(..., description="徽章上显示的文本")
    """徽章上显示的文本"""
    color_scheme: Literal["primary", "success", "warning", "error", "info"] = Field(
        default="info",
        description="预设的颜色方案",
    )
    """预设的颜色方案"""

    @property
    def template_name(self) -> str:
        return "components/widgets/badge"


class ProgressBar(RenderableComponent):
    """一个进度条组件。"""

    component_type: Literal["progress_bar"] = "progress_bar"
    """组件类型"""
    progress: float = Field(..., ge=0, le=100, description="进度百分比 (0-100)")
    """进度百分比 (0-100)"""
    label: str | None = Field(default=None, description="显示在进度条上的可选文本")
    """显示在进度条上的可选文本"""
    color_scheme: Literal["primary", "success", "warning", "error", "info"] = Field(
        default="primary",
        description="预设的颜色方案",
    )
    """预设的颜色方案"""
    animated: bool = Field(default=False, description="是否显示动画效果")
    """是否显示动画效果"""

    @property
    def template_name(self) -> str:
        return "components/widgets/progress_bar"
