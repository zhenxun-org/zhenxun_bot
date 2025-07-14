import time
from typing import ClassVar
from typing_extensions import Self

from tortoise import fields

from zhenxun.services.db_context import Model
from zhenxun.services.log import logger
from zhenxun.utils.enum import CacheType, DbLockType
from zhenxun.utils.exception import UserAndGroupIsNone


class BanConsole(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    user_id = fields.CharField(255, null=True)
    """用户id"""
    group_id = fields.CharField(255, null=True)
    """群组id"""
    ban_level = fields.IntField()
    """使用ban命令的用户等级"""
    ban_time = fields.BigIntField()
    """ban开始的时间"""
    duration = fields.BigIntField()
    """ban时长"""
    operator = fields.CharField(255)
    """使用Ban命令的用户"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "ban_console"
        table_description = "封禁人员/群组数据表"
        unique_together = ("user_id", "group_id")
        indexes = [("user_id",), ("group_id",)]  # noqa: RUF012

    cache_type = CacheType.BAN
    """缓存类型"""
    cache_key_field = ("user_id", "group_id")
    """缓存键字段"""
    enable_lock: ClassVar[list[DbLockType]] = [DbLockType.CREATE, DbLockType.UPSERT]
    """开启锁"""

    @classmethod
    async def _get_data(cls, user_id: str | None, group_id: str | None) -> Self | None:
        """获取数据

        参数:
            user_id: 用户id
            group_id: 群组id

        异常:
            UserAndGroupIsNone: 用户id和群组id都为空

        返回:
            Self | None: Self
        """
        if not user_id and not group_id:
            raise UserAndGroupIsNone()
        if user_id:
            return (
                await cls.safe_get_or_none(user_id=user_id, group_id=group_id)
                if group_id
                else await cls.safe_get_or_none(user_id=user_id, group_id__isnull=True)
            )
        else:
            return await cls.safe_get_or_none(user_id="", group_id=group_id)

    @classmethod
    async def check_ban_level(
        cls, user_id: str | None, group_id: str | None, level: int
    ) -> bool:
        """检测ban掉目标的用户与unban用户的权限等级大小

        参数:
            user_id: 用户id
            group_id: 群组id
            level: 权限等级

        返回:
            bool: 权限判断，能否unban
        """
        user = await cls._get_data(user_id, group_id)
        if user:
            logger.debug(
                f"检测用户被ban等级，user_level: {user.ban_level}，level: {level}",
                target=f"{group_id}:{user_id}",
            )
            return user.ban_level <= level
        return False

    @classmethod
    async def check_ban_time(
        cls, user_id: str | None, group_id: str | None = None
    ) -> int:
        """检测用户被ban时长

        参数:
            user_id: 用户id

        返回:
            int: ban剩余时长，-1时为永久ban，0表示未被ban
        """
        logger.debug("获取用户ban时长", target=f"{group_id}:{user_id}")
        user = await cls._get_data(user_id, group_id)
        if not user and user_id:
            user = await cls._get_data(user_id, None)
        if user:
            if user.duration == -1:
                return -1
            _time = time.time() - (user.ban_time + user.duration)
            return 0 if _time > 0 else int(time.time() - user.ban_time - user.duration)
        return 0

    @classmethod
    async def is_ban(cls, user_id: str | None, group_id: str | None = None) -> bool:
        """判断用户是否被ban

        参数:
            user_id: 用户id

        返回:
            bool: 是否被ban
        """
        logger.debug("检测是否被ban", target=f"{group_id}:{user_id}")
        if await cls.check_ban_time(user_id, group_id):
            return True
        else:
            await cls.unban(user_id, group_id)
        return False

    @classmethod
    async def ban(
        cls,
        user_id: str | None,
        group_id: str | None,
        ban_level: int,
        duration: int,
        operator: str | None = None,
    ):
        """ban掉目标用户

        参数:
            user_id: 用户id
            group_id: 群组id
            ban_level: 使用命令者的权限等级
            duration: 时长，分钟，-1时为永久
            operator: 操作者id
        """
        logger.debug(
            f"封禁用户/群组，等级:{ban_level}，时长: {duration}",
            target=f"{group_id}:{user_id}",
        )
        target = await cls._get_data(user_id, group_id)
        if target:
            await cls.unban(user_id, group_id)
        await cls.create(
            user_id=user_id,
            group_id=group_id,
            ban_level=ban_level,
            ban_time=int(time.time()),
            duration=duration,
            operator=operator or 0,
        )

    @classmethod
    async def unban(cls, user_id: str | None, group_id: str | None = None) -> bool:
        """unban用户

        参数:
            user_id: 用户id
            group_id: 群组id

        返回:
            bool: 是否被ban
        """
        user = await cls._get_data(user_id, group_id)
        if user:
            logger.debug("解除封禁", target=f"{group_id}:{user_id}")
            await user.delete()
            return True
        return False

    @classmethod
    async def get_ban(
        cls,
        *,
        id: int | None = None,
        user_id: str | None = None,
        group_id: str | None = None,
    ) -> Self | None:
        """安全地获取ban记录

        参数:
            id: 记录id
            user_id: 用户id
            group_id: 群组id

        返回:
            Self | None: ban记录
        """
        if id is not None:
            return await cls.safe_get_or_none(id=id)
        return await cls._get_data(user_id, group_id)

    @classmethod
    async def _run_script(cls):
        return [
            "CREATE INDEX idx_ban_console_user_id ON ban_console(user_id);",
            "CREATE INDEX idx_ban_console_group_id ON ban_console(group_id);",
        ]
