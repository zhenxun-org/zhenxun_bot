from datetime import datetime
from typing import Any

from nonebot_plugin_alconna import UniMessage

from zhenxun.models.group_console import GroupConsole

GroupKey = str
MessageID = int
BroadcastResult = tuple[int, int]
BroadcastDetailResult = tuple[int, int, int]


class BroadcastTarget:
    """广播目标"""

    def __init__(self, group_id: str, channel_id: str | None = None):
        self.group_id = group_id
        self.channel_id = channel_id

    def to_dict(self) -> dict[str, str | None]:
        """转换为字典格式"""
        return {"group_id": self.group_id, "channel_id": self.channel_id}

    @classmethod
    def from_group_console(cls, group: GroupConsole) -> "BroadcastTarget":
        """从 GroupConsole 对象创建"""
        return cls(group_id=group.group_id, channel_id=group.channel_id)

    @property
    def key(self) -> str:
        """获取群组的唯一标识"""
        if self.channel_id:
            return f"{self.group_id}:{self.channel_id}"
        return str(self.group_id)


class BroadcastTask:
    """广播任务"""

    def __init__(
        self,
        bot_id: str,
        message: UniMessage,
        targets: list[BroadcastTarget],
        scheduled_time: datetime | None = None,
        task_id: str | None = None,
    ):
        self.bot_id = bot_id
        self.message = message
        self.targets = targets
        self.scheduled_time = scheduled_time
        self.task_id = task_id

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，用于序列化"""
        return {
            "bot_id": self.bot_id,
            "targets": [t.to_dict() for t in self.targets],
            "scheduled_time": self.scheduled_time.isoformat()
            if self.scheduled_time
            else None,
            "task_id": self.task_id,
        }
