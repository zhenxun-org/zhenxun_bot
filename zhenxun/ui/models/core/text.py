from typing import Literal

from pydantic import BaseModel, Field

from .base import RenderableComponent


class TextSpan(BaseModel):
    """单个富文本片段的数据模型"""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    code: bool = False
    color: str | None = None
    font_size: str | None = None
    font_family: str | None = None


class TextData(RenderableComponent):
    """轻量级富文本组件的数据模型"""

    spans: list[TextSpan] = Field(default_factory=list, description="文本片段列表")
    align: Literal["left", "right", "center"] = Field(
        "left", description="整体文本对齐方式"
    )

    @property
    def template_name(self) -> str:
        return "components/core/text"
