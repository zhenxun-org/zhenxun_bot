from typing import Literal

from pydantic import Field

from ..core.base import RenderableComponent

__all__ = ["Avatar", "AvatarGroup", "Divider", "Rectangle", "UserInfoBlock"]


class Avatar(RenderableComponent):
    """单个头像组件。"""

    component_type: Literal["avatar"] = "avatar"
    """组件类型"""
    src: str = Field(..., description="头像的URL或Base64数据URI")
    """头像的URL或Base64数据URI"""
    shape: Literal["circle", "square"] = Field("circle", description="头像形状")
    """头像形状"""
    size: int = Field(50, description="头像尺寸（像素）")
    """头像尺寸（像素）"""

    @property
    def template_name(self) -> str:
        return "components/widgets/avatar"


class AvatarGroup(RenderableComponent):
    """一组堆叠的头像组件。"""

    component_type: Literal["avatar_group"] = "avatar_group"
    """组件类型"""
    avatars: list[Avatar] = Field(default_factory=list, description="头像列表")
    """头像列表"""
    spacing: int = Field(-15, description="头像间的间距（负数表示重叠）")
    """头像间的间距（负数表示重叠）"""
    max_count: int | None = Field(
        None, description="最多显示的头像数量，超出部分会显示为'+N'"
    )
    """最多显示的头像数量，超出部分会显示为'+N'"""

    @property
    def template_name(self) -> str:
        return "components/widgets/avatar"


class Divider(RenderableComponent):
    """一个简单的分割线组件。"""

    component_type: Literal["divider"] = "divider"
    """组件类型"""
    margin: str = Field("2em 0", description="CSS margin属性，控制分割线上下的间距")
    """CSS margin属性，控制分割线上下的间距"""
    color: str = Field("#f7889c", description="分割线颜色")
    """分割线颜色"""
    style: Literal["solid", "dashed", "dotted"] = Field("solid", description="线条样式")
    """线条样式"""
    thickness: str = Field("1px", description="线条粗细")
    """线条粗细"""

    @property
    def template_name(self) -> str:
        return "components/widgets/divider"


class Rectangle(RenderableComponent):
    """一个矩形背景块组件。"""

    component_type: Literal["rectangle"] = "rectangle"
    """组件类型"""
    height: str = Field("50px", description="矩形的高度 (CSS value)")
    """矩形的高度 (CSS value)"""
    background_color: str = Field("#fdf1f5", description="背景颜色")
    """背景颜色"""
    border: str = Field("1px solid #fce4ec", description="CSS border属性")
    """CSS border属性"""
    border_radius: str = Field("8px", description="CSS border-radius属性")
    """CSS border-radius属性"""

    @property
    def template_name(self) -> str:
        return "components/widgets/rectangle"


class UserInfoBlock(RenderableComponent):
    """一个带头像、名称和副标题的用户信息块组件。"""

    component_type: Literal["user_info_block"] = "user_info_block"
    """组件类型"""
    avatar_url: str = Field(..., description="用户头像的URL")
    """用户头像的URL"""
    name: str = Field(..., description="用户的名称")
    """用户的名称"""
    subtitle: str | None = Field(
        default=None, description="显示在名称下方的副标题 (如UID或角色)"
    )
    """显示在名称下方的副标题 (如UID或角色)"""
    tags: list[str] = Field(default_factory=list, description="附加的标签列表")
    """附加的标签列表"""

    @property
    def template_name(self) -> str:
        return "components/widgets/user_info_block"
