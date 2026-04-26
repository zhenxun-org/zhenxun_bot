import time

from nonebot.adapters import Event
from nonebot_plugin_uninfo import Uninfo

from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.cache.runtime_cache import GroupSnapshot, _parse_block_modules
from zhenxun.services.log import logger
from zhenxun.utils.enum import BlockType

from .config import LOGGER_COMMAND, WARNING_THRESHOLD
from .context import PermissionContext
from .exception import IsSuperuserException, SkipPluginException
from .utils import freq, is_poke


def _get_group_block_sets(
    group: GroupConsole | GroupSnapshot,
) -> tuple[frozenset[str], frozenset[str]]:
    block_set = getattr(group, "block_plugin_set", None)
    super_block_set = getattr(group, "superuser_block_plugin_set", None)
    if block_set is None:
        block_set = _parse_block_modules(getattr(group, "block_plugin", "") or "")
        setattr(group, "block_plugin_set", block_set)
    if super_block_set is None:
        super_block_set = _parse_block_modules(
            getattr(group, "superuser_block_plugin", "") or ""
        )
        setattr(group, "superuser_block_plugin_set", super_block_set)
    return block_set, super_block_set


class GroupCheck:
    def __init__(
        self,
        plugin: PluginInfo,
        group: GroupConsole | GroupSnapshot,
        session: Uninfo,
        is_poke: bool,
        skip_group_block: bool,
    ) -> None:
        self.session = session
        self.is_poke = is_poke
        self.plugin = plugin
        self.group_data = group
        self.group_id = group.group_id
        self.skip_group_block = skip_group_block
        (
            self.block_plugin_set,
            self.superuser_block_plugin_set,
        ) = _get_group_block_sets(group)

    async def check(self):
        start_time = time.time()
        try:
            if not self.skip_group_block:
                # 检查超级用户禁用
                if (
                    self.group_data
                    and self.plugin.module in self.superuser_block_plugin_set
                ):
                    should_tip = freq.is_send_limit_message(
                        self.plugin, self.group_id, self.is_poke
                    )
                    raise SkipPluginException(
                        f"{self.plugin.name}({self.plugin.module})"
                        f" 超级管理员禁用了该群此功能...",
                        tip_message=(
                            "超级管理员禁用了该群此功能..." if should_tip else None
                        ),
                        tip_check_tag=self.group_id if should_tip else None,
                        tip_background=should_tip,
                    )

                # 检查普通禁用
                if self.group_data and self.plugin.module in self.block_plugin_set:
                    should_tip = freq.is_send_limit_message(
                        self.plugin, self.group_id, self.is_poke
                    )
                    raise SkipPluginException(
                        f"{self.plugin.name}({self.plugin.module}) 未开启此功能...",
                        tip_message="该群未开启此功能..." if should_tip else None,
                        tip_check_tag=self.group_id if should_tip else None,
                        tip_background=should_tip,
                    )

            # 检查全局禁用
            if self.plugin.block_type == BlockType.GROUP:
                should_tip = freq.is_send_limit_message(
                    self.plugin, self.group_id, self.is_poke
                )
                raise SkipPluginException(
                    f"{self.plugin.name}({self.plugin.module})该插件在群组中已被禁用...",
                    tip_message="该功能在群组中已被禁用..." if should_tip else None,
                    tip_check_tag=self.group_id if should_tip else None,
                    tip_background=should_tip,
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
    def __init__(
        self,
        group: GroupConsole | GroupSnapshot | None,
        session: Uninfo,
        is_poke: bool,
        user_id: str | None,
    ):
        self.session = session
        self.is_poke = is_poke
        self.group_data = group
        self.user_id = user_id or session.user.id
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
            should_tip = freq.is_send_limit_message(plugin, self.user_id, self.is_poke)
            raise SkipPluginException(
                f"{plugin.name}({plugin.module}) 该插件在私聊中已被禁用...",
                tip_message="该功能在私聊中已被禁用..." if should_tip else None,
                tip_check_tag=self.user_id if should_tip else None,
                tip_background=should_tip,
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

            sid = self.group_id or self.user_id
            should_tip = freq.is_send_limit_message(plugin, sid, self.is_poke)
            raise SkipPluginException(
                f"{plugin.name}({plugin.module}) 全局未开启此功能...",
                tip_message="全局未开启此功能..." if should_tip else None,
                tip_check_tag=sid if should_tip else None,
                tip_background=should_tip,
            )
        finally:
            # 记录执行时间
            elapsed = time.time() - start_time
            if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
                logger.warning(
                    f"PluginCheck.check_global 耗时: {elapsed:.3f}s", LOGGER_COMMAND
                )


async def auth_plugin(
    plugin: PluginInfo,
    group: GroupConsole | GroupSnapshot | None,
    session: Uninfo,
    event: Event,
    *,
    context: PermissionContext | None = None,
    skip_group_block: bool = False,
    user_id: str | None = None,
):
    """插件状态

    参数:
        plugin: PluginInfo
        session: Uninfo
        event: Event
    """
    start_time = time.time()
    try:
        if context is not None:
            group = context.group or group
            user_id = context.user_id
        is_poke_event = is_poke(event)
        user_check = PluginCheck(group, session, is_poke_event, user_id)

        if group:
            block_set, super_block_set = _get_group_block_sets(group)
            if (
                plugin.status
                and plugin.block_type != BlockType.GROUP
                and not block_set
                and not super_block_set
            ):
                return
            await GroupCheck(
                plugin, group, session, is_poke_event, skip_group_block
            ).check()
        else:
            await user_check.check_user(plugin)
        await user_check.check_global(plugin)

    finally:
        # 记录总执行时间
        elapsed = time.time() - start_time
        if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
            logger.warning(
                f"auth_plugin 总耗时: {elapsed:.3f}s, 模块: {plugin.module}",
                LOGGER_COMMAND,
            )
