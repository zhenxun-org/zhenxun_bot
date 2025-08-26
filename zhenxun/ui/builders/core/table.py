from typing import Literal

from ...models.core.table import TableCell, TableData
from ..base import BaseBuilder

__all__ = ["TableBuilder"]


class TableBuilder(BaseBuilder[TableData]):
    """链式构建通用表格的辅助类"""

    def __init__(self, title: str, tip: str | None = None):
        data_model = TableData(title=title, tip=tip, headers=[], rows=[])
        super().__init__(data_model, template_name="components/core/table")

    def set_headers(self, headers: list[str]) -> "TableBuilder":
        """
        设置表格的表头。

        参数:
            headers: 一个包含表头文本的字符串列表。

        返回:
            TableBuilder: 当前构建器实例，以支持链式调用。
        """
        self._data.headers = headers
        return self

    def set_column_alignments(
        self, alignments: list[Literal["left", "center", "right"]]
    ) -> "TableBuilder":
        """
        设置表格每列的文本对齐方式。

        参数:
            alignments: 一个包含 'left', 'center', 'right' 的对齐方式列表。

        返回:
            TableBuilder: 当前构建器实例，以支持链式调用。
        """
        self._data.column_alignments = alignments
        return self

    def set_column_widths(self, widths: list[str | int]) -> "TableBuilder":
        """设置每列的宽度"""
        self._data.column_widths = widths
        return self

    def add_row(self, row: list[TableCell]) -> "TableBuilder":
        """
        向表格中添加一行数据。

        参数:
            row: 一个包含单元格数据的列表。单元格可以是字符串、数字或
                 `TextCell`, `ImageCell` 等模型实例。

        返回:
            TableBuilder: 当前构建器实例，以支持链式调用。
        """
        self._data.rows.append(row)
        return self

    def add_rows(self, rows: list[list[TableCell]]) -> "TableBuilder":
        """
        向表格中批量添加多行数据。

        参数:
            rows: 一个包含多行数据的列表。

        返回:
            TableBuilder: 当前构建器实例，以支持链式调用。
        """
        self._data.rows.extend(rows)
        return self
