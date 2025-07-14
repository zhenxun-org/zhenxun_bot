from tortoise import fields

from zhenxun.configs.config import Config
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
    nickname = fields.CharField(255, null=True)
    """群聊昵称"""
    uid = fields.BigIntField(null=True)
    """用户uid"""
    platform = fields.CharField(255, null=True, description="平台")
    """平台"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "group_info_users"
        table_description = "群员信息数据表"
        unique_together = ("user_id", "group_id")
    _unicode_safe_fields = ["user_name", "nickname"]  # noqa: RUF012

    @classmethod
    async def get_all_uid(cls, group_id: str) -> set[int]:
        """获取该群所有用户id

        参数:
            group_id: 群号
        """
        return set(
            await cls.filter(group_id=group_id).values_list("user_id", flat=True)
        )  # type: ignore

    @classmethod
    async def set_user_nickname(
        cls,
        user_id: str,
        group_id: str,
        nickname: str,
        uname: str | None = None,
        platform: str | None = None,
    ):
        """设置群员在该群内的昵称

        参数:
            user_id: 用户id
            group_id: 群号
            nickname: 昵称
            uname: 用户昵称
            platform: 平台
        """
        defaults = {"nickname": nickname}
        if uname is not None:
            defaults["user_name"] = uname
        if platform is not None:
            defaults["platform"] = platform
        await cls.update_or_create(
            user_id=user_id,
            group_id=group_id,
            defaults=defaults,
        )

    @classmethod
    async def get_user_all_group(cls, user_id: str) -> list[int]:
        """获取该用户所在的所有群聊

        参数:
            user_id: 用户id
        """
        return list(
            await cls.filter(user_id=str(user_id)).values_list("group_id", flat=True)
        )  # type: ignore

    @classmethod
    async def get_user_nickname(cls, user_id: str, group_id: str) -> str:
        """获取用户在该群的昵称

        参数:
            user_id: 用户id
            group_id: 群号
        """
        if user := await cls.get_or_none(user_id=user_id, group_id=group_id):
            if user.nickname:
                nickname = ""
                if black_word := Config.get_config("nickname", "BLACK_WORD"):
                    for x in user.nickname:
                        nickname += "*" if x in black_word else x
                    return nickname
                return user.nickname
        return ""

    @classmethod
    async def _run_script(cls):
        return [
            # 允许 user_join_time 为空
            "alter table group_info_users alter user_join_time drop not null;",
            "ALTER TABLE group_info_users "
            "ALTER COLUMN user_join_time TYPE timestamp with time zone "
            "USING user_join_time::timestamp with time zone;",
            # 将user_id改为user_id
            "ALTER TABLE group_info_users RENAME COLUMN user_qq TO user_id;",
            "ALTER TABLE group_info_users "
            "ALTER COLUMN user_id TYPE character varying(255);",
            # 将user_id字段类型改为character varying(255)
            "ALTER TABLE group_info_users "
            "ALTER COLUMN group_id TYPE character varying(255);",
            "ALTER TABLE group_info_users "
            "ADD COLUMN platform character varying(255) default 'qq';",
        ]
