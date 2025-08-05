from collections.abc import Callable
from pathlib import Path
from typing import cast

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebug import App
from pytest_mock import MockerFixture
from respx import MockRouter

from tests.config import BotId, GroupId, MessageId, UserId
from tests.utils import _v11_group_message_event


async def test_update_plugin_basic_need_update(
    app: App,
    mocker: MockerFixture,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试更新基础插件，插件需要更新
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    mock_base_path = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )
    mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.StoreManager.get_loaded_plugins",
        return_value=[("search_image", "0.0")],
    )

    plugin_id = 1

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"更新插件 {plugin_id}"
        event: GroupMessageEvent = _v11_group_message_event(
            message=raw_message,
            self_id=BotId.QQ_BOT,
            user_id=UserId.SUPERUSER,
            group_id=GroupId.GROUP_ID_LEVEL_5,
            message_id=MessageId.MESSAGE_ID,
            to_me=True,
        )
        ctx.receive_event(bot=bot, event=event)
        ctx.should_call_send(
            event=event,
            message=Message(message=f"正在更新插件 Id: {plugin_id}"),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event=event,
            message=Message(message="插件 识图 更新成功! 重启后生效"),
            result=None,
            bot=bot,
        )
    assert (mock_base_path / "plugins" / "search_image" / "__init__.py").is_file()


async def test_update_plugin_basic_is_new(
    app: App,
    mocker: MockerFixture,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试更新基础插件，插件是最新版
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )
    mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.StoreManager.get_loaded_plugins",
        return_value=[("search_image", "0.2")],
    )

    plugin_id = 1

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"更新插件 {plugin_id}"
        event: GroupMessageEvent = _v11_group_message_event(
            message=raw_message,
            self_id=BotId.QQ_BOT,
            user_id=UserId.SUPERUSER,
            group_id=GroupId.GROUP_ID_LEVEL_5,
            message_id=MessageId.MESSAGE_ID,
            to_me=True,
        )
        ctx.receive_event(bot=bot, event=event)
        ctx.should_call_send(
            event=event,
            message=Message(message=f"正在更新插件 Id: {plugin_id}"),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event=event,
            message=Message(message="插件 识图 已是最新版本"),
            result=None,
            bot=bot,
        )


async def test_plugin_not_exist_update(
    app: App,
    create_bot: Callable,
) -> None:
    """
    测试插件不存在，更新插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    plugin_id = -1

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"更新插件 {plugin_id}"
        event: GroupMessageEvent = _v11_group_message_event(
            message=raw_message,
            self_id=BotId.QQ_BOT,
            user_id=UserId.SUPERUSER,
            group_id=GroupId.GROUP_ID_LEVEL_5,
            message_id=MessageId.MESSAGE_ID_2,
            to_me=True,
        )
        ctx.receive_event(bot=bot, event=event)
        ctx.should_call_send(
            event=event,
            message=Message(message=f"正在更新插件 Id: {plugin_id}"),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event=event,
            message=Message(message="更新插件 Id: -1 失败 e: 插件ID不存在..."),
            result=None,
            bot=bot,
        )


async def test_update_plugin_not_install(
    app: App,
    mocked_api: MockRouter,
    create_bot: Callable,
) -> None:
    """
    测试插件不存在，更新插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    plugin_id = 1

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"更新插件 {plugin_id}"
        event: GroupMessageEvent = _v11_group_message_event(
            message=raw_message,
            self_id=BotId.QQ_BOT,
            user_id=UserId.SUPERUSER,
            group_id=GroupId.GROUP_ID_LEVEL_5,
            message_id=MessageId.MESSAGE_ID_2,
            to_me=True,
        )
        ctx.receive_event(bot=bot, event=event)
        ctx.should_call_send(
            event=event,
            message=Message(message=f"正在更新插件 Id: {plugin_id}"),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event=event,
            message=Message(
                message="更新插件 Id: 1 失败 e: 插件 识图 未安装，无法更新"
            ),
            result=None,
            bot=bot,
        )
