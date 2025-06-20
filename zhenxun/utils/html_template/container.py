from .component import Component


class Row(Component):
    def __init__(self, background_color: str = "#fff"):
        super().__init__(background_color, True)


class Col(Component):
    def __init__(self, background_color: str = "#fff"):
        super().__init__(background_color, True)


class Container(Component):
    def __init__(self, background_color: str = "#fff"):
        super().__init__(background_color, True)
        self.children = []


class GlobalOverview:
    def __init__(self, name: str):
        self.name = name
        self.class_name: dict[str, list[str]] = {}
        self.content = None

    def set_content(self, content: Container):
        self.content = content

    def add_class(self, class_name: str, contents: list[str]):
        """全局样式"""
        self.class_name[class_name] = contents
