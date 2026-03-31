from tortoise import fields

from zhenxun.services.db_context import Model


class FriendUser(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    user_id = fields.CharField(255, unique=True, description="用户id")
    """用户id"""
    user_name = fields.CharField(max_length=255, default="", description="用户名称")
    """用户名称"""
    platform = fields.CharField(255, null=True, description="平台")
    """平台"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "friend_users"
        table_description = "好友信息数据表"

    @classmethod
    async def get_user_name(cls, user_id: str) -> str:
        """获取好友用户名称

        参数:
            user_id: 用户id
        """
        if user := await cls.get_or_none(user_id=user_id):
            return user.user_name
        return ""

    @classmethod
    def _run_script(cls):
        return ["ALTER TABLE friend_users DROP COLUMN nickname;"]
