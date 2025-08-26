from typing import Generic, TypeVar
from typing_extensions import Self

from pydantic import BaseModel

T_DataModel = TypeVar("T_DataModel", bound=BaseModel)


class BaseBuilder(Generic[T_DataModel]):
    """
    所有UI构建器的通用基类。

    它实现了Builder设计模式，提供了一个流畅的、链式调用的API来创建和配置UI组件的数据模型。
    同时，它也提供了通用的样式化方法，如 `with_style`, `with_inline_style` 等。

    参数:
        T_DataModel: 与此构建器关联的 Pydantic 数据模型类型。
    """

    def __init__(self, data_model: T_DataModel, template_name: str):
        self._data: T_DataModel = data_model
        self._style_name: str | None = None
        self._template_name = template_name
        self._inline_style: dict | None = None
        self._component_css: str | None = None
        self._variant: str | None = None
        self._extra_classes: list[str] = []

    @property
    def data(self) -> T_DataModel:
        return self._data

    def with_style(self, style_name: str) -> Self:
        """
        为组件应用一个特定的样式。

        参数:
            style_name: 在主题的CSS中定义的样式类名。

        返回:
            Self: 当前构建器实例，以支持链式调用。
        """
        self._style_name = style_name
        return self

    def with_inline_style(self, style: dict[str, str]) -> Self:
        """
        为组件的根元素应用动态的内联样式。

        参数:
            style: 一个CSS样式字典，例如
                   `{"background-color":"#fff","font-size":"16px"}`。

        返回:
            Self: 当前构建器实例，以支持链式调用。
        """
        self._inline_style = style
        return self

    def with_variant(self, variant_name: str) -> Self:
        """
        为组件应用一个特定的变体/皮肤。

        参数:
            variant_name: 在组件的 `skins/` 目录下定义的变体名称。

        返回:
            Self: 当前构建器实例，以支持链式调用。
        """
        self._variant = variant_name
        return self

    def with_component_css(self, css: str) -> Self:
        """
        向页面注入一段自定义的CSS样式字符串。

        参数:
            css: 包含CSS规则的字符串。

        返回:
            Self: 当前构建器实例，以支持链式调用。
        """
        self._component_css = css
        return self

    def with_classes(self, *class_names: str) -> Self:
        """
        为组件的根元素添加一个或多个CSS工具类。
        这些类来自主题预定义的工具集。

        示例: .with_classes("p-4", "text-center", "font-bold")
        """
        self._extra_classes.extend(class_names)
        return self

    def build(self) -> T_DataModel:
        """
        构建并返回配置好的数据模型。
        这是构建过程的最后一步，它会将所有配置应用到数据模型上。

        返回:
            T_DataModel: 最终配置好的、可被渲染服务使用的数据模型实例。
        """
        if self._style_name and hasattr(self._data, "style_name"):
            setattr(self._data, "style_name", self._style_name)

        if self._inline_style and hasattr(self._data, "inline_style"):
            setattr(self._data, "inline_style", self._inline_style)
        if self._component_css and hasattr(self._data, "component_css"):
            setattr(self._data, "component_css", self._component_css)
        if self._variant and hasattr(self._data, "variant"):
            setattr(self._data, "variant", self._variant)

        if self._extra_classes and hasattr(self._data, "extra_classes"):
            setattr(self._data, "extra_classes", self._extra_classes)

        return self._data
