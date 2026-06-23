from datetime import datetime, timedelta
import random

from nonebot_plugin_uninfo import Uninfo
from tortoise.expressions import RawSQL
from tortoise.functions import Count

from zhenxun import ui
from zhenxun.models.chat_history import ChatHistory
from zhenxun.models.level_user import LevelUser
from zhenxun.models.sign_user import SignUser
from zhenxun.models.statistics import Statistics
from zhenxun.models.user_console import UserConsole
from zhenxun.services import avatar_service
from zhenxun.services.db_context import with_db_timeout
from zhenxun.services.message_load import is_db_unhealthy
from zhenxun.utils.platform import PlatformUtils

RACE = [
    "龙族",
    "魅魔",
    "森林精灵",
    "血精灵",
    "暗夜精灵",
    "狗头人",
    "狼人",
    "猫人",
    "猪头人",
    "骷髅",
    "僵尸",
    "虫族",
    "人类",
    "天使",
    "恶魔",
    "甲壳虫",
    "猎猫",
    "人鱼",
    "哥布林",
    "地精",
    "泰坦",
    "矮人",
    "山巨人",
    "石巨人",
]

SEX = ["男", "女", "雌", "雄"]

OCC = [
    "猎人",
    "战士",
    "魔法师",
    "狂战士",
    "魔战士",
    "盗贼",
    "术士",
    "牧师",
    "骑士",
    "刺客",
    "游侠",
    "召唤师",
    "圣骑士",
    "魔使",
    "龙骑士",
    "赏金猎手",
    "吟游诗人",
    "德鲁伊",
    "祭司",
    "符文师",
    "狂暴术士",
    "萨满",
    "裁决者",
    "角斗士",
]

lik2level = {
    400: 8,
    270: 7,
    200: 6,
    140: 5,
    90: 4,
    50: 3,
    25: 2,
    10: 1,
    0: 0,
}
_INFO_DB_TIMEOUT = 3.0


async def _read_db(factory, operation: str, default):
    if is_db_unhealthy():
        return default
    try:
        return await with_db_timeout(
            factory(),
            timeout=_INFO_DB_TIMEOUT,
            operation=operation,
            source="my_info",
        )
    except Exception:
        return default


def get_level(impression: float) -> int:
    """获取好感度等级"""
    return next((level for imp, level in lik2level.items() if impression >= imp), 0)


async def get_chat_history(
    user_id: str, group_id: str | None
) -> tuple[list[str], list[int]]:
    """获取用户聊天记录

    参数:
        user_id: 用户id
        group_id: 群id

    返回:
        tuple[list[str], list[int]]: 日期列表, 次数列表

    """
    now = datetime.now()
    filter_date = now - timedelta(days=7)
    date_list = await _read_db(
        lambda: ChatHistory.filter(
            user_id=user_id,
            group_id=group_id,
            create_time__gte=filter_date,
        )
        .annotate(date=RawSQL("DATE(create_time)"), count=Count("id"))
        .group_by("date")
        .values("date", "count"),
        "MyInfo.chat_history_chart",
        [],
    )
    chart_date: list[str] = []
    count_list: list[int] = []
    date2cnt = {str(item["date"]): item["count"] for item in date_list}
    current_date = now.date()
    for _ in range(7):
        date_str = str(current_date)
        count_list.append(date2cnt.get(date_str, 0))
        chart_date.append(date_str[5:])
        current_date -= timedelta(days=1)
    chart_date.reverse()
    count_list.reverse()
    return chart_date, count_list


async def get_user_info(
    session: Uninfo, user_id: str, group_id: str | None, nickname: str
) -> bytes:
    """获取用户个人信息

    参数:
        session: Uninfo
        user_id: 用户id
        group_id: 群id
        nickname: 用户昵称

    返回:
        bytes: 图片数据
    """
    platform = PlatformUtils.get_platform(session) or "qq"
    avatar_path = await avatar_service.get_avatar_path(platform, user_id)
    avatar_url = avatar_path.as_uri() if avatar_path else ""

    user = await _read_db(
        lambda: UserConsole.get_user(user_id, platform),
        "MyInfo.user_console",
        None,
    )
    permission_level = await _read_db(
        lambda: LevelUser.get_user_level(user_id, group_id),
        "MyInfo.level_user",
        0,
    )

    sign_level = 0
    if sign_user := await _read_db(
        lambda: SignUser.get_or_none(user_id=user_id),
        "MyInfo.sign_user",
        None,
    ):
        sign_level = get_level(float(sign_user.impression))

    chat_count = await _read_db(
        lambda: ChatHistory.filter(user_id=user_id, group_id=group_id).count(),
        "MyInfo.chat_count",
        0,
    )
    stat_count = await _read_db(
        lambda: Statistics.filter(user_id=user_id, group_id=group_id).count(),
        "MyInfo.stat_count",
        0,
    )

    selected_indices = [""] * 9
    selected_indices[sign_level] = "select"

    uid = f"{getattr(user, 'uid', 0)}".rjust(8, "0")
    uid_formatted = f"{uid[:4]} {uid[4:]}"

    now = datetime.now()
    weather_icon_name = "moon" if now.hour < 6 or now.hour > 19 else "sun"

    chart_labels, chart_data = await get_chat_history(user_id, group_id)

    profile_data = {
        "page": {
            "date": str(now.date()),
            "weather_icon_name": weather_icon_name,
        },
        "info": {
            "avatar_url": avatar_url,
            "nickname": nickname,
            "title": "勇 者",
            "race": random.choice(RACE),
            "sex": random.choice(SEX),
            "occupation": random.choice(OCC),
            "uid": uid_formatted,
            "description": (
                "这是一个传奇的故事,人类的赞歌是勇气的赞歌,人类的伟大是勇气的伟大"
            ),
        },
        "stats": {
            "gold": getattr(user, "gold", 0),
            "prop_count": len(getattr(user, "props", {}) or {}),
            "call_count": stat_count,
            "chat_count": chat_count,
        },
        "favorability": {
            "level": sign_level,
            "selected_indices": selected_indices,
        },
        "permission_level": permission_level,
        "chart": {
            "labels": chart_labels,
            "data": chart_data,
        },
    }

    return await ui.render_template("pages/builtin/my_info", data=profile_data)
