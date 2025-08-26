from typing import Any
from typing_extensions import Self

from ...models.core.base import RenderableComponent
from ...models.core.layout import LayoutData, LayoutItem
from ..base import BaseBuilder

__all__ = ["LayoutBuilder"]


class LayoutBuilder(BaseBuilder[LayoutData]):
    """
    一个用于将多个UI组件组合成单张图片的链式构建器。
    它通过在单个渲染流程中动态包含子模板来实现高质量的输出。
    """

    def __init__(self):
        super().__init__(LayoutData(), template_name="")
        self._options: dict[str, Any] = {}

    @classmethod
    def column(
        cls, *, gap: str = "20px", align_items: str = "stretch", **options: Any
    ) -> Self:
        builder = cls()
        builder._template_name = "components/core/layouts/column"
        builder._options["gap"] = gap
        builder._options["align_items"] = align_items
        builder._options.update(options)
        return builder

    @classmethod
    def row(
        cls, *, gap: str = "10px", align_items: str = "center", **options: Any
    ) -> Self:
        builder = cls()
        builder._template_name = "components/core/layouts/row"
        builder._options["gap"] = gap
        builder._options["align_items"] = align_items
        builder._options.update(options)
        return builder

    @classmethod
    def grid(cls, columns: int = 2, **options: Any) -> Self:
        builder = cls()
        builder._template_name = "components/core/layouts/grid"
        builder._options["columns"] = columns
        builder._options.update(options)
        return builder

    @classmethod
    def hstack(
        cls, components: list["BaseBuilder | RenderableComponent"], **options: Any
    ) -> Self:
        builder = cls.row(**options)
        for component in components:
            builder.add_item(component)
        return builder

    @classmethod
    def vstack(
        cls, components: list["BaseBuilder | RenderableComponent"], **options: Any
    ) -> Self:
        builder = cls.column(**options)
        for component in components:
            builder.add_item(component)
        return builder

    def add_item(
        self,
        component: "BaseBuilder | RenderableComponent",
        metadata: dict[str, Any] | None = None,
    ) -> Self:
        """
        向布局中添加一个组件项。

        参数:
            component: 一个 `BaseBuilder` 实例 (如 `TableBuilder()`) 或一个已构建的
                       `RenderableComponent` 数据模型。
            metadata: (可选) 与此项目关联的元数据，可在布局模板中访问。

        返回:
            Self: 当前构建器实例，以支持链式调用。
        """
        component_data = (
            component.data if isinstance(component, BaseBuilder) else component
        )
        self._data.children.append(
            LayoutItem(component=component_data, metadata=metadata)
        )
        return self

    def add_option(self, key: str, value: Any) -> Self:
        """
        为布局模板添加一个自定义选项。

        例如，`add_option("padding", "30px")` 会在模板的 `data.options`
        字典中添加 `{"padding": "30px"}`。

        参数:
            key: 选项的键名。
            value: 选项的值。

        返回:
            Self: 当前构建器实例，以支持链式调用。
        """
        self._options[key] = value
        return self

    def build(self) -> LayoutData:
        """
        构建并返回 LayoutData 模型实例。
        """
        if not self._template_name:
            raise ValueError(
                "必须通过工厂方法 (如 LayoutBuilder.column()) 初始化布局类型。"
            )

        self._data.options = self._options
        self._data.layout_type = self._template_name.split("/")[-1]
        return super().build()
