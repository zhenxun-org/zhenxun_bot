from tortoise.functions import Count

from zhenxun.models.group_console import GroupConsole
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.statistics import Statistics
from zhenxun.utils.echart_utils import ChartUtils
from zhenxun.utils.echart_utils.models import Barh
from zhenxun.utils.enum import PluginType
from zhenxun.utils.image_utils import BuildImage
from zhenxun.utils.time_utils import TimeUtils


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
        day = None
        day_type = ""
        if search_type == "day":
            day = 1
            day_type = "日"
        elif search_type == "month":
            day = 30
            day_type = "月"
        elif search_type == "week":
            day = 7
            day_type = "周"
        if day_type:
            day_type += f"({day}天)"
        title = ""
        if user_id:
            """查用户"""
            query = GroupInfoUser.filter(user_id=user_id)
            if group_id:
                query = query.filter(group_id=group_id)
            user = await query.first()
            title = f"{user.user_name if user else user_id} {day_type}功能调用统计"
        elif group_id:
            """查群组"""
            group = await GroupConsole.get_group(group_id=group_id)
            title = f"{group.group_name if group else group_id} {day_type}功能调用统计"
        else:
            title = "功能调用统计"
        if is_global and not user_id:
            title = f"全局 {title}"
            return await cls.get_global_statistics(plugin_name, day, title)
        if user_id:
            return await cls.get_my_statistics(user_id, group_id, day, title)
        if group_id:
            return await cls.get_group_statistics(group_id, day, title)
        return None

    @classmethod
    async def get_global_statistics(
        cls, plugin_name: str | None, day: int | None, title: str
    ) -> BuildImage | str:
        query = Statistics
        if plugin_name:
            query = query.filter(plugin_name=plugin_name)
        if day:
            query = query.filter(create_time__gte=TimeUtils.get_day_start())
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
        cls, user_id: str, group_id: str | None, day: int | None, title: str
    ):
        query = Statistics.filter(user_id=user_id)
        if group_id:
            query = query.filter(group_id=group_id)
        if day:
            query = query.filter(create_time__gte=TimeUtils.get_day_start())
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
    async def get_group_statistics(cls, group_id: str, day: int | None, title: str):
        query = Statistics.filter(group_id=group_id)
        if day:
            query = query.filter(create_time__gte=TimeUtils.get_day_start())
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
    async def __build_image(cls, data_list: list[tuple[str, int]], title: str):
        module2count = {x[0]: x[1] for x in data_list}
        plugin_info = await PluginInfo.filter(
            module__in=module2count.keys(),
            load_status=True,
            plugin_type=PluginType.NORMAL,
        ).all()
        x_index = []
        data = []
        for plugin in plugin_info:
            x_index.append(plugin.name)
            data.append(module2count.get(plugin.module, 0))
        barh = Barh(data=data, category_data=x_index, title=title)
        return await ChartUtils.barh(barh)
