import asyncio
import time

from nonebot.adapters import Event
from nonebot_plugin_uninfo import Uninfo

from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.db_context import DB_TIMEOUT_SECONDS
from zhenxun.services.log import logger
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.enum import BlockType

from .config import LOGGER_COMMAND, WARNING_THRESHOLD
from .exception import IsSuperuserException, SkipPluginException
from .utils import freq, is_poke, send_message


class GroupCheck:
    def __init__(
        self, plugin: PluginInfo, group: GroupConsole, session: Uninfo, is_poke: bool
    ) -> None:
        self.session = session
        self.is_poke = is_poke
        self.plugin = plugin
        self.group_data = group
        self.group_id = group.group_id

    async def check(self):
        start_time = time.time()
        try:
            # 检查超级用户禁用
            if (
                self.group_data
                and CommonUtils.format(self.plugin.module)
                in self.group_data.superuser_block_plugin
            ):
                if freq.is_send_limit_message(self.plugin, self.group_id, self.is_poke):
                    try:
                        await asyncio.wait_for(
                            send_message(
                                self.session,
                                "超级管理员禁用了该群此功能...",
                                self.group_id,
                            ),
                            timeout=DB_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"发送消息超时: {self.group_id}", LOGGER_COMMAND)
                raise SkipPluginException(
                    f"{self.plugin.name}({self.plugin.module})"
                    f" 超级管理员禁用了该群此功能..."
                )

            # 检查普通禁用
            if (
                self.group_data
                and CommonUtils.format(self.plugin.module)
                in self.group_data.block_plugin
            ):
                if freq.is_send_limit_message(self.plugin, self.group_id, self.is_poke):
                    try:
                        await asyncio.wait_for(
                            send_message(
                                self.session, "该群未开启此功能...", self.group_id
                            ),
                            timeout=DB_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"发送消息超时: {self.group_id}", LOGGER_COMMAND)
                raise SkipPluginException(
                    f"{self.plugin.name}({self.plugin.module}) 未开启此功能..."
                )

            # 检查全局禁用
            if self.plugin.block_type == BlockType.GROUP:
                if freq.is_send_limit_message(self.plugin, self.group_id, self.is_poke):
                    try:
                        await asyncio.wait_for(
                            send_message(
                                self.session, "该功能在群组中已被禁用...", self.group_id
                            ),
                            timeout=DB_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"发送消息超时: {self.group_id}", LOGGER_COMMAND)
                raise SkipPluginException(
                    f"{self.plugin.name}({self.plugin.module})该插件在群组中已被禁用..."
                )
        finally:
            # 记录执行时间
            elapsed = time.time() - start_time
            if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
                logger.warning(
                    f"GroupCheck.check 耗时: {elapsed:.3f}s, 群组: {self.group_id}",
                    LOGGER_COMMAND,
                )


class PluginCheck:
    def __init__(self, group: GroupConsole | None, session: Uninfo, is_poke: bool):
        self.session = session
        self.is_poke = is_poke
        self.group_data = group
        self.group_id = None
        if group:
            self.group_id = group.group_id

    async def check_user(self, plugin: PluginInfo):
        """全局私聊禁用检测

        参数:
            plugin: PluginInfo

        异常:
            IgnoredException: 忽略插件
        """
        if plugin.block_type == BlockType.PRIVATE:
            if freq.is_send_limit_message(plugin, self.session.user.id, self.is_poke):
                try:
                    await asyncio.wait_for(
                        send_message(self.session, "该功能在私聊中已被禁用..."),
                        timeout=DB_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.error("发送消息超时", LOGGER_COMMAND)
            raise SkipPluginException(
                f"{plugin.name}({plugin.module}) 该插件在私聊中已被禁用..."
            )

    async def check_global(self, plugin: PluginInfo):
        """全局状态

        参数:
            plugin: PluginInfo

        异常:
            IgnoredException: 忽略插件
        """
        start_time = time.time()
        try:
            if plugin.status or plugin.block_type != BlockType.ALL:
                return
            """全局状态"""
            if self.group_data and self.group_data.is_super:
                raise IsSuperuserException()

            sid = self.group_id or self.session.user.id
            if freq.is_send_limit_message(plugin, sid, self.is_poke):
                try:
                    await asyncio.wait_for(
                        send_message(self.session, "全局未开启此功能...", sid),
                        timeout=DB_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.error(f"发送消息超时: {sid}", LOGGER_COMMAND)
            raise SkipPluginException(
                f"{plugin.name}({plugin.module}) 全局未开启此功能..."
            )
        finally:
            # 记录执行时间
            elapsed = time.time() - start_time
            if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
                logger.warning(
                    f"PluginCheck.check_global 耗时: {elapsed:.3f}s", LOGGER_COMMAND
                )


async def auth_plugin(
    plugin: PluginInfo, group: GroupConsole | None, session: Uninfo, event: Event
):
    """插件状态

    参数:
        plugin: PluginInfo
        session: Uninfo
        event: Event
    """
    start_time = time.time()
    try:
        is_poke_event = is_poke(event)
        user_check = PluginCheck(group, session, is_poke_event)

        tasks = []
        if group:
            tasks.append(GroupCheck(plugin, group, session, is_poke_event).check())
        else:
            tasks.append(user_check.check_user(plugin))
        tasks.append(user_check.check_global(plugin))

        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks), timeout=DB_TIMEOUT_SECONDS * 2
            )
        except asyncio.TimeoutError:
            logger.error("插件用户/群组/全局检查超时...", LOGGER_COMMAND)

    finally:
        # 记录总执行时间
        elapsed = time.time() - start_time
        if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
            logger.warning(
                f"auth_plugin 总耗时: {elapsed:.3f}s, 模块: {plugin.module}",
                LOGGER_COMMAND,
            )
