from typing import Literal

from pydantic import Field

from ..core.base import RenderableComponent

__all__ = ["Alert"]


class Alert(RenderableComponent):
    """一个带样式的提示框组件，用于显示重要信息。"""

    component_type: Literal["alert"] = "alert"
    type: Literal["info", "success", "warning", "error"] = Field(
        default="info", description="提示框的类型，决定了颜色和图标"
    )
    title: str = Field(..., description="提示框的标题")
    content: str = Field(..., description="提示框的主要内容")
    show_icon: bool = Field(default=True, description="是否显示与类型匹配的图标")

    @property
    def template_name(self) -> str:
        return "components/widgets/alert"
