from tortoise import fields

from zhenxun.services.db_context import Model


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

    @classmethod
    async def get_all_uid(cls, group_id: str) -> set[str]:
        """获取该群所有用户id

        参数:
            group_id: 群号
        """
        return set(
            await cls.filter(group_id=group_id).values_list("user_id", flat=True)
        )  # type: ignore

    @classmethod
    async def get_user_all_group(cls, user_id: str) -> list[str]:
        """获取该用户所在的所有群聊

        参数:
            user_id: 用户id
        """
        return list(
            await cls.filter(user_id=user_id).values_list("group_id", flat=True)
        )  # type: ignore

    @classmethod
    async def _run_script(cls):
        return ["ALTER TABLE group_info_users DROP COLUMN nickname;"]
