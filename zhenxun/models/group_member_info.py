from typing import ClassVar

from tortoise import fields

from zhenxun.services.db_context import Model
from zhenxun.services.db_context.schema_ops import AlterColumnType, DropColumn


class GroupInfoUser(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    user_id = fields.CharField(255)
    """用户id"""
    user_name = fields.CharField(255, default="")
    """用户昵称"""
    group_id = fields.CharField(255)
    """群聊id"""
    user_join_time = fields.DatetimeField(null=True)
    """用户入群时间"""
    uid = fields.BigIntField(null=True)
    """用户uid"""
    platform = fields.CharField(255, null=True, description="平台")
    """平台"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "group_info_users"
        table_description = "群员信息数据表"
        unique_together = ("user_id", "group_id")
        indexes: ClassVar = [("group_id",), ("user_id",)]

    @classmethod
    async def get_all_uid(cls, group_id: str) -> set[str]:
        """获取该群所有用户id

        参数:
            group_id: 群号
        """
        from zhenxun.services.hot_query_cache import get_group_user_ids

        return await get_group_user_ids(group_id)

    @classmethod
    async def get_user_all_group(cls, user_id: str) -> list[str]:
        """获取该用户所在的所有群聊

        参数:
            user_id: 用户id
        """
        from zhenxun.services.hot_query_cache import get_user_group_ids

        return await get_user_group_ids(user_id)

    @classmethod
    async def _run_script(cls):
        return [
            DropColumn("group_info_users", "nickname"),
            AlterColumnType(
                "group_info_users",
                "user_id",
                {"postgres": "character varying(255)", "mysql": "VARCHAR(255)"},
                nullable=False,
            ),
            AlterColumnType(
                "group_info_users",
                "group_id",
                {"postgres": "character varying(255)", "mysql": "VARCHAR(255)"},
                nullable=False,
            ),
        ]
