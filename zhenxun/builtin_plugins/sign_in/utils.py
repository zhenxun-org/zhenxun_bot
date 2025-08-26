from datetime import datetime
import os
from pathlib import Path
import random

import aiofiles
import nonebot
from nonebot.drivers import Driver
from nonebot_plugin_uninfo import Uninfo

from zhenxun import ui
from zhenxun.configs.config import BotConfig, Config
from zhenxun.models.sign_user import SignUser
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.platform import PlatformUtils

from .config import (
    SIGN_TODAY_CARD_PATH,
    level2attitude,
    lik2level,
    lik2relation,
)

assert (
    len(level2attitude) == len(lik2level) == len(lik2relation)
), "好感度态度、等级、关系长度不匹配！"

AVA_URL = "http://q1.qlogo.cn/g?b=qq&nk={}&s=160"

driver: Driver = nonebot.get_driver()

base_config = Config.get("sign_in")


MORNING_MESSAGE = [
    "早上好，希望今天是美好的一天！",
    "醒了吗，今天也要元气满满哦！",
    "早上好呀，今天也要开心哦！",
    "早安，愿你拥有美好的一天！",
]

LG_MESSAGE = [
    "今天要早点休息哦~",
    "可不要熬夜到太晚呀",
    "请尽早休息吧！",
    "不要熬夜啦！",
]


@PriorityLifecycle.on_startup(priority=5)
async def init_image():
    SIGN_TODAY_CARD_PATH.mkdir(exist_ok=True, parents=True)
    clear_sign_data_pic()


async def get_card(
    user: SignUser,
    session: Uninfo,
    nickname: str,
    add_impression: float,
    gold: int | None,
    gift: str,
    is_double: bool = False,
    is_card_view: bool = False,
) -> Path:
    """获取好感度卡片

    参数:
        user: SignUser
        session: Uninfo
        nickname: 用户昵称
        impression: 新增的好感度
        gold: 金币
        gift: 礼物
        is_double: 是否触发双倍.
        is_card_view: 是否展示好感度卡片.

    返回:
        Path: 卡片路径
    """
    user_id = user.user_id
    date = datetime.now().date()
    _type = "view" if is_card_view else "sign"
    file_name = f"{user_id}_{_type}_{date}.png"
    card_file = SIGN_TODAY_CARD_PATH / file_name

    if card_file.exists():
        return card_file

    if add_impression == -1:
        view_name = f"{user_id}_view_{date}.png"
        view_card_file = SIGN_TODAY_CARD_PATH / view_name
        if view_card_file.exists():
            return view_card_file
        is_card_view = True

    return await _generate_html_card(
        user, session, nickname, add_impression, gold, gift, is_double, is_card_view
    )


def get_level_and_next_impression(impression: float) -> tuple[int, int | float, int]:
    """获取当前好感等级与下一等级的差距

    参数:
        impression: 好感度

    返回:
        tuple[int, int, int]: 好感度等级，下一等级好感度要求，已达到的好感度要求
    """

    keys = list(lik2level.keys())
    level_int, next_impression, previous_impression = (
        int(lik2level[keys[-1]]),
        keys[-2],
        keys[-1],
    )
    for i in range(len(keys)):
        if impression >= keys[i]:
            level_int, next_impression, previous_impression = (
                int(lik2level[keys[i]]),
                keys[i - 1],
                keys[i],
            )
            if i == 0:
                next_impression = impression
            break
    return level_int, next_impression, previous_impression


def clear_sign_data_pic():
    """
    清空当前签到图片数据
    """
    date = datetime.now().date()
    for file in os.listdir(SIGN_TODAY_CARD_PATH):
        if str(date) not in file:
            os.remove(SIGN_TODAY_CARD_PATH / file)


async def _generate_html_card(
    user: SignUser,
    session: Uninfo,
    nickname: str,
    add_impression: float,
    gold: int | None,
    gift: str,
    is_double: bool = False,
    is_card_view: bool = False,
) -> Path:
    """使用渲染服务生成签到卡片

    参数:
        user: SignUser
        session: Uninfo
        nickname: 用户昵称
        add_impression: 新增的好感度
        gold: 金币
        gift: 礼物
        is_double: 是否触发双倍.
        is_card_view: 是否为卡片视图.

    返回:
        Path: 卡片路径
    """
    now = datetime.now()
    date = now.date()
    _type = "view" if is_card_view else "sign"
    file_name = f"{user.user_id}_{_type}_{date}.png"
    card_file = SIGN_TODAY_CARD_PATH / file_name

    if card_file.exists():
        return card_file

    impression = float(user.impression)
    user_console = await user.user_console
    if user_console and user_console.uid is not None:
        uid = f"{user_console.uid}".rjust(12, "0")
        uid_formatted = f"{uid[:4]} {uid[4:8]} {uid[8:]}"
    else:
        uid_formatted = "XXXX XXXX XXXX"

    level, next_impression, previous_impression = get_level_and_next_impression(
        impression
    )

    attitude = f"对你的态度: {level2attitude.get(str(level), '未知')}"
    interpolation_val = max(0, next_impression - impression)
    interpolation = f"{interpolation_val:.2f}"

    denominator = next_impression - previous_impression
    progress = (
        100.0
        if denominator == 0
        else min(100.0, ((impression - previous_impression) / denominator) * 100)
    )

    hour = now.hour
    if 6 < hour < 10:
        message = random.choice(MORNING_MESSAGE)
    elif 0 <= hour < 6:
        message = random.choice(LG_MESSAGE)
    else:
        message = f"{BotConfig.self_nickname}希望你开心！"
    bot_message = f"{BotConfig.self_nickname}说: {message}"

    temperature = random.randint(1, 40)
    weather_icon_name = f"{random.randint(0, 11)}.png"
    tag_icon_name = f"{random.randint(0, 5)}.png"

    font_size = 45
    if len(nickname) > 6:
        font_size = 27

    user_info = {
        "nickname": nickname,
        "uid_str": uid_formatted,
        "avatar_url": PlatformUtils.get_user_avatar_url(
            user.user_id, PlatformUtils.get_platform(session), session.self_id
        )
        or "",
        "sign_count": user.sign_count,
        "font_size": font_size,
    }

    favorability_info = {
        "current": impression,
        "level": level,
        "level_text": f"{level} [{lik2relation.get(str(level), '未知')}]",
        "attitude": f"对你的态度: {level2attitude.get(str(level), '未知')}",
        "relation": lik2relation.get(str(level), "未知"),
        "heart2": [1 for _ in range(level)],
        "heart1": [1 for _ in range(len(lik2level) - level - 1)],
        "next_level_at": next_impression,
        "previous_level_at": previous_impression,
    }

    reward_info = None
    rank = None
    total_gold = None
    last_sign_date_str = None

    if is_card_view:
        value_list = (
            await SignUser.annotate()
            .order_by("-impression")
            .values_list("user_id", flat=True)
        )
        rank = value_list.index(user.user_id) + 1 if user.user_id in value_list else 0
        total_gold = user_console.gold if user_console else 0

        last_sign_date_str = ""

        reward_info = {
            "impression": f"好感度排名第 {rank} 位",
            "gold": f"总金币：{total_gold}",
            "gift": "",
            "is_double": False,
        }

    else:
        reward_info = {
            "impression_added": add_impression,
            "gold_added": gold or 0,
            "gift_received": gift,
            "is_double": is_double,
        }

    page_info = {
        "date_str": str(now.replace(microsecond=0)),
        "weather_icon_name": weather_icon_name,
        "temperature": temperature,
        "tag_icon_name": tag_icon_name,
    }

    card_data = {
        "is_card_view": is_card_view,
        "user": user_info,
        "favorability": favorability_info,
        "reward": reward_info,
        "page": page_info,
        "bot_message": bot_message,
        "attitude": attitude,
        "interpolation": interpolation,
        "progress": progress,
        "rank": rank,
        "total_gold": total_gold,
        "last_sign_date_str": last_sign_date_str,
    }

    image_bytes = await ui.render_template("pages/builtin/sign", data=card_data)

    async with aiofiles.open(card_file, "wb") as f:
        await f.write(image_bytes)

    return card_file
