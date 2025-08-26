from typing_extensions import Self

from ...models.components.timeline import Timeline, TimelineItem
from ..base import BaseBuilder


class TimelineBuilder(BaseBuilder[Timeline]):
    """链式构建时间轴组件的辅助类"""

    def __init__(self):
        data_model = Timeline(items=[])
        super().__init__(data_model, template_name="components/widgets/timeline")

    def add_item(
        self,
        timestamp: str,
        title: str,
        content: str,
        *,
        icon: str | None = None,
        color: str | None = None,
    ) -> Self:
        """向时间轴中添加一个事件点"""
        item = TimelineItem(
            timestamp=timestamp, title=title, content=content, icon=icon, color=color
        )
        self._data.items.append(item)
        return self
