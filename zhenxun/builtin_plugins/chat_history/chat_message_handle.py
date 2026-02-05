from datetime import datetime, timedelta
from typing import cast

from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Arparma,
    Match,
    Option,
    Query,
    on_alconna,
    store_true,
)
from nonebot_plugin_session import EventSession
import pytz

from zhenxun import ui
from zhenxun.configs.config import Config
from zhenxun.configs.utils import Command, PluginExtraData, RegisterConfig
from zhenxun.models.chat_history import ChatHistory
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.services import avatar_service
from zhenxun.services.log import logger
from zhenxun.ui.models import ImageCell, TextCell
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

__plugin_meta__ = PluginMetadata(
    name="消息统计",
    description="消息统计查询",
    usage="""
    格式:
    消息排行 ?[type [日,周,月,季,年]] ?[--des]

    快捷:
    [日,周,月,季,年]消息排行 ?[数量]

    示例:
    消息排行             : 所有记录排行
    日消息排行           : 今日记录排行
    周消息排行           : 本周记录排行
    月消息排行           : 本月记录排行
    季消息排行           : 本季度记录排行
    年消息排行           : 本年记录排行
    消息排行 周 --des    : 逆序周记录排行
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.2",
        plugin_type=PluginType.NORMAL,
        menu_type="数据统计",
        commands=[
            Command(command="消息统计"),
            Command(command="日消息统计"),
            Command(command="周消息排行"),
            Command(command="月消息排行"),
            Command(command="季消息排行"),
            Command(command="年消息排行"),
        ],
        configs=[
            RegisterConfig(
                module="chat_history",
                key="SHOW_QUIT_MEMBER",
                value=True,
                help="是否在消息排行中显示已退群用户",
                default_value=True,
                type=bool,
            )
        ],
    ).to_dict(),
)


_matcher = on_alconna(
    Alconna(
        "消息排行",
        Option("--des", action=store_true, help_text="逆序"),
        Args["type?", ["日", "周", "月", "季", "年"]]["count?", int, 10],
    ),
    aliases={"消息统计"},
    priority=5,
    block=True,
)

_matcher.shortcut(
    r"(?P<type>['日', '周', '月', '季', '年'])?消息(排行|统计)\s?(?P<cnt>\d+)?",
    command="消息排行",
    arguments=["{type}", "{cnt}"],
    prefix=True,
)


@_matcher.handle()
async def _(
    session: EventSession,
    arparma: Arparma,
    type: Match[str],
    count: Query[int] = Query("count", 10),
):
    group_id = session.id3 or session.id2
    time_now = datetime.now()
    date_scope = None
    zero_today = time_now - timedelta(
        hours=time_now.hour, minutes=time_now.minute, seconds=time_now.second
    )
    date = type.result if type.available else None
    if date:
        if date in ["日"]:
            date_scope = (zero_today, time_now)
        elif date in ["周"]:
            date_scope = (time_now - timedelta(days=7), time_now)
        elif date in ["月"]:
            date_scope = (time_now - timedelta(days=30), time_now)
        elif date in ["季"]:
            date_scope = (time_now - timedelta(days=90), time_now)
    column_name = ["名次", "头像", "昵称", "发言次数"]
    show_quit_member = Config.get_config("chat_history", "SHOW_QUIT_MEMBER", True)

    fetch_count = count.result
    if not show_quit_member:
        fetch_count = count.result * 2

    raw_rank_data = await ChatHistory.get_group_msg_rank(
        group_id, fetch_count, "DES" if arparma.find("des") else "DESC", date_scope
    )

    if raw_rank_data:
        rank_data = cast(list[tuple[str, int]], raw_rank_data)
        rows_data = []
        platform = "qq"

        user_ids_in_rank = [str(uid) for uid, _ in rank_data]
        users_in_group_query = GroupInfoUser.filter(
            user_id__in=user_ids_in_rank, group_id=group_id
        )
        users_in_group = {u.user_id: u for u in await users_in_group_query}

        for idx, (uid, num) in enumerate(rank_data):
            if len(rows_data) >= count.result:
                break

            uid_str = str(uid)
            user_in_group = users_in_group.get(uid_str)

            if not user_in_group and not show_quit_member:
                continue

            user_name = (
                user_in_group.user_name if user_in_group else f"{uid_str}(已退群)"
            )

            avatar_path = await avatar_service.get_avatar_path(platform, uid_str)

            rows_data.append(
                [
                    TextCell(content=str(len(rows_data) + 1)),
                    ImageCell(
                        src=avatar_path.as_uri() if avatar_path else "", shape="circle"
                    ),
                    TextCell(content=user_name),
                    TextCell(content=str(num), bold=True),
                ]
            )
        if not date_scope:
            first_msg_time = await ChatHistory.get_group_first_msg_datetime(group_id)
            if first_msg_time:
                date_scope_start = first_msg_time.astimezone(
                    pytz.timezone("Asia/Shanghai")
                ).replace(microsecond=0)
                date_str = f"{str(date_scope_start).split('+')[0]} - 至今"
            else:
                date_str = f"{time_now.replace(microsecond=0)} - 至今"
        else:
            date_str = (
                f"{date_scope[0].replace(microsecond=0)} - "
                f"{date_scope[1].replace(microsecond=0)}"
            )

        table = ui.table(f"消息排行({count.result})", date_str)
        table.set_headers(column_name).add_rows(rows_data)

        image_bytes = await ui.render(table)

        logger.info(
            f"查看消息排行 数量={count.result}", arparma.header_result, session=session
        )
        await MessageUtils.build_message(image_bytes).finish(reply_to=True)
    await MessageUtils.build_message("群组消息记录为空...").finish()
