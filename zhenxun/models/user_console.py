import asyncio
from typing import ClassVar

from tortoise import fields
from tortoise.exceptions import IntegrityError

from zhenxun.models.goods_info import GoodsInfo
from zhenxun.services.buffered_writers import append_user_gold_log
from zhenxun.services.db_context import Model
from zhenxun.utils.enum import CacheType, GoldHandle
from zhenxun.utils.exception import GoodsNotFound, InsufficientGold


class UserConsole(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    user_id = fields.CharField(255, unique=True, description="用户id")
    """用户id"""
    uid = fields.IntField(description="UID", unique=True)
    """UID"""
    gold = fields.IntField(default=100, description="金币数量")
    """金币数量"""
    sign = fields.ReverseRelation["SignUser"]  # type: ignore
    """好感度"""
    props: dict[str, int] = fields.JSONField(default={})  # type: ignore
    """道具"""
    platform = fields.CharField(255, null=True, description="平台")
    """平台"""
    create_time = fields.DatetimeField(auto_now_add=True, description="创建时间")
    """创建时间"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "user_console"
        table_description = "用户数据表"
        indexes = [("user_id",), ("uid",)]  # noqa: RUF012

    cache_type = CacheType.USERS
    """缓存类型"""
    cache_key_field = "user_id"
    """缓存键字段"""

    _uid_counter: ClassVar[int | None] = None
    _uid_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    @classmethod
    async def get_or_create_user(
        cls, user_id: str, platform: str | None = None
    ) -> tuple["UserConsole", bool]:
        for attempt in range(2):
            try:
                return await cls.get_or_create(
                    user_id=user_id,
                    defaults={"platform": platform, "uid": await cls.get_new_uid()},
                )
            except IntegrityError:
                async with cls._uid_lock:
                    cls._uid_counter = None
                if attempt >= 1:
                    raise
        return await cls.get_or_create(
            user_id=user_id,
            defaults={"platform": platform, "uid": await cls.get_new_uid()},
        )

    @classmethod
    async def get_user(cls, user_id: str, platform: str | None = None) -> "UserConsole":
        """获取用户

        参数:
            user_id: 用户id
            platform: 平台.

        返回:
            UserConsole: UserConsole
        """
        user, _ = await cls.get_or_create_user(user_id=user_id, platform=platform)
        return user

    @classmethod
    async def _get_user_for_write(
        cls, user_id: str, platform: str | None = None
    ) -> "UserConsole":
        """获取写入用用户；已有用户不走 get_or_create，避免重复清理缓存。"""
        user = await cls.get_or_none(user_id=user_id)
        if user is not None:
            return user
        user, _ = await cls.get_or_create_user(user_id=user_id, platform=platform)
        return user

    @classmethod
    async def get_new_uid(cls) -> int:
        """获取最新uid

        返回:
            int: 最新uid
        """
        async with cls._uid_lock:
            if cls._uid_counter is None:
                user = await cls.annotate().order_by("-uid").first()
                cls._uid_counter = user.uid if user else 0
            cls._uid_counter += 1
            return cls._uid_counter

    @classmethod
    async def add_gold(
        cls, user_id: str, gold: int, source: str, platform: str | None = None
    ):
        """添加金币

        参数:
            user_id: 用户id
            gold: 金币
            source: 来源
            platform: 平台.
        """
        user = await cls._get_user_for_write(user_id=user_id, platform=platform)
        user.gold += gold
        await user.save(update_fields=["gold"])
        await append_user_gold_log(
            user_id=user_id, gold=gold, handle=GoldHandle.GET, source=source
        )

    @classmethod
    async def reduce_gold(
        cls,
        user_id: str,
        gold: int,
        handle: GoldHandle,
        plugin_module: str,
        platform: str | None = None,
    ):
        """消耗金币

        参数:
            user_id: 用户id
            gold: 金币
            handle: 金币处理
            plugin_name: 插件模块
            platform: 平台.

        异常:
            InsufficientGold: 金币不足
        """
        user = await cls._get_user_for_write(user_id=user_id, platform=platform)
        if user.gold < gold:
            raise InsufficientGold()
        user.gold -= gold
        await user.save(update_fields=["gold"])
        await append_user_gold_log(
            user_id=user_id, gold=gold, handle=handle, source=plugin_module
        )

    @classmethod
    async def add_props(
        cls, user_id: str, goods_uuid: str, num: int = 1, platform: str | None = None
    ):
        """添加道具

        参数:
            user_id: 用户id
            goods_uuid: 道具uuid
            num: 道具数量.
            platform: 平台.
        """
        user = await cls._get_user_for_write(user_id=user_id, platform=platform)
        if goods_uuid not in user.props:
            user.props[goods_uuid] = 0
        user.props[goods_uuid] += num
        await user.save(update_fields=["props"])

    @classmethod
    async def add_props_by_name(
        cls, user_id: str, name: str, num: int = 1, platform: str | None = None
    ):
        """根据名称添加道具

        参数:
            user_id: 用户id
            name: 道具名称
            num: 道具数量.
            platform: 平台.
        """
        if goods := await GoodsInfo.get_or_none(goods_name=name):
            return await cls.add_props(user_id, goods.uuid, num, platform)
        raise GoodsNotFound("未找到商品...")

    @classmethod
    async def use_props(
        cls, user_id: str, goods_uuid: str, num: int = 1, platform: str | None = None
    ):
        """添加道具

        参数:
            user_id: 用户id
            goods_uuid: 道具uuid
            num: 道具数量.
            platform: 平台.
        """
        user = await cls._get_user_for_write(user_id=user_id, platform=platform)

        if goods_uuid not in user.props or user.props[goods_uuid] < num:
            raise GoodsNotFound("未找到商品或道具数量不足...")
        user.props[goods_uuid] -= num
        if user.props[goods_uuid] <= 0:
            del user.props[goods_uuid]
        await user.save(update_fields=["props"])

    @classmethod
    async def use_props_by_name(
        cls, user_id: str, name: str, num: int = 1, platform: str | None = None
    ):
        """根据名称添加道具

        参数:
            user_id: 用户id
            name: 道具名称
            num: 道具数量.
            platform: 平台.
        """
        if goods := await GoodsInfo.get_or_none(goods_name=name):
            return await cls.use_props(user_id, goods.uuid, num, platform)
        raise GoodsNotFound("未找到商品...")

    @classmethod
    async def _run_script(cls):
        return [
            "CREATE INDEX idx_user_console_user_id ON user_console(user_id);",
            "CREATE INDEX idx_user_console_uid ON user_console(uid);",
        ]
