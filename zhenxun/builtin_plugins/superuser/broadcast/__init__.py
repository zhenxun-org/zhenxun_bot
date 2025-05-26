from arclet.alconna import AllParam
from nepattern import UnionPattern
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
import nonebot_plugin_alconna as alc
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    on_alconna,
)
from nonebot_plugin_alconna.uniseg.segment import (
    At,
    AtAll,
    Audio,
    Button,
    Emoji,
    File,
    Hyper,
    Image,
    Keyboard,
    Reference,
    Reply,
    Text,
    Video,
    Voice,
)
from nonebot_plugin_session import EventSession

from zhenxun.configs.utils import PluginExtraData, RegisterConfig, Task
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

from .broadcast_manager import BroadcastManager
from .message_processor import (
    _extract_broadcast_content,
    get_broadcast_target_groups,
    send_broadcast_and_notify,
)

BROADCAST_SEND_DELAY_RANGE = (1, 3)

__plugin_meta__ = PluginMetadata(
    name="广播",
    description="昭告天下！",
    usage="""
    广播 [消息内容]
    - 直接发送消息到除当前群组外的所有群组
    - 支持文本、图片、@、表情、视频等多种消息类型
    - 示例：广播 你们好！
    - 示例：广播 [图片] 新活动开始啦！

    广播 + 引用消息
    - 将引用的消息作为广播内容发送
    - 支持引用普通消息或合并转发消息
    - 示例：(引用一条消息) 广播

    广播撤回
    - 撤回最近一次由您触发的广播消息
    - 仅能撤回短时间内的消息
    - 示例：广播撤回

    特性：
    - 在群组中使用广播时，不会将消息发送到当前群组
    - 在私聊中使用广播时，会发送到所有群组

    别名：
    - bc (广播的简写)
    - recall (广播撤回的别名)
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="1.2",
        plugin_type=PluginType.SUPERUSER,
        configs=[
            RegisterConfig(
                module="_task",
                key="DEFAULT_BROADCAST",
                value=True,
                help="被动 广播 进群默认开关状态",
                default_value=True,
                type=bool,
            )
        ],
        tasks=[Task(module="broadcast", name="广播")],
    ).to_dict(),
)

AnySeg = (
    UnionPattern(
        [
            Text,
            Image,
            At,
            AtAll,
            Audio,
            Video,
            File,
            Emoji,
            Reply,
            Reference,
            Hyper,
            Button,
            Keyboard,
            Voice,
        ]
    )
    @ "AnySeg"
)

_matcher = on_alconna(
    Alconna(
        "广播",
        Args["content?", AllParam],
    ),
    aliases={"bc"},
    priority=1,
    permission=SUPERUSER,
    block=True,
    rule=to_me(),
    use_origin=False,
)

_recall_matcher = on_alconna(
    Alconna("广播撤回"),
    aliases={"recall"},
    priority=1,
    permission=SUPERUSER,
    block=True,
    rule=to_me(),
)


@_matcher.handle()
async def handle_broadcast(
    bot: Bot,
    event: Event,
    session: EventSession,
    arp: alc.Arparma,
):
    broadcast_content_msg = await _extract_broadcast_content(bot, event, arp, session)
    if not broadcast_content_msg:
        return

    target_groups, enabled_groups = await get_broadcast_target_groups(bot, session)
    if not target_groups or not enabled_groups:
        return

    try:
        await send_broadcast_and_notify(
            bot, event, broadcast_content_msg, enabled_groups, target_groups, session
        )
    except Exception as e:
        error_msg = "发送广播失败"
        BroadcastManager.log_error(error_msg, e, session)
        await MessageUtils.build_message(f"{error_msg}。").send(reply_to=True)


@_recall_matcher.handle()
async def handle_broadcast_recall(
    bot: Bot,
    event: Event,
    session: EventSession,
):
    """处理广播撤回命令"""
    await MessageUtils.build_message("正在尝试撤回最近一次广播...").send()

    try:
        success_count, error_count = await BroadcastManager.recall_last_broadcast(
            bot, session
        )

        user_id = str(event.get_user_id())
        if success_count == 0 and error_count == 0:
            await bot.send_private_msg(
                user_id=user_id,
                message="没有找到最近的广播消息记录，可能已经撤回或超过可撤回时间。",
            )
        else:
            result = f"广播撤回完成!\n成功撤回 {success_count} 条消息"
            if error_count:
                result += f"\n撤回失败 {error_count} 条消息 (可能已过期或无权限)"
            await bot.send_private_msg(user_id=user_id, message=result)
            BroadcastManager.log_info(
                f"广播撤回完成: 成功 {success_count}, 失败 {error_count}", session
            )
    except Exception as e:
        error_msg = "撤回广播消息失败"
        BroadcastManager.log_error(error_msg, e, session)
        user_id = str(event.get_user_id())
        await bot.send_private_msg(user_id=user_id, message=f"{error_msg}。")
