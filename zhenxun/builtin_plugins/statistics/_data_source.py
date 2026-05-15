from dataclasses import dataclass
from datetime import datetime, timedelta

from tortoise.functions import Count

from zhenxun.models.group_console import GroupConsole
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.statistics import Statistics
from zhenxun.utils.echart_utils import ChartUtils
from zhenxun.utils.echart_utils.models import Barh
from zhenxun.utils.enum import PluginType
from zhenxun.utils.time_utils import TimeUtils


@dataclass(frozen=True)
class _StatisticsPeriod:
    title: str
    start_time: datetime | None


def _get_statistics_period(search_type: str | None) -> _StatisticsPeriod:
    if search_type == "day":
        return _StatisticsPeriod("日(1天)", TimeUtils.get_day_start())
    if search_type == "week":
        return _StatisticsPeriod(
            "周(7天)",
            TimeUtils.get_day_start(
                datetime.now(TimeUtils.DEFAULT_TIMEZONE) - timedelta(days=6)
            ),
        )
    if search_type == "month":
        return _StatisticsPeriod(
            "月(30天)",
            TimeUtils.get_day_start(
                datetime.now(TimeUtils.DEFAULT_TIMEZONE) - timedelta(days=29)
            ),
        )
    return _StatisticsPeriod("", None)


def _build_statistics_title(
    *,
    target_name: str | None,
    is_global: bool,
    period_title: str,
) -> str:
    title = f"{period_title}功能调用统计" if period_title else "功能调用统计"
    prefixes: list[str] = []
    if target_name:
        prefixes.append(target_name)
    if is_global:
        prefixes.append("全局")
    if prefixes:
        return f"{' '.join(prefixes)} {title}"
    return title


class StatisticsManage:
    @classmethod
    async def get_statistics(
        cls,
        plugin_name: str | None,
        is_global: bool,
        search_type: str | None,
        user_id: str | None = None,
        group_id: str | None = None,
    ):
        period = _get_statistics_period(search_type)
        if user_id:
            """查用户"""
            query = GroupInfoUser.filter(user_id=user_id)
            if group_id:
                query = query.filter(group_id=group_id)
            user = await query.first()
            title = _build_statistics_title(
                target_name=user.user_name if user else user_id,
                is_global=is_global and not group_id,
                period_title=period.title,
            )
        elif group_id:
            """查群组"""
            group = await GroupConsole.get_group(group_id=group_id)
            title = _build_statistics_title(
                target_name=group.group_name if group else group_id,
                is_global=False,
                period_title=period.title,
            )
        else:
            title = _build_statistics_title(
                target_name=None,
                is_global=is_global,
                period_title=period.title,
            )
        if is_global and not user_id:
            return await cls.get_global_statistics(
                plugin_name, period.start_time, title
            )
        if user_id:
            return await cls.get_my_statistics(
                user_id, group_id, period.start_time, title
            )
        if group_id:
            return await cls.get_group_statistics(group_id, period.start_time, title)
        return None

    @classmethod
    async def get_global_statistics(
        cls, plugin_name: str | None, start_time: datetime | None, title: str
    ) -> bytes | str:
        query = Statistics
        if plugin_name:
            query = query.filter(plugin_name=plugin_name)
        if start_time:
            query = query.filter(create_time__gte=start_time)
        data_list = (
            await query.annotate(count=Count("id"))
            .group_by("plugin_name")
            .values_list("plugin_name", "count")
        )
        return (
            await cls.__build_image(data_list, title)
            if data_list
            else "统计数据为空..."
        )

    @classmethod
    async def get_my_statistics(
        cls,
        user_id: str,
        group_id: str | None,
        start_time: datetime | None,
        title: str,
    ):
        query = Statistics.filter(user_id=user_id)
        if group_id:
            query = query.filter(group_id=group_id)
        if start_time:
            query = query.filter(create_time__gte=start_time)
        data_list = (
            await query.annotate(count=Count("id"))
            .group_by("plugin_name")
            .values_list("plugin_name", "count")
        )
        return (
            await cls.__build_image(data_list, title)
            if data_list
            else "统计数据为空..."
        )

    @classmethod
    async def get_group_statistics(
        cls, group_id: str, start_time: datetime | None, title: str
    ):
        query = Statistics.filter(group_id=group_id)
        if start_time:
            query = query.filter(create_time__gte=start_time)
        data_list = (
            await query.annotate(count=Count("id"))
            .group_by("plugin_name")
            .values_list("plugin_name", "count")
        )
        return (
            await cls.__build_image(data_list, title)
            if data_list
            else "统计数据为空..."
        )

    @classmethod
    async def __build_image(cls, data_list: list[tuple[str, int]], title: str) -> bytes:
        module2count = {x[0]: x[1] for x in data_list}
        plugin_info = await PluginInfo.get_plugins(
            module__in=list(module2count.keys()),
            load_status=True,
            filter_parent=False,
            plugin_type=PluginType.NORMAL,
        )
        x_index = []
        data = []
        for plugin in plugin_info:
            x_index.append(plugin.name)
            data.append(module2count.get(plugin.module, 0))
        barh = Barh(data=data, category_data=x_index, title=title)
        return await ChartUtils.barh(barh)
