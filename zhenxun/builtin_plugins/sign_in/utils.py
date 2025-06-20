from datetime import datetime
from io import BytesIO
import os
from pathlib import Path
import random

import nonebot
from nonebot.drivers import Driver
from nonebot_plugin_htmlrender import template_to_pic
from nonebot_plugin_uninfo import Uninfo
import pytz

from zhenxun.configs.config import BotConfig, Config
from zhenxun.configs.path_config import IMAGE_PATH, TEMPLATE_PATH
from zhenxun.models.sign_log import SignLog
from zhenxun.models.sign_user import SignUser
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.image_utils import BuildImage
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.platform import PlatformUtils

from .config import (
    SIGN_BACKGROUND_PATH,
    SIGN_BORDER_PATH,
    SIGN_RESOURCE_PATH,
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
    SIGN_RESOURCE_PATH.mkdir(parents=True, exist_ok=True)
    SIGN_TODAY_CARD_PATH.mkdir(exist_ok=True, parents=True)
    # await generate_progress_bar_pic()
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
    await generate_progress_bar_pic()
    user_id = user.user_id
    date = datetime.now().date()
    _type = "view" if is_card_view else "sign"
    file_name = f"{user_id}_{_type}_{date}.png"
    view_name = f"{user_id}_view_{date}.png"
    card_file = Path(SIGN_TODAY_CARD_PATH) / file_name
    if card_file.exists():
        return IMAGE_PATH / "sign" / "today_card" / file_name
    if add_impression == -1:
        card_file = Path(SIGN_TODAY_CARD_PATH) / view_name
        if card_file.exists():
            return card_file
        is_card_view = True
    return (
        await _generate_html_card(
            user, session, nickname, add_impression, gold, gift, is_double, is_card_view
        )
        if base_config.get("IMAGE_STYLE") == "zhenxun"
        else await _generate_card(
            user, session, nickname, add_impression, gold, gift, is_double, is_card_view
        )
    )


async def _generate_card(
    user: SignUser,
    session: Uninfo,
    nickname: str,
    add_impression: float,
    gold: int | None,
    gift: str,
    is_double: bool = False,
    is_card_view: bool = False,
) -> Path:
    """生成签到卡片

    参数:
        user: SignUser
        session: Uninfo
        nickname: 用户昵称
        add_impression: 新增的好感度
        gold: 金币
        gift: 礼物
        is_double: 是否触发双倍.
        is_card_view: 是否展示好感度卡片.

    返回:
        Path: 卡片路径
    """
    ava_bk = BuildImage(140, 140, (255, 255, 255, 0))
    ava_border = BuildImage(
        140,
        140,
        background=SIGN_BORDER_PATH / "ava_border_01.png",
    )
    if session.user.avatar and (
        byt := await AsyncHttpx.get_content(session.user.avatar)
    ):
        ava = BuildImage(107, 107, background=BytesIO(byt))
    else:
        ava = BuildImage(107, 107, (0, 0, 0))
    await ava.circle()
    await ava_bk.paste(ava, (19, 18))
    await ava_bk.paste(ava_border, center_type="center")
    impression = float(user.impression)
    info_img = BuildImage(250, 150, color=(255, 255, 255, 0), font_size=15)
    level, next_impression, previous_impression = get_level_and_next_impression(
        impression
    )
    interpolation = next_impression - impression
    await info_img.text((0, 0), f"· 好感度等级：{level} [{lik2relation[level]}]")
    await info_img.text(
        (0, 20), f"· {BotConfig.self_nickname}对你的态度：{level2attitude[level]}"
    )
    await info_img.text((0, 40), f"· 距离升级还差 {interpolation:.2f} 好感度")

    bar_bk = BuildImage(220, 20, background=SIGN_RESOURCE_PATH / "bar_white.png")
    bar = BuildImage(220, 20, background=SIGN_RESOURCE_PATH / "bar.png")
    ratio = 1 - (next_impression - impression) / (next_impression - previous_impression)
    if next_impression == 0:
        ratio = 0
    await bar.resize(width=int(bar.width * ratio) or 1, height=bar.height)
    await bar_bk.paste(bar)
    font_size = 20 if "好感度双倍加持卡" in gift else 30
    gift_border = BuildImage(
        270,
        100,
        background=SIGN_BORDER_PATH / "gift_border_02.png",
        font_size=font_size,
    )
    await gift_border.text((0, 0), gift, center_type="center")

    bk = BuildImage(
        876,
        424,
        background=SIGN_BACKGROUND_PATH
        / random.choice(os.listdir(SIGN_BACKGROUND_PATH)),
        font_size=25,
    )
    A = BuildImage(876, 274, background=SIGN_RESOURCE_PATH / "white.png")
    line = BuildImage(2, 180, color="black")
    await A.transparent(2)
    await A.paste(ava_bk, (25, 80))
    await A.paste(line, (200, 70))
    nickname_img = await BuildImage.build_text_image(
        nickname, size=50, font_color=(255, 255, 255)
    )
    user_console = await user.user_console
    if user_console and user_console.uid is not None:
        uid = f"{user_console.uid}".rjust(12, "0")
        uid = f"{uid[:4]} {uid[4:8]} {uid[8:]}"
    else:
        uid = "XXXX XXXX XXXX"
    uid_img = await BuildImage.build_text_image(
        f"UID: {uid}", size=30, font_color=(255, 255, 255)
    )
    image1 = await bk.build_text_image("Accumulative check-in for", bk.font, size=30)
    image2 = await bk.build_text_image("days", bk.font, size=30)
    sign_day_img = await BuildImage.build_text_image(
        f"{user.sign_count}", size=40, font_color=(211, 64, 33)
    )
    tip_width = image1.width + image2.width + sign_day_img.width + 60
    tip_height = max([image1.height, image2.height, sign_day_img.height])
    tip_image = BuildImage(tip_width, tip_height, (255, 255, 255, 0))
    await tip_image.paste(image1, (0, 7))
    await tip_image.paste(sign_day_img, (image1.width + 7, 0))
    await tip_image.paste(image2, (image1.width + sign_day_img.width + 15, 7))

    lik_text1_img = await BuildImage.build_text_image("当前", size=20)
    lik_text2_img = await BuildImage.build_text_image(
        f"好感度：{user.impression:.2f}", size=30
    )
    watermark = await BuildImage.build_text_image(
        f"{BotConfig.self_nickname}@{datetime.now().year}",
        size=15,
        font_color=(155, 155, 155),
    )
    today_data = BuildImage(300, 300, color=(255, 255, 255, 0), font_size=20)
    if is_card_view:
        today_sign_text_img = await BuildImage.build_text_image("", size=30)
        value_list = (
            await SignUser.annotate()
            .order_by("-impression")
            .values_list("user_id", flat=True)
        )
        index = value_list.index(user.user_id) + 1  # type: ignore
        rank_img = await BuildImage.build_text_image(
            f"* 好感度排名第 {index} 位", size=30
        )
        await A.paste(rank_img, ((A.width - rank_img.width - 32), 20))
        last_log = (
            await SignLog.filter(user_id=user.user_id).order_by("create_time").first()
        )
        last_date = "从未"
        if last_log:
            last_date = last_log.create_time.astimezone(
                pytz.timezone("Asia/Shanghai")
            ).date()
        await today_data.text(
            (0, 0),
            f"上次签到日期：{last_date}",
        )
        await today_data.text((0, 25), f"总金币：{gold}")
        default_setu_prob = (
            Config.get_config("send_setu", "INITIAL_SETU_PROBABILITY") * 100  # type: ignore
        )
        setu_prob = (
            default_setu_prob + float(user.impression) if user.impression < 100 else 100
        )
        await today_data.text(
            (0, 50),
            f"色图概率：{setu_prob:.2f}%",
        )
        await today_data.text((0, 75), f"开箱次数：{(20 + int(user.impression / 3))}")
        _type = "view"
    else:
        await A.paste(gift_border, (570, 140))
        today_sign_text_img = await BuildImage.build_text_image("今日签到", size=30)
        if is_double:
            await today_data.text((0, 0), f"好感度 + {add_impression / 2:.2f} × 2")
        else:
            await today_data.text((0, 0), f"好感度 + {add_impression:.2f}")
        await today_data.text((0, 25), f"金币 + {gold}")
        _type = "sign"
    current_date = datetime.now()
    current_datetime_str = current_date.strftime("%Y-%m-%d %a %H:%M:%S")
    date = current_date.date()
    date_img = await BuildImage.build_text_image(
        f"时间：{current_datetime_str}", size=20
    )
    await bk.paste(nickname_img, (30, 15))
    await bk.paste(uid_img, (30, 85))
    await bk.paste(A, (0, 150))
    await bk.paste(tip_image, (10, 167))
    await bk.paste(date_img, (220, 370))
    await bk.paste(lik_text1_img, (220, 240))
    await bk.paste(lik_text2_img, (262, 234))
    await bk.paste(bar_bk, (225, 275))
    await bk.paste(info_img, (220, 305))
    await bk.paste(today_sign_text_img, (550, 180))
    await bk.paste(today_data, (580, 220))
    await bk.paste(watermark, (15, 400))
    await bk.save(SIGN_TODAY_CARD_PATH / f"{user.user_id}_{_type}_{date}.png")
    return IMAGE_PATH / "sign" / "today_card" / f"{user.user_id}_{_type}_{date}.png"


async def generate_progress_bar_pic():
    """
    初始化进度条图片
    """
    bar_white_file = SIGN_RESOURCE_PATH / "bar_white.png"
    if bar_white_file.exists():
        return

    bg_2 = (254, 1, 254)
    bg_1 = (0, 245, 246)

    bk = BuildImage(1000, 50)
    img_x = BuildImage(50, 50, color=bg_2)
    await img_x.circle()
    await img_x.crop((25, 0, 50, 50))
    img_y = BuildImage(50, 50, color=bg_1)
    await img_y.circle()
    await img_y.crop((0, 0, 25, 50))
    A = BuildImage(950, 50)
    width, height = A.size

    step_r = (bg_2[0] - bg_1[0]) / width
    step_g = (bg_2[1] - bg_1[1]) / width
    step_b = (bg_2[2] - bg_1[2]) / width

    for y in range(width):
        bg_r = round(bg_1[0] + step_r * y)
        bg_g = round(bg_1[1] + step_g * y)
        bg_b = round(bg_1[2] + step_b * y)
        for x in range(height):
            await A.point((y, x), fill=(bg_r, bg_g, bg_b))
    await bk.paste(img_y, (0, 0))
    await bk.paste(A, (25, 0))
    await bk.paste(img_x, (975, 0))
    await bk.save(SIGN_RESOURCE_PATH / "bar.png")

    A = BuildImage(950, 50)
    bk = BuildImage(1000, 50)
    img_x = BuildImage(50, 50)
    await img_x.circle()
    await img_x.crop((25, 0, 50, 50))
    img_y = BuildImage(50, 50)
    await img_y.circle()
    await img_y.crop((0, 0, 25, 50))
    await bk.paste(img_y, (0, 0))
    await bk.paste(A, (25, 0))
    await bk.paste(img_x, (975, 0))
    await bk.save(bar_white_file)


def get_level_and_next_impression(impression: float) -> tuple[str, int | float, int]:
    """获取当前好感等级与下一等级的差距

    参数:
        impression: 好感度

    返回:
        tuple[str, int, int]: 好感度等级，下一等级好感度要求，已达到的好感度要求
    """

    keys = list(lik2level.keys())
    level, next_impression, previous_impression = (
        lik2level[keys[-1]],
        keys[-2],
        keys[-1],
    )
    for i in range(len(keys)):
        if impression >= keys[i]:
            level, next_impression, previous_impression = (
                lik2level[keys[i]],
                keys[i - 1],
                keys[i],
            )
            if i == 0:
                next_impression = impression
            break
    return level, next_impression, previous_impression


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
    """生成签到卡片

    参数:
        user: SignUser
        session: Uninfo
        nickname: 用户昵称
        add_impression: 新增的好感度
        gold: 金币
        gift: 礼物
        is_double: 是否触发双倍.
        is_card_view: 是否展示好感度卡片.

    返回:
        Path: 卡片路径
    """
    impression = float(user.impression)
    user_console = await user.user_console
    if user_console and user_console.uid is not None:
        uid = f"{user_console.uid}".rjust(12, "0")
        uid = f"{uid[:4]} {uid[4:8]} {uid[8:]}"
    else:
        uid = "XXXX XXXX XXXX"
    level, next_impression, previous_impression = get_level_and_next_impression(
        impression
    )
    interpolation = next_impression - impression
    message = f"{BotConfig.self_nickname}希望你开心！"
    hour = datetime.now().hour
    if hour > 6 and hour < 10:
        message = random.choice(MORNING_MESSAGE)
    elif hour >= 0 and hour < 6:
        message = random.choice(LG_MESSAGE)
    _impression = f"{add_impression}(×2)" if is_double else add_impression
    process = 1 - (next_impression - impression) / (
        next_impression - previous_impression
    )
    now = datetime.now()
    data = {
        "ava_url": PlatformUtils.get_user_avatar_url(
            user.user_id, PlatformUtils.get_platform(session), session.self_id
        ),
        "name": nickname,
        "uid": uid,
        "sign_count": f"{user.sign_count}",
        "message": f"{BotConfig.self_nickname}说: {message}",
        "cur_impression": f"{impression:.2f}",
        "impression": f"好感度+{_impression}",
        "gold": f"金币+{gold}",
        "gift": gift,
        "level": f"{level} [{lik2relation[level]}]",
        "attitude": f"对你的态度: {level2attitude[level]}",
        "interpolation": f"{interpolation:.2f}",
        "heart2": [1 for _ in range(int(level))],
        "heart1": [1 for _ in range(len(lik2level) - int(level) - 1)],
        "process": process * 100,
        "date": str(now.replace(microsecond=0)),
        "font_size": 45,
    }
    if len(nickname) > 6:
        data["font_size"] = 27
    _type = "sign"
    if is_card_view:
        _type = "view"
        value_list = (
            await SignUser.annotate()
            .order_by("-impression")
            .values_list("user_id", flat=True)
        )
        index = value_list.index(user.user_id) + 1  # type: ignore
        data["impression"] = f"好感度排名第 {index} 位"
        data["gold"] = f"总金币：{gold}"
        data["gift"] = ""
    pic = await template_to_pic(
        template_path=str((TEMPLATE_PATH / "sign").absolute()),
        template_name="main.html",
        templates={"data": data},
        pages={
            "viewport": {"width": 465, "height": 926},
            "base_url": f"file://{TEMPLATE_PATH}",
        },
        wait=2,
    )
    image = BuildImage.open(pic)
    date = now.date()
    await image.save(SIGN_TODAY_CARD_PATH / f"{user.user_id}_{_type}_{date}.png")
    return IMAGE_PATH / "sign" / "today_card" / f"{user.user_id}_{_type}_{date}.png"
