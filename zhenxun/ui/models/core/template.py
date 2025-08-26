from pathlib import Path
from typing import Any

from .base import RenderableComponent

__all__ = ["TemplateComponent"]


class TemplateComponent(RenderableComponent):
    """基于独立模板文件的UI组件"""

    _is_standalone_template: bool = True
    template_path: str | Path
    data: dict[str, Any]

    @property
    def template_name(self) -> str:
        """返回模板路径"""
        if isinstance(self.template_path, Path):
            return self.template_path.as_posix()
        return str(self.template_path)

    def get_render_data(self) -> dict[str, Any]:
        """返回传递给模板的数据"""
        return self.data

    def __getattr__(self, name: str) -> Any:
        """允许直接访问 `data` 字典中的属性。"""
        try:
            return self.data[name]
        except KeyError:
            raise AttributeError(
                f"'{type(self).__name__}' 对象没有属性 '{name}'"
            ) from None
