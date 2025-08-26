from typing import Literal

from pydantic import Field

from ..core.base import RenderableComponent

__all__ = ["Avatar", "AvatarGroup"]


class Avatar(RenderableComponent):
    """单个头像组件。"""

    component_type: Literal["avatar"] = "avatar"
    src: str = Field(..., description="头像的URL或Base64数据URI")
    shape: Literal["circle", "square"] = Field("circle", description="头像形状")
    size: int = Field(50, description="头像尺寸（像素）")

    @property
    def template_name(self) -> str:
        return "components/widgets/avatar"


class AvatarGroup(RenderableComponent):
    """一组堆叠的头像组件。"""

    component_type: Literal["avatar_group"] = "avatar_group"
    avatars: list[Avatar] = Field(default_factory=list, description="头像列表")
    spacing: int = Field(-15, description="头像间的间距（负数表示重叠）")
    max_count: int | None = Field(
        None, description="最多显示的头像数量，超出部分会显示为'+N'"
    )

    @property
    def template_name(self) -> str:
        return "components/widgets/avatar"
