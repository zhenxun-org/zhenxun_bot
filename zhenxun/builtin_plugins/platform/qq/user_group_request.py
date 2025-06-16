import asyncio
from datetime import datetime
import random

from nonebot.adapters import Bot
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from nonebot_plugin_alconna import Alconna, Args, Arparma, Field, on_alconna
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.utils import PluginCdBlock, PluginExtraData
from zhenxun.models.fg_request import FgRequest
from zhenxun.services.log import logger
from zhenxun.utils.depends import UserName
from zhenxun.utils.enum import RequestHandleType, RequestType
from zhenxun.utils.platform import PlatformUtils

__plugin_meta__ = PluginMetadata(
    name="群组申请",
    description="""
    一些小群直接邀请入群导致无法正常生成审核请求，需要用该方法手动生成审核请求。
    当管理员同意同意时会发送消息进行提示，之后再进行拉群不会退出。
    该消息会发送至管理员，多次发送不存在的群组id或相同群组id可能导致ban。
    """.strip(),
    usage="""
    指令：
        申请入群 [群号]
        示例: 申请入群 123123123
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        menu_type="其他",
        limits=[PluginCdBlock(cd=300, result="每5分钟只能申请一次哦~")],
    ).to_dict(),
)


_matcher = on_alconna(
    Alconna(
        "申请入群",
        Args[
            "group_id",
            int,
            Field(
                missing_tips=lambda: "请在命令后跟随群组id！",
                unmatch_tips=lambda _: "群组id必须为数字！",
            ),
        ],
    ),
    skip_for_unmatch=False,
    priority=5,
    block=True,
    rule=to_me(),
)


@_matcher.handle()
async def _(
    bot: Bot, session: Uninfo, arparma: Arparma, group_id: int, uname: str = UserName()
):
    # 旧请求全部设置为过期
    await FgRequest.filter(
        request_type=RequestType.GROUP,
        user_id=session.user.id,
        group_id=str(group_id),
        handle_type__isnull=True,
    ).update(handle_type=RequestHandleType.EXPIRE)
    f = await FgRequest.create(
        request_type=RequestType.GROUP,
        platform=PlatformUtils.get_platform(session),
        bot_id=bot.self_id,
        flag="0",
        user_id=session.user.id,
        nickname=uname,
        group_id=str(group_id),
    )
    results = await PlatformUtils.send_superuser(
        bot,
        f"*****一份入群申请*****\n"
        f"ID：{f.id}\n"
        f"申请人：{uname}({session.user.id})\n群聊："
        f"{group_id}\n邀请日期：{datetime.now().replace(microsecond=0)}\n"
        "注：该请求为手动申请入群",
    )
    if message_ids := [
        str(r[1].msg_ids[0]["message_id"]) for r in results if r[1] and r[1].msg_ids
    ]:
        f.message_ids = ",".join(message_ids)
        await f.save(update_fields=["message_ids"])
    await asyncio.sleep(random.randint(1, 5))
    await bot.send_private_msg(
        user_id=int(session.user.id),
        message=f"已发送申请，请等待管理员审核，ID：{f.id}。",
    )
    logger.info(
        f"用户 {uname}({session.user.id}) 申请入群 {group_id}，ID：{f.id}。",
        arparma.header_result,
        session=session,
    )
