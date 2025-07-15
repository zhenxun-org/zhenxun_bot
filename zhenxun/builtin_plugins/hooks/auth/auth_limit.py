import asyncio
import time
from typing import ClassVar

import nonebot
from nonebot_plugin_uninfo import Uninfo
from pydantic import BaseModel

from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.plugin_limit import PluginLimit
from zhenxun.services.db_context import DB_TIMEOUT_SECONDS
from zhenxun.services.log import logger
from zhenxun.utils.enum import LimitWatchType, PluginLimitType
from zhenxun.utils.limiters import CountLimiter, FreqLimiter, UserBlockLimiter
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.time_utils import TimeUtils
from zhenxun.utils.utils import get_entity_ids

from .config import LOGGER_COMMAND, WARNING_THRESHOLD
from .exception import SkipPluginException

driver = nonebot.get_driver()


@PriorityLifecycle.on_startup(priority=5)
async def _():
    """初始化限制"""
    await LimitManager.init_limit()


class Limit(BaseModel):
    limit: PluginLimit
    limiter: FreqLimiter | UserBlockLimiter | CountLimiter

    class Config:
        arbitrary_types_allowed = True


class LimitManager:
    add_module: ClassVar[list] = []
    last_update_time: ClassVar[float] = 0
    update_interval: ClassVar[float] = 6000  # 1小时更新一次
    is_updating: ClassVar[bool] = False  # 防止并发更新

    cd_limit: ClassVar[dict[str, Limit]] = {}
    block_limit: ClassVar[dict[str, Limit]] = {}
    count_limit: ClassVar[dict[str, Limit]] = {}

    # 模块限制缓存，避免频繁查询数据库
    module_limit_cache: ClassVar[dict[str, tuple[float, list[PluginLimit]]]] = {}
    module_cache_ttl: ClassVar[float] = 60  # 模块缓存有效期（秒）

    @classmethod
    async def init_limit(cls):
        """初始化限制"""
        cls.last_update_time = time.time()
        try:
            await asyncio.wait_for(cls.update_limits(), timeout=DB_TIMEOUT_SECONDS * 2)
        except asyncio.TimeoutError:
            logger.error("初始化限制超时", LOGGER_COMMAND)

    @classmethod
    async def update_limits(cls):
        """更新限制信息"""
        # 防止并发更新
        if cls.is_updating:
            return

        cls.is_updating = True
        try:
            start_time = time.time()
            try:
                limit_list = await asyncio.wait_for(
                    PluginLimit.filter(status=True).all(), timeout=DB_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.error("查询限制信息超时", LOGGER_COMMAND)
                cls.is_updating = False
                return

            # 清空旧数据
            cls.add_module = []
            cls.cd_limit = {}
            cls.block_limit = {}
            cls.count_limit = {}
            # 添加新数据
            for limit in limit_list:
                cls.add_limit(limit)

            cls.last_update_time = time.time()
            elapsed = time.time() - start_time
            if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的更新
                logger.warning(f"更新限制信息耗时: {elapsed:.3f}s", LOGGER_COMMAND)
        finally:
            cls.is_updating = False

    @classmethod
    def add_limit(cls, limit: PluginLimit):
        """添加限制

        参数:
            limit: PluginLimit
        """
        if limit.module not in cls.add_module:
            cls.add_module.append(limit.module)
            if limit.limit_type == PluginLimitType.BLOCK:
                cls.block_limit[limit.module] = Limit(
                    limit=limit, limiter=UserBlockLimiter()
                )
            elif limit.limit_type == PluginLimitType.CD:
                cls.cd_limit[limit.module] = Limit(
                    limit=limit, limiter=FreqLimiter(limit.cd)
                )
            elif limit.limit_type == PluginLimitType.COUNT:
                cls.count_limit[limit.module] = Limit(
                    limit=limit, limiter=CountLimiter(limit.max_count)
                )

    @classmethod
    def unblock(
        cls, module: str, user_id: str, group_id: str | None, channel_id: str | None
    ):
        """解除插件block

        参数:
            module: 模块名
            user_id: 用户id
            group_id: 群组id
            channel_id: 频道id
        """
        if limit_model := cls.block_limit.get(module):
            limit = limit_model.limit
            limiter: UserBlockLimiter = limit_model.limiter  # type: ignore
            key_type = user_id
            if group_id and limit.watch_type == LimitWatchType.GROUP:
                key_type = channel_id or group_id
            logger.debug(
                f"解除对象: {key_type} 的block限制",
                LOGGER_COMMAND,
                session=user_id,
                group_id=group_id,
            )
            limiter.set_false(key_type)

    @classmethod
    async def get_module_limits(cls, module: str) -> list[PluginLimit]:
        """获取模块的限制信息，使用缓存减少数据库查询

        参数:
            module: 模块名

        返回:
            list[PluginLimit]: 限制列表
        """
        current_time = time.time()

        # 检查缓存
        if module in cls.module_limit_cache:
            cache_time, limits = cls.module_limit_cache[module]
            if current_time - cache_time < cls.module_cache_ttl:
                return limits

        # 缓存不存在或已过期，从数据库查询
        try:
            start_time = time.time()
            limits = await asyncio.wait_for(
                PluginLimit.filter(module=module, status=True).all(),
                timeout=DB_TIMEOUT_SECONDS,
            )
            elapsed = time.time() - start_time
            if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的查询
                logger.warning(
                    f"查询模块限制信息耗时: {elapsed:.3f}s, 模块: {module}",
                    LOGGER_COMMAND,
                )

            # 更新缓存
            cls.module_limit_cache[module] = (current_time, limits)
            return limits
        except asyncio.TimeoutError:
            logger.error(f"查询模块限制信息超时: {module}", LOGGER_COMMAND)
            # 超时时返回空列表，避免阻塞
            return []

    @classmethod
    async def check(
        cls,
        module: str,
        user_id: str,
        group_id: str | None,
        channel_id: str | None,
    ):
        """检测限制

        参数:
            module: 模块名
            user_id: 用户id
            group_id: 群组id
            channel_id: 频道id

        异常:
            IgnoredException: IgnoredException
        """
        start_time = time.time()

        # 定期更新全局限制信息
        if (
            time.time() - cls.last_update_time > cls.update_interval
            and not cls.is_updating
        ):
            # 使用异步任务更新，避免阻塞当前请求
            asyncio.create_task(cls.update_limits())  # noqa: RUF006

        # 如果模块不在已加载列表中，只加载该模块的限制
        if module not in cls.add_module:
            limits = await cls.get_module_limits(module)
            for limit in limits:
                cls.add_limit(limit)

        # 检查各种限制
        try:
            if limit_model := cls.cd_limit.get(module):
                await cls.__check(limit_model, user_id, group_id, channel_id)
            if limit_model := cls.block_limit.get(module):
                await cls.__check(limit_model, user_id, group_id, channel_id)
            if limit_model := cls.count_limit.get(module):
                await cls.__check(limit_model, user_id, group_id, channel_id)
        finally:
            # 记录总执行时间
            elapsed = time.time() - start_time
            if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
                logger.warning(
                    f"限制检查耗时: {elapsed:.3f}s, 模块: {module}",
                    LOGGER_COMMAND,
                    session=user_id,
                    group_id=group_id,
                )

    @classmethod
    async def __check(
        cls,
        limit_model: Limit | None,
        user_id: str,
        group_id: str | None,
        channel_id: str | None,
    ):
        """检测限制

        参数:
            limit_model: Limit
            user_id: 用户id
            group_id: 群组id
            channel_id: 频道id

        异常:
            IgnoredException: IgnoredException
        """
        if not limit_model:
            return
        limit = limit_model.limit
        limiter = limit_model.limiter
        is_limit = (
            LimitWatchType.ALL
            or (group_id and limit.watch_type == LimitWatchType.GROUP)
            or (not group_id and limit.watch_type == LimitWatchType.USER)
        )
        key_type = user_id
        if group_id and limit.watch_type == LimitWatchType.GROUP:
            key_type = channel_id or group_id
        if is_limit and not limiter.check(key_type):
            if limit.result:
                format_kwargs = {}
                if isinstance(limiter, FreqLimiter):
                    left_time = limiter.left_time(key_type)
                    cd_str = TimeUtils.format_duration(left_time)
                    format_kwargs = {"cd": cd_str}
                try:
                    await asyncio.wait_for(
                        MessageUtils.build_message(
                            limit.result, format_args=format_kwargs
                        ).send(),
                        timeout=DB_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.error(f"发送限制消息超时: {limit.module}", LOGGER_COMMAND)
            raise SkipPluginException(
                f"{limit.module}({limit.limit_type}) 正在限制中..."
            )
        else:
            logger.debug(
                f"开始进行限制 {limit.module}({limit.limit_type})...",
                LOGGER_COMMAND,
                session=user_id,
                group_id=group_id,
            )
            if isinstance(limiter, FreqLimiter):
                limiter.start_cd(key_type)
            if isinstance(limiter, UserBlockLimiter):
                limiter.set_true(key_type)
            if isinstance(limiter, CountLimiter):
                limiter.increase(key_type)


async def auth_limit(plugin: PluginInfo, session: Uninfo):
    """插件限制

    参数:
        plugin: PluginInfo
        session: Uninfo
    """
    entity = get_entity_ids(session)
    try:
        await asyncio.wait_for(
            LimitManager.check(
                plugin.module, entity.user_id, entity.group_id, entity.channel_id
            ),
            timeout=DB_TIMEOUT_SECONDS * 2,  # 给予更长的超时时间
        )
    except asyncio.TimeoutError:
        logger.error(f"检查插件限制超时: {plugin.module}", LOGGER_COMMAND)
        # 超时时不抛出异常，允许继续执行
