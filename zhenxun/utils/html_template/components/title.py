from ..component import Component, Style
from ..container import Row


class Title(Component):
    def __init__(self, text: str, color: str = "#000"):
        self.text = text
        self.color = color

    def build(self):
        row = Row()
        style = Style(font_size="36px", color=self.color)
        row.set_style(style)

    # def
