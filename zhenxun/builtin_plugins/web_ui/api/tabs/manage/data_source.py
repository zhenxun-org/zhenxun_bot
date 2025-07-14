import nonebot
from tortoise.functions import Count

from zhenxun.models.ban_console import BanConsole
from zhenxun.models.chat_history import ChatHistory
from zhenxun.models.fg_request import FgRequest
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.statistics import Statistics
from zhenxun.models.task_info import TaskInfo
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.enum import RequestType
from zhenxun.utils.platform import PlatformUtils

from ....config import AVA_URL, GROUP_AVA_URL
from .model import (
    FriendRequestResult,
    GroupDetail,
    GroupRequestResult,
    Plugin,
    ReqResult,
    Task,
    UpdateGroup,
    UserDetail,
)


class ApiDataSource:
    @classmethod
    async def update_group(cls, group: UpdateGroup):
        """更新群组数据

        参数:
            group: UpdateGroup
        """
        db_group = await GroupConsole.get_group(group.group_id) or GroupConsole(
            group_id=group.group_id
        )
        task_list = await TaskInfo.all().values_list("module", flat=True)
        db_group.level = group.level
        db_group.status = group.status
        if group.close_plugins:
            db_group.block_plugin = CommonUtils.convert_module_format(
                group.close_plugins
            )
        else:
            db_group.block_plugin = ""
        if group.task:
            if block_task := [t for t in task_list if t not in group.task]:
                db_group.block_task = CommonUtils.convert_module_format(block_task)  # type: ignore
        else:
            db_group.block_task = CommonUtils.convert_module_format(task_list)  # type: ignore
        await db_group.save()

    @classmethod
    async def get_request_list(cls) -> ReqResult:
        """获取好友与群组请求列表

        返回:
            ReqResult: 数据内容
        """
        req_result = ReqResult()
        data_list = await FgRequest.filter(handle_type__isnull=True).all()
        for req in data_list:
            if req.request_type == RequestType.FRIEND:
                req_result.friend.append(
                    FriendRequestResult(
                        oid=req.id,
                        bot_id=req.bot_id,
                        id=req.user_id,
                        flag=req.flag,
                        nickname=req.nickname,
                        comment=req.comment,
                        ava_url=AVA_URL.format(req.user_id),
                        type=str(req.request_type).lower(),
                    )
                )
            else:
                req_result.group.append(
                    GroupRequestResult(
                        oid=req.id,
                        bot_id=req.bot_id,
                        id=req.user_id,
                        flag=req.flag,
                        nickname=req.nickname,
                        comment=req.comment,
                        ava_url=GROUP_AVA_URL.format(req.group_id, req.group_id),
                        type=str(req.request_type).lower(),
                        invite_group=req.group_id,
                        group_name=None,
                    )
                )
        req_result.friend.reverse()
        req_result.group.reverse()
        return req_result

    @classmethod
    async def get_friend_detail(cls, bot_id: str, user_id: str) -> UserDetail | None:
        """获取好友详情

        参数:
            bot_id: bot id
            user_id: 用户id

        返回:
            UserDetail | None: 详情数据
        """
        bot = nonebot.get_bot(bot_id)
        friend_list, _ = await PlatformUtils.get_friend_list(bot)
        fd = [x for x in friend_list if x.user_id == user_id]
        if not fd:
            return None
        like_plugin_list = (
            await Statistics.filter(user_id=user_id)
            .annotate(count=Count("id"))
            .group_by("plugin_name")
            .order_by("-count")
            .limit(5)
            .values_list("plugin_name", "count")
        )
        like_plugin = {}
        module_list = [x[0] for x in like_plugin_list]
        plugins = await PluginInfo.filter(module__in=module_list).all()
        module2name = {p.module: p.name for p in plugins}
        for data in like_plugin_list:
            name = module2name.get(data[0]) or data[0]
            like_plugin[name] = data[1]
        user = fd[0]
        return UserDetail(
            user_id=user_id,
            ava_url=AVA_URL.format(user_id),
            nickname=user.user_name,
            remark="",
            is_ban=await BanConsole.is_ban(user_id),
            chat_count=await ChatHistory.filter(user_id=user_id).count(),
            call_count=await Statistics.filter(user_id=user_id).count(),
            like_plugin=like_plugin,
        )

    @classmethod
    async def __get_group_detail_like_plugin(cls, group_id: str) -> dict[str, int]:
        """获取群组喜爱的插件

        参数:
            group_id: 群组id

        返回:
            dict[str, int]: 插件与调用次数
        """
        like_plugin_list = (
            await Statistics.filter(group_id=group_id)
            .annotate(count=Count("id"))
            .group_by("plugin_name")
            .order_by("-count")
            .limit(5)
            .values_list("plugin_name", "count")
        )
        like_plugin = {}
        plugins = await PluginInfo.get_plugins()
        module2name = {p.module: p.name for p in plugins}
        for data in like_plugin_list:
            name = module2name.get(data[0]) or data[0]
            like_plugin[name] = data[1]
        return like_plugin

    @classmethod
    async def __get_group_detail_disable_plugin(
        cls, group: GroupConsole
    ) -> list[Plugin]:
        """获取群组禁用插件

        参数:
            group: GroupConsole

        返回:
            list[Plugin]: 禁用插件数据列表
        """
        disable_plugins: list[Plugin] = []
        plugins = await PluginInfo.get_plugins()
        module2name = {p.module: p.name for p in plugins}
        if group.block_plugin:
            for module in CommonUtils.convert_module_format(group.block_plugin):
                if module:
                    plugin = Plugin(
                        module=module,
                        plugin_name=module,
                        is_super_block=False,
                    )
                    plugin.plugin_name = module2name.get(module) or module
                    disable_plugins.append(plugin)
        exists_modules = [p.module for p in disable_plugins]
        if group.superuser_block_plugin:
            for module in CommonUtils.convert_module_format(
                group.superuser_block_plugin
            ):
                if module and module not in exists_modules:
                    plugin = Plugin(
                        module=module,
                        plugin_name=module,
                        is_super_block=True,
                    )
                    plugin.plugin_name = module2name.get(module) or module
                    disable_plugins.append(plugin)
        return disable_plugins

    @classmethod
    async def __get_group_detail_task(cls, group: GroupConsole) -> list[Task]:
        """获取群组被动技能状态

        参数:
            group: GroupConsole

        返回:
            list[Task]: 群组被动列表
        """
        all_task = await TaskInfo.annotate().values_list("module", "name")
        task_module2name = {x[0]: x[1] for x in all_task}
        task_list = []
        if group.block_task or group.superuser_block_plugin:
            sbp = CommonUtils.convert_module_format(group.superuser_block_task)
            tasks = CommonUtils.convert_module_format(group.block_task)
            task_list.extend(
                Task(
                    name=task[0],
                    zh_name=task_module2name.get(task[0]) or task[0],
                    status=task[0] not in tasks and task[0] not in sbp,
                    is_super_block=task[0] in sbp,
                )
                for task in all_task
            )
        else:
            task_list.extend(
                Task(
                    name=task[0],
                    zh_name=task_module2name.get(task[0]) or task[0],
                    status=True,
                    is_super_block=False,
                )
                for task in all_task
            )
        return task_list

    @classmethod
    async def get_group_detail(cls, group_id: str) -> GroupDetail | None:
        """获取群组详情

        参数:
            group_id: 群组id

        返回:
            GroupDetail | None: 群组详情数据
        """
        group = await GroupConsole.get_group(group_id=group_id)
        if not group:
            return None
        like_plugin = await cls.__get_group_detail_like_plugin(group_id)
        disable_plugins: list[Plugin] = await cls.__get_group_detail_disable_plugin(
            group
        )
        task_list = await cls.__get_group_detail_task(group)
        return GroupDetail(
            group_id=group_id,
            ava_url=GROUP_AVA_URL.format(group_id, group_id),
            name=group.group_name,
            member_count=group.member_count,
            max_member_count=group.max_member_count,
            chat_count=await ChatHistory.filter(group_id=group_id).count(),
            call_count=await Statistics.filter(group_id=group_id).count(),
            like_plugin=like_plugin,
            level=group.level,
            status=group.status,
            close_plugins=disable_plugins,
            task=task_list,
        )
