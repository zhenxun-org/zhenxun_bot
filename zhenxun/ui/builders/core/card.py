from typing_extensions import Self

from ...models.core.base import RenderableComponent
from ...models.core.card import CardData
from ..base import BaseBuilder


class CardBuilder(BaseBuilder[CardData]):
    """链式构建通用卡片容器的辅助类"""

    def __init__(self, content: "RenderableComponent | BaseBuilder"):
        content_model = content.build() if isinstance(content, BaseBuilder) else content
        data_model = CardData(content=content_model)
        super().__init__(data_model, template_name="components/core/card")

    def set_header(self, header: "RenderableComponent | BaseBuilder") -> Self:
        """设置卡片的头部组件"""
        header_model = header.build() if isinstance(header, BaseBuilder) else header
        self._data.header = header_model
        return self

    def set_footer(self, footer: "RenderableComponent | BaseBuilder") -> Self:
        """设置卡片的尾部组件"""
        footer_model = footer.build() if isinstance(footer, BaseBuilder) else footer
        self._data.footer = footer_model
        return self
