# ruff: noqa: ASYNC230

from collections.abc import Callable
from pathlib import Path
from typing import cast

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebug import App
from pytest_mock import MockerFixture
from respx import MockRouter

from tests.builtin_plugins.plugin_store.utils import get_content_bytes, init_mocked_api
from tests.config import BotId, GroupId, MessageId, UserId
from tests.utils import _v11_group_message_event


async def test_remove_plugin(
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试删除插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    init_mocked_api(mocked_api=mocked_api)
    mock_base_path = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )

    plugin_path = mock_base_path / "plugins" / "search_image"
    plugin_path.mkdir(parents=True, exist_ok=True)

    with open(plugin_path / "__init__.py", "wb") as f:
        f.write(get_content_bytes("search_image.py"))

    plugin_id = 1

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"移除插件 {plugin_id}"
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
            message=Message(message="插件 识图 移除成功! 重启后生效"),
            result=None,
            bot=bot,
        )
    assert mocked_api["basic_plugins"].called
    assert mocked_api["extra_plugins"].called
    assert not (mock_base_path / "plugins" / "search_image" / "__init__.py").is_file()


async def test_plugin_not_exist_remove(
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试插件不存在，移除插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    init_mocked_api(mocked_api=mocked_api)
    plugin_id = -1

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"移除插件 {plugin_id}"
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
            message=Message(message="移除插件 Id: -1 失败 e: 插件ID不存在..."),
            result=None,
            bot=bot,
        )


async def test_remove_plugin_not_install(
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试插件未安装，移除插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    init_mocked_api(mocked_api=mocked_api)
    _ = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )
    plugin_id = 1

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"移除插件 {plugin_id}"
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
            message=Message(message="插件 识图 不存在..."),
            result=None,
            bot=bot,
        )
