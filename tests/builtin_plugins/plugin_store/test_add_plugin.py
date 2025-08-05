from collections.abc import Callable
from pathlib import Path
from typing import cast

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebug import App
from pytest_mock import MockerFixture

from tests.config import BotId, GroupId, MessageId, UserId
from tests.utils import _v11_group_message_event

test_path = Path(__file__).parent.parent.parent


async def test_add_plugin_basic(
    app: App,
    mocker: MockerFixture,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试添加基础插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    mock_base_path = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )

    plugin_id = "search_image"

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"添加插件 {plugin_id}"
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
            message=Message(message=f"正在添加插件 Module: {plugin_id}"),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event=event,
            message=Message(message="插件 识图 安装成功! 重启后生效"),
            result=None,
            bot=bot,
        )
    assert (mock_base_path / "plugins" / "search_image" / "__init__.py").is_file()


async def test_add_plugin_basic_commit_version(
    app: App,
    mocker: MockerFixture,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试添加基础插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    mock_base_path = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )

    plugin_id = "bilibili_sub"

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"添加插件 {plugin_id}"
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
            message=Message(message=f"正在添加插件 Module: {plugin_id}"),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event=event,
            message=Message(message="插件 B站订阅 安装成功! 重启后生效"),
            result=None,
            bot=bot,
        )
    assert (mock_base_path / "plugins" / "bilibili_sub" / "__init__.py").is_file()


async def test_add_plugin_basic_is_not_dir(
    app: App,
    mocker: MockerFixture,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试添加基础插件，插件不是目录
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    mock_base_path = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )

    plugin_id = "jitang"

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"添加插件 {plugin_id}"
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
            message=Message(message=f"正在添加插件 Module: {plugin_id}"),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event=event,
            message=Message(message="插件 鸡汤 安装成功! 重启后生效"),
            result=None,
            bot=bot,
        )
    assert (mock_base_path / "plugins" / "jitang.py").is_file()


async def test_add_plugin_extra(
    app: App,
    mocker: MockerFixture,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试添加额外插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    mock_base_path = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )

    plugin_id = "github_sub"

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message: str = f"添加插件 {plugin_id}"
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
            message=Message(message=f"正在添加插件 Module: {plugin_id}"),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event=event,
            message=Message(message="插件 github订阅 安装成功! 重启后生效"),
            result=None,
            bot=bot,
        )
    assert (mock_base_path / "plugins" / "github_sub" / "__init__.py").is_file()


async def test_plugin_not_exist_add(
    app: App,
    create_bot: Callable,
) -> None:
    """
    测试插件不存在，添加插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    plugin_id = -1

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"添加插件 {plugin_id}"
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
            message=Message(message=f"正在添加插件 Id: {plugin_id}"),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event=event,
            message=Message(message="添加插件 Id: -1 失败 e: 插件ID不存在..."),
            result=None,
            bot=bot,
        )


async def test_add_plugin_exist(
    app: App,
    mocker: MockerFixture,
    create_bot: Callable,
) -> None:
    """
    测试插件已经存在，添加插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.StoreManager.get_loaded_plugins",
        return_value=[("search_image", "0.1")],
    )
    plugin_id = 1

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"添加插件 {plugin_id}"
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
            message=Message(message=f"正在添加插件 Id: {plugin_id}"),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event=event,
            message=Message(
                message="添加插件 Id: 1 失败 e: 插件 识图 已安装，无需重复安装"
            ),
            result=None,
            bot=bot,
        )
