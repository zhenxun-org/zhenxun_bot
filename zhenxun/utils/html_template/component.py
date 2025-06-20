from abc import ABC
from typing import Literal

from pydantic import BaseModel


class Style(BaseModel):
    """常用样式"""

    padding: str = "0px"
    margin: str = "0px"
    border: str = "0px"
    border_radius: str = "0px"
    text_align: Literal["left", "right", "center"] = "left"
    color: str = "#000"
    font_size: str = "16px"


class Component(ABC):
    def __init__(self, background_color: str = "#fff", is_container: bool = False):
        self.extra_style = []
        self.style = Style()
        self.background_color = background_color
        self.is_container = is_container
        self.children = []

    def add_child(self, child: "Component | str"):
        self.children.append(child)

    def set_style(self, style: Style):
        self.style = style

    def add_style(self, style: str):
        self.extra_style.append(style)

    def to_html(self) -> str: ...
