from datetime import datetime, timedelta
from pathlib import Path
import time

import nonebot
from nonebot.adapters import Bot
from nonebot.drivers import Driver
from tortoise.functions import Count

from zhenxun.models.bot_connect_log import BotConnectLog
from zhenxun.models.bot_console import BotConsole
from zhenxun.models.chat_history import ChatHistory
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.statistics import Statistics
from zhenxun.models.task_info import TaskInfo
from zhenxun.services.log import logger
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.enum import PluginType
from zhenxun.utils.platform import PlatformUtils

from ....config import AVA_URL, GROUP_AVA_URL, QueryDateType
from .model import (
    ActiveGroup,
    BaseInfo,
    BotBlockModule,
    HotPlugin,
    QueryCount,
    TemplateBaseInfo,
)

driver: Driver = nonebot.get_driver()


class BotLive:
    def __init__(self):
        self._data = {}

    def add(self, bot_id: str):
        self._data[bot_id] = int(time.time())

    def get(self, bot_id: str) -> int | None:
        return self._data.get(bot_id)

    def remove(self, bot_id: str):
        if bot_id in self._data:
            del self._data[bot_id]


bot_live = BotLive()


@driver.on_bot_connect
async def _(bot: Bot):
    bot_live.add(bot.self_id)


@driver.on_bot_disconnect
async def _(bot: Bot):
    bot_live.remove(bot.self_id)


class ApiDataSource:
    @classmethod
    async def __build_bot_info(cls, bot: Bot) -> TemplateBaseInfo:
        """构建bot信息

        参数:
            bot: bot实例

        返回:
            TemplateBaseInfo: bot信息
        """
        login_info = None
        try:
            login_info = await bot.get_login_info()
        except Exception as e:
            logger.warning("调用接口get_login_info失败", "WebUi", e=e)
        return TemplateBaseInfo(
            bot=bot,
            self_id=bot.self_id,
            nickname=login_info["nickname"] if login_info else bot.self_id,
            ava_url=AVA_URL.format(bot.self_id),
        )

    @classmethod
    def __get_bot_version(cls) -> str:
        """获取bot版本

        返回:
            str | None: 版本
        """
        version_file = Path() / "__version__"
        if version_file.exists():
            if text := version_file.open(encoding="utf-8").read():
                return text.replace("__version__: ", "").strip()
        return "unknown"

    @classmethod
    async def __init_bot_base_data(cls, select_bot: TemplateBaseInfo):
        """初始化bot的基础数据

        参数:
            select_bot: bot
        """
        now = datetime.now()
        # 今日累计接收消息
        select_bot.received_messages = await ChatHistory.filter(
            bot_id=select_bot.self_id,
            create_time__gte=now - timedelta(hours=now.hour),
        ).count()
        # 群聊数量
        try:
            select_bot.group_count = len(
                (await PlatformUtils.get_group_list(select_bot.bot, True))[0]
            )
            # 好友数量
            select_bot.friend_count = len(
                (await PlatformUtils.get_friend_list(select_bot.bot))[0]
            )
        except Exception as e:
            logger.warning("获取bot好友/群组信息失败...", "WebUi", e=e)
            select_bot.group_count = 0
            select_bot.friend_count = 0
        select_bot.status = await BotConsole.get_bot_status(select_bot.self_id)
        # 连接时间
        select_bot.connect_time = bot_live.get(select_bot.self_id) or 0
        if select_bot.connect_time:
            connect_date = datetime.fromtimestamp(select_bot.connect_time)
            select_bot.connect_date = connect_date.strftime("%Y-%m-%d %H:%M:%S")
        select_bot.version = cls.__get_bot_version()
        day_call = await Statistics.filter(
            create_time__gte=now - timedelta(hours=now.hour)
        ).count()
        select_bot.day_call = day_call
        select_bot.connect_count = await BotConnectLog.filter(
            bot_id=select_bot.self_id
        ).count()

    @classmethod
    async def get_base_info(cls, bot_id: str | None) -> list[BaseInfo] | None:
        """获取bot信息

        参数:
            bot_id: bot id

        返回:
            list[BaseInfo] | None: bot列表
        """
        bots = nonebot.get_bots()
        if not bots:
            return None
        select_bot: BaseInfo
        bot_list = [await cls.__build_bot_info(bot) for _, bot in bots.items()]
        # 获取指定qq号的bot信息，若无指定   则获取第一个
        if _bl := [b for b in bot_list if b.self_id == bot_id]:
            select_bot = _bl[0]
        else:
            select_bot = bot_list[0]
        await cls.__init_bot_base_data(select_bot)
        for bot in bot_list:
            bot.bot = None  # type: ignore
        select_bot.is_select = True
        return [BaseInfo(**e.to_dict()) for e in bot_list]

    @classmethod
    async def get_all_chat_count(cls, bot_id: str | None) -> QueryCount:
        """获取年/月/周/日聊天次数

        参数:
            bot_id: bot id

        返回:
            QueryCount: 数据内容
        """
        now = datetime.now()
        query = ChatHistory
        if bot_id:
            query = query.filter(bot_id=bot_id)
        all_count = await query.annotate().count()
        day_count = await query.filter(
            create_time__gte=now - timedelta(hours=now.hour, minutes=now.minute)
        ).count()
        week_count = await query.filter(
            create_time__gte=now - timedelta(days=7, hours=now.hour, minutes=now.minute)
        ).count()
        month_count = await query.filter(
            create_time__gte=now
            - timedelta(days=30, hours=now.hour, minutes=now.minute)
        ).count()
        year_count = await query.filter(
            create_time__gte=now
            - timedelta(days=365, hours=now.hour, minutes=now.minute)
        ).count()
        return QueryCount(
            num=all_count,
            day=day_count,
            week=week_count,
            month=month_count,
            year=year_count,
        )

    @classmethod
    async def get_all_call_count(cls, bot_id: str | None) -> QueryCount:
        """获取年/月/周/日调用次数

        参数:
            bot_id: bot id

        返回:
            QueryCount: 数据内容
        """
        now = datetime.now()
        query = Statistics
        if bot_id:
            query = query.filter(bot_id=bot_id)
        all_count = await query.annotate().count()
        day_count = await query.filter(
            create_time__gte=now - timedelta(hours=now.hour, minutes=now.minute)
        ).count()
        week_count = await query.filter(
            create_time__gte=now - timedelta(days=7, hours=now.hour, minutes=now.minute)
        ).count()
        month_count = await query.filter(
            create_time__gte=now
            - timedelta(days=30, hours=now.hour, minutes=now.minute)
        ).count()
        year_count = await query.filter(
            create_time__gte=now
            - timedelta(days=365, hours=now.hour, minutes=now.minute)
        ).count()
        return QueryCount(
            num=all_count,
            day=day_count,
            week=week_count,
            month=month_count,
            year=year_count,
        )

    @classmethod
    def __get_query(
        cls,
        base_query: type[ChatHistory | Statistics],
        date_type: QueryDateType | None = None,
        bot_id: str | None = None,
    ):
        """构建日期查询条件

        参数:
            date_type: 日期类型.
            bot_id: bot id.
        """
        query = base_query
        now = datetime.now()
        if bot_id:
            query = query.filter(bot_id=bot_id)
        if date_type == QueryDateType.DAY:
            query = query.filter(
                create_time__gte=now
                - timedelta(hours=now.hour, minutes=now.minute, seconds=now.second)
            )
        if date_type == QueryDateType.WEEK:
            query = query.filter(
                create_time__gte=now
                - timedelta(
                    days=7, hours=now.hour, minutes=now.minute, seconds=now.second
                )
            )
        if date_type == QueryDateType.MONTH:
            query = query.filter(
                create_time__gte=now
                - timedelta(
                    days=30, hours=now.hour, minutes=now.minute, seconds=now.second
                )
            )
        if date_type == QueryDateType.YEAR:
            query = query.filter(
                create_time__gte=now
                - timedelta(
                    days=365, hours=now.hour, minutes=now.minute, seconds=now.second
                )
            )
        return query

    @classmethod
    async def get_active_group(
        cls, date_type: QueryDateType | None = None, bot_id: str | None = None
    ) -> list[ActiveGroup]:
        """获取活跃群组

        参数:
            date_type: 日期类型.
            bot_id: bot id.

        返回:
            list[ActiveGroup]: 活跃群组列表
        """
        query = cls.__get_query(ChatHistory, date_type, bot_id)
        data_list = (
            await query.annotate(count=Count("id"))
            .filter(group_id__not_isnull=True)
            .group_by("group_id")
            .order_by("-count")
            .limit(5)
            .values_list("group_id", "count")
        )
        id2name = {}
        if data_list:
            if info_list := await GroupConsole.filter(
                group_id__in=[x[0] for x in data_list]
            ).all():
                for group_info in info_list:
                    id2name[group_info.group_id] = group_info.group_name
        active_group_list = [
            ActiveGroup(
                group_id=data[0],
                name=id2name.get(data[0]) or data[0],
                chat_num=data[1],
                ava_img=GROUP_AVA_URL.format(data[0], data[0]),
            )
            for data in data_list
        ]
        active_group_list = sorted(
            active_group_list, key=lambda x: x.chat_num, reverse=True
        )
        if len(active_group_list) > 5:
            active_group_list = active_group_list[:5]
        return active_group_list

    @classmethod
    async def get_hot_plugin(
        cls, date_type: QueryDateType | None = None, bot_id: str | None = None
    ) -> list[HotPlugin]:
        """获取热门插件

        参数:
            date_type: 日期类型.
            bot_id: bot id.

        返回:
            list[HotPlugin]: 热门插件列表
        """
        query = cls.__get_query(Statistics, date_type, bot_id)
        data_list = (
            await query.annotate(count=Count("id"))
            .group_by("plugin_name")
            .order_by("-count")
            .limit(5)
            .values_list("plugin_name", "count")
        )
        hot_plugin_list = []
        module_list = [x[0] for x in data_list]
        plugins = await PluginInfo.filter(module__in=module_list).all()
        module2name = {p.module: p.name for p in plugins}
        for data in data_list:
            module = data[0]
            name = module2name.get(module) or module
            hot_plugin_list.append(HotPlugin(module=module, name=name, count=data[1]))
        hot_plugin_list = sorted(hot_plugin_list, key=lambda x: x.count, reverse=True)
        if len(hot_plugin_list) > 5:
            hot_plugin_list = hot_plugin_list[:5]
        return hot_plugin_list

    @classmethod
    async def get_bot_block_module(cls, bot_id: str) -> BotBlockModule | None:
        """获取bot层面的禁用模块

        参数:
            bot_id: bot id

        返回:
            BotBlockModule | None: 数据内容
        """
        bot_data = await BotConsole.get_or_none(bot_id=bot_id)
        if not bot_data:
            return None
        block_tasks = []
        block_plugins = []
        all_plugins = await PluginInfo.filter(
            load_status=True, plugin_type=PluginType.NORMAL
        ).values("module", "name")
        all_task = await TaskInfo.annotate().values("module", "name")
        if bot_data.block_tasks:
            tasks = CommonUtils.convert_module_format(bot_data.block_tasks)
            block_tasks = [t["module"] for t in all_task if t["module"] in tasks]
        if bot_data.block_plugins:
            plugins = CommonUtils.convert_module_format(bot_data.block_plugins)
            block_plugins = [t["module"] for t in all_plugins if t["module"] in plugins]
        return BotBlockModule(
            bot_id=bot_id,
            block_tasks=block_tasks,
            block_plugins=block_plugins,
            all_plugins=all_plugins,
            all_tasks=all_task,
        )
