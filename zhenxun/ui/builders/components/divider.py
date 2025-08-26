from typing import Literal

from ...models.components.divider import Divider
from ..base import BaseBuilder


class DividerBuilder(BaseBuilder[Divider]):
    """链式构建分割线组件的辅助类"""

    def __init__(
        self,
        margin: str = "2em 0",
        color: str = "#f7889c",
        style: Literal["solid", "dashed", "dotted"] = "solid",
        thickness: str = "1px",
    ):
        data_model = Divider(
            margin=margin, color=color, style=style, thickness=thickness
        )
        super().__init__(data_model, template_name="components/widgets/divider")
