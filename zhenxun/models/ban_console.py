import time
from typing import ClassVar
from typing_extensions import Self

from tortoise import fields
from tortoise.expressions import Q

from zhenxun.services.cache.runtime_cache import BanMemoryCache
from zhenxun.services.db_context import Model
from zhenxun.services.log import logger
from zhenxun.utils.enum import DbLockType
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
    ban_reason = fields.TextField(null=True, default=None)
    """ban的理由"""
    duration = fields.BigIntField()
    """ban时长"""
    operator = fields.CharField(255)
    """使用Ban命令的用户"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "ban_console"
        table_description = "封禁人员/群组数据表"
        unique_together = ("user_id", "group_id")
        indexes = [("user_id",), ("group_id",)]  # noqa: RUF012

    enable_lock: ClassVar[list[DbLockType]] = [DbLockType.CREATE, DbLockType.UPSERT]
    """开启锁"""

    @classmethod
    async def create(cls, *args, **kwargs) -> Self:
        result = await super().create(*args, **kwargs)
        await BanMemoryCache.upsert_from_model(result)
        return result

    async def delete(self, *args, **kwargs):
        user_id = self.user_id
        group_id = self.group_id
        await super().delete(*args, **kwargs)
        await BanMemoryCache.remove(user_id, group_id)

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
            return await cls.safe_get_or_none(
                Q(user_id__isnull=True) | Q(user_id=""),
                group_id=group_id,
            )

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
        logger.debug("检测用户被ban等级", target=f"{group_id}:{user_id}")
        if not BanMemoryCache.is_loaded():
            return False
        return BanMemoryCache.check_ban_level(user_id, group_id, level)

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
        if not BanMemoryCache.is_loaded():
            return 0
        return BanMemoryCache.remaining_time(user_id, group_id)

    @classmethod
    async def is_ban(cls, user_id: str | None, group_id: str | None = None) -> bool:
        """判断用户是否被ban

        参数:
            user_id: 用户id

        返回:
            bool: 是否被ban
        """
        logger.debug("检测是否被ban", target=f"{group_id}:{user_id}")
        return (await cls.check_ban_time(user_id, group_id)) != 0

    @classmethod
    async def ban(
        cls,
        user_id: str | None,
        group_id: str | None,
        ban_level: int,
        reason: str | None,
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
        if not user_id and not group_id:
            raise UserAndGroupIsNone()
        target, _ = await cls.update_or_create(
            user_id=user_id,
            group_id=group_id,
            defaults={
                "ban_level": ban_level,
                "ban_time": int(time.time()),
                "ban_reason": reason,
                "duration": duration,
                "operator": operator or 0,
            },
        )
        await BanMemoryCache.upsert_from_model(target)

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
        return []
