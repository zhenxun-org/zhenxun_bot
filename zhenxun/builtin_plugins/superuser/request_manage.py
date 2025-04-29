from io import BytesIO

from arclet.alconna import Args, Option
from arclet.alconna.typing import CommandMeta
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from nonebot_plugin_alconna import (
    Alconna,
    AlconnaQuery,
    Arparma,
    Match,
    Query,
    Reply,
    on_alconna,
    store_true,
)
from nonebot_plugin_alconna.uniseg.tools import reply_fetch
from nonebot_plugin_session import EventSession

from zhenxun.configs.config import BotConfig
from zhenxun.configs.path_config import IMAGE_PATH
from zhenxun.configs.utils import PluginExtraData
from zhenxun.models.fg_request import FgRequest
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType, RequestHandleType, RequestType
from zhenxun.utils.exception import NotFoundError
from zhenxun.utils.image_utils import BuildImage
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.utils import get_user_avatar

usage = """
查看请求
清空请求
请求处理 -fa [id] / 同意好友请求 [id]      # 同意好友请求
请求处理 -fr [id] / 拒绝好友请求 [id]      # 拒绝好友请求
请求处理 -fi [id] / 忽略好友请求 [id]      # 忽略好友请求
请求处理 -ga [id] / 同意群组请求 [id]      # 同意群聊请求
请求处理 -gr [id] / 拒绝群组请求 [id]      # 拒绝群聊请求
请求处理 -gi [id] / 忽略群组请求 [id]      # 忽略群聊请求
""".strip()


__plugin_meta__ = PluginMetadata(
    name="请求处理",
    description="好友与邀请群组请求处理",
    usage=usage,
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.SUPERUSER,
    ).to_dict(),
)


_req_matcher = on_alconna(
    Alconna(
        "请求处理",
        Args["handle", ["-fa", "-fr", "-fi", "-ga", "-gr", "-gi"]]["id?", int],
        meta=CommandMeta(
            description="好友/群组请求处理",
            usage=usage,
            example="同意好友请求 20",
            compact=True,
        ),
    ),
    permission=SUPERUSER,
    priority=1,
    rule=to_me(),
    block=True,
)

_read_matcher = on_alconna(
    Alconna(
        "查看请求",
        Option("-f|--friend", action=store_true, help_text="查看好友请求"),
        Option("-g|--group", action=store_true, help_text="查看群组请求"),
        meta=CommandMeta(
            description="查看所有请求或好友群组请求",
            usage="查看请求\n查看请求 -f\n查看请求-g",
            example="查看请求 -f",
            compact=True,
        ),
    ),
    permission=SUPERUSER,
    priority=1,
    rule=to_me(),
    block=True,
)

_clear_matcher = on_alconna(
    Alconna(
        "清空请求",
        Option("-f|--friend", action=store_true, help_text="清空好友请求"),
        Option("-g|--group", action=store_true, help_text="清空群组请求"),
        meta=CommandMeta(
            description="清空请求",
            usage="清空请求\n清空请求 -f\n清空请求-g",
            example="清空请求 -f",
            compact=True,
        ),
    ),
    permission=SUPERUSER,
    priority=1,
    rule=to_me(),
    block=True,
)

reg_arg_list = [
    (r"同意好友请求\s*(?P<id>\d*)", ["-fa", "{id}"]),
    (r"拒绝好友请求\s*(?P<id>\d*)", ["-fr", "{id}"]),
    (r"忽略好友请求\s*(?P<id>\d*)", ["-fi", "{id}"]),
    (r"同意群组请求\s*(?P<id>\d*)", ["-ga", "{id}"]),
    (r"拒绝群组请求\s*(?P<id>\d*)", ["-gr", "{id}"]),
    (r"忽略群组请求\s*(?P<id>\d*)", ["-gi", "{id}"]),
]

for r in reg_arg_list:
    _req_matcher.shortcut(
        r[0],
        command="请求处理",
        arguments=r[1],
        prefix=True,
    )


@_req_matcher.handle()
async def _(
    bot: Bot,
    event: Event,
    session: EventSession,
    handle: str,
    id: Match[int],
    arparma: Arparma,
):
    reply: Reply | None = None
    type_dict = {
        "a": RequestHandleType.APPROVE,
        "r": RequestHandleType.REFUSED,
        "i": RequestHandleType.IGNORE,
    }
    if not id.available:
        reply = await reply_fetch(event, bot)
        if not reply:
            await MessageUtils.build_message("请引用消息处理或添加处理Id.").finish()
    handle_id = id.result
    if reply:
        db_data = await FgRequest.get_or_none(message_ids__contains=reply.id)
        if not db_data:
            await MessageUtils.build_message(
                "未发现此消息的Id，请使用Id进行处理..."
            ).finish(reply_to=True)
        handle_id = db_data.id
    req = None
    handle_type = type_dict[handle[-1]]
    try:
        if handle_type == RequestHandleType.APPROVE:
            req = await FgRequest.approve(bot, handle_id)
        if handle_type == RequestHandleType.REFUSED:
            req = await FgRequest.refused(bot, handle_id)
        if handle_type == RequestHandleType.IGNORE:
            req = await FgRequest.ignore(handle_id)
    except NotFoundError:
        await MessageUtils.build_message("未发现此id的请求...").finish(reply_to=True)
    except Exception:
        await MessageUtils.build_message("其他错误, 可能flag已失效...").finish(
            reply_to=True
        )
    logger.info(
        f"处理请求 Id: {req.id if req else ''}", arparma.header_result, session=session
    )
    await MessageUtils.build_message("成功处理请求!").send(reply_to=True)
    if req and handle_type == RequestHandleType.APPROVE:
        await bot.send_private_msg(
            user_id=req.user_id,
            message=f"管理员已同意此次群组邀请，请不要让{BotConfig.self_nickname}受委屈哦（狠狠监控）"
            "\n在群组中 群组管理员与群主 允许使用管理员帮助"
            "（包括ban与功能开关等）\n请在群组中发送 '管理员帮助'",
        )


@_read_matcher.handle()
async def _(
    session: EventSession,
    arparma: Arparma,
    is_friend: Query[bool] = AlconnaQuery("friend.value", False),
    is_group: Query[bool] = AlconnaQuery("group.value", False),
):
    if all_request := await FgRequest.filter(handle_type__isnull=True).all():
        req_list = list(all_request)
        req_list.reverse()
        friend_req: list[FgRequest] = []
        group_req: list[FgRequest] = []
        for req in req_list:
            if req.request_type == RequestType.FRIEND:
                friend_req.append(req)
            else:
                group_req.append(req)
        if is_friend.result:
            group_req = []
        elif is_group.result:
            friend_req = []
        req_image_list: list[BuildImage] = []
        for i, req_list in enumerate([friend_req, group_req]):
            img_list = []
            for req in req_list:
                content = await get_user_avatar(req.user_id)
                ava_img = BuildImage(
                    80, 80, background=BytesIO(content) if content else None
                )
                await ava_img.circle()
                handle_img = BuildImage(
                    130, 32, font_size=15, color="#EEEFF4", font="HYWenHei-85W.ttf"
                )
                await handle_img.text((0, 0), "同意/拒绝/忽略", center_type="center")
                await handle_img.circle_corner(10)
                background = BuildImage(500, 100, font_size=22, color=(255, 255, 255))
                await background.paste(ava_img, (55, 0), center_type="height")
                if session.platform and session.platform != "unknown":
                    platform_icon = BuildImage(
                        30,
                        30,
                        background=IMAGE_PATH / "_icon" / f"{session.platform}.png",
                    )
                    await background.paste(platform_icon, (46, 10))
                await background.text((150, 12), req.nickname)
                if i == 0:
                    comment_img = await BuildImage.build_text_image(
                        f"对方留言：{req.comment}", size=15, font_color=(140, 140, 143)
                    )
                else:
                    comment_img = await BuildImage.build_text_image(
                        f"群组：{req.group_id}", size=15, font_color=(140, 140, 143)
                    )
                await background.paste(comment_img, (150, 65))
                tag = await BuildImage.build_text_image(
                    f"{req.platform}",
                    size=13,
                    color=(0, 167, 250),
                    font="HYWenHei-85W.ttf",
                    font_color=(255, 255, 255),
                    padding=(1, 6, 1, 6),
                )
                await tag.circle_corner(5)
                await background.paste(tag, (150, 42))
                await background.paste(handle_img, (360, 35))
                _id_img = BuildImage(
                    32, 32, font_size=15, color="#EEEFF4", font="HYWenHei-85W.ttf"
                )
                await _id_img.text((0, 0), f"{req.id}", center_type="center")
                await _id_img.circle_corner(10)
                await background.paste(_id_img, (10, 0), center_type="height")
                img_list.append(background)
            if img_list:
                A = await BuildImage.auto_paste(img_list, 1)
                result_image = BuildImage(
                    A.width, A.height + 30, color=(255, 255, 255), font_size=20
                )
                await result_image.paste(A, (0, 30))
                _type_text = "好友请求" if i == 0 else "群组请求"
                await result_image.text((15, 13), _type_text, fill=(140, 140, 143))
                req_image_list.append(result_image)
        if not req_image_list:
            await MessageUtils.build_message("没有任何请求喔...").finish(reply_to=True)
        if len(req_image_list) == 1:
            await MessageUtils.build_message(req_image_list[0]).finish()
        width = sum(img.width for img in req_image_list)
        height = max(img.height for img in req_image_list)
        background = BuildImage(width, height)
        await background.paste(req_image_list[0])
        await req_image_list[1].line((0, 10, 1, req_image_list[1].height - 10), width=1)
        await background.paste(req_image_list[1], (req_image_list[1].width, 0))
        logger.info("查看请求", arparma.header_result, session=session)
        await MessageUtils.build_message(background).finish()
    await MessageUtils.build_message("没有任何请求喔...").finish(reply_to=True)


@_clear_matcher.handle()
async def _(
    session: EventSession,
    arparma: Arparma,
    is_friend: Query[bool] = AlconnaQuery("friend.value", False),
    is_group: Query[bool] = AlconnaQuery("group.value", False),
):
    _type = ""
    if is_friend.result:
        _type = "好友"
        await FgRequest.filter(
            handle_type__isnull=True, request_type=RequestType.FRIEND
        ).update(handle_type=RequestHandleType.IGNORE)
    elif is_group.result:
        _type = "群组"
        await FgRequest.filter(
            handle_type__isnull=True, request_type=RequestType.GROUP
        ).update(handle_type=RequestHandleType.IGNORE)
    else:
        _type = "所有"
        await FgRequest.filter(handle_type__isnull=True).update(
            handle_type=RequestHandleType.IGNORE
        )
    logger.info(f"清空{_type}请求", arparma.header_result, session=session)
    await MessageUtils.build_message(f"已清空{_type}请求!").finish()
