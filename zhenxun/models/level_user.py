from tortoise import fields

from zhenxun.services.db_context import Model
from zhenxun.utils.enum import CacheType


class LevelUser(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    user_id = fields.CharField(255)
    """用户id"""
    group_id = fields.CharField(255)
    """群聊id"""
    user_level = fields.BigIntField()
    """用户权限等级"""
    group_flag = fields.IntField(default=0)
    """特殊标记，是否随群管理员变更而设置权限"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "level_users"
        table_description = "用户权限数据库"
        unique_together = ("user_id", "group_id")

    cache_type = CacheType.LEVEL
    """缓存类型"""
    cache_key_field = ("user_id", "group_id")
    """缓存键字段"""

    @classmethod
    async def get_user_level(cls, user_id: str, group_id: str | None) -> int:
        """获取用户在群内的等级

        参数:
            user_id: 用户id
            group_id: 群组id

        返回:
            int: 权限等级
        """
        if not group_id:
            return 0
        if user := await cls.get_or_none(user_id=user_id, group_id=group_id):
            return user.user_level
        return 0

    @classmethod
    async def set_level(
        cls,
        user_id: str,
        group_id: str,
        level: int,
        group_flag: int = 0,
    ):
        """设置用户在群内的权限

        参数:
            user_id: 用户id
            group_id: 群组id
            level: 权限等级
            group_flag: 是否被自动更新刷新权限 0:是, 1:否.
        """
        if await cls.exists(user_id=user_id, group_id=group_id, user_level=level):
            # 权限相同时跳过
            return
        await cls.update_or_create(
            user_id=user_id,
            group_id=group_id,
            defaults={
                "user_level": level,
                "group_flag": group_flag,
            },
        )

    @classmethod
    async def delete_level(cls, user_id: str, group_id: str) -> bool:
        """删除用户权限

        参数:
            user_id: 用户id
            group_id: 群组id

        返回:
            bool: 是否含有用户权限
        """
        if user := await cls.get_or_none(user_id=user_id, group_id=group_id):
            await user.delete()
            return True
        return False

    @classmethod
    async def check_level(cls, user_id: str, group_id: str | None, level: int) -> bool:
        """检查用户权限等级是否大于 level

        参数:
            user_id: 用户id
            group_id: 群组id
            level: 权限等级

        返回:
            bool: 是否大于level
        """
        if level == 0:
            return True
        if group_id:
            if user := await cls.get_or_none(user_id=user_id, group_id=group_id):
                return user.user_level >= level
        elif user_list := await cls.filter(user_id=user_id).all():
            user = max(user_list, key=lambda x: x.user_level)
            return user.user_level >= level
        return False

    @classmethod
    async def is_group_flag(cls, user_id: str, group_id: str) -> bool:
        """检测是否会被自动更新刷新权限

        参数:
            user_id: 用户id
            group_id: 群组id

        返回:
            bool: 是否会被自动更新权限刷新
        """
        if user := await cls.get_or_none(user_id=user_id, group_id=group_id):
            return user.group_flag == 1
        return False

    @classmethod
    async def _run_script(cls):
        return [
            # 将user_id改为user_id
            "ALTER TABLE level_users RENAME COLUMN user_qq TO user_id;",
            "ALTER TABLE level_users ALTER COLUMN user_id TYPE character varying(255);",
            # 将user_id字段类型改为character varying(255)
            "ALTER TABLE level_users "
            "ALTER COLUMN group_id TYPE character varying(255);",
        ]
