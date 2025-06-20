from collections.abc import Callable
from pathlib import Path
from typing import cast

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebug import App
from pytest_mock import MockerFixture
from respx import MockRouter

from tests.builtin_plugins.plugin_store.utils import init_mocked_api
from tests.config import BotId, GroupId, MessageId, UserId
from tests.utils import _v11_group_message_event


async def test_search_plugin_name(
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试搜索插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher
    from zhenxun.builtin_plugins.plugin_store.data_source import row_style

    init_mocked_api(mocked_api=mocked_api)

    mock_table_page = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.ImageTemplate.table_page"
    )
    mock_table_page_return = mocker.AsyncMock()
    mock_table_page.return_value = mock_table_page_return

    mock_build_message = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.MessageUtils.build_message"
    )
    mock_build_message_return = mocker.AsyncMock()
    mock_build_message.return_value = mock_build_message_return

    plugin_name = "github订阅"

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"搜索插件 {plugin_name}"
        event: GroupMessageEvent = _v11_group_message_event(
            message=raw_message,
            self_id=BotId.QQ_BOT,
            user_id=UserId.SUPERUSER,
            group_id=GroupId.GROUP_ID_LEVEL_5,
            message_id=MessageId.MESSAGE_ID_3,
            to_me=True,
        )
        ctx.receive_event(bot=bot, event=event)
    mock_table_page.assert_awaited_once_with(
        "商店插件列表",
        "通过添加/移除插件 ID 来管理插件",
        ["-", "ID", "名称", "简介", "作者", "版本", "类型"],
        [
            [
                "",
                4,
                "github订阅",
                "订阅github用户或仓库",
                "xuanerwa",
                "0.7",
                "普通插件",
            ]
        ],
        text_style=row_style,
    )
    mock_build_message.assert_called_once_with(mock_table_page_return)
    mock_build_message_return.send.assert_awaited_once()

    assert mocked_api["basic_plugins"].called
    assert mocked_api["extra_plugins"].called


async def test_search_plugin_author(
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试搜索插件，作者
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher
    from zhenxun.builtin_plugins.plugin_store.data_source import row_style

    init_mocked_api(mocked_api=mocked_api)

    mock_table_page = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.ImageTemplate.table_page"
    )
    mock_table_page_return = mocker.AsyncMock()
    mock_table_page.return_value = mock_table_page_return

    mock_build_message = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.MessageUtils.build_message"
    )
    mock_build_message_return = mocker.AsyncMock()
    mock_build_message.return_value = mock_build_message_return

    author_name = "xuanerwa"

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"搜索插件 {author_name}"
        event: GroupMessageEvent = _v11_group_message_event(
            message=raw_message,
            self_id=BotId.QQ_BOT,
            user_id=UserId.SUPERUSER,
            group_id=GroupId.GROUP_ID_LEVEL_5,
            message_id=MessageId.MESSAGE_ID_3,
            to_me=True,
        )
        ctx.receive_event(bot=bot, event=event)
    mock_table_page.assert_awaited_once_with(
        "商店插件列表",
        "通过添加/移除插件 ID 来管理插件",
        ["-", "ID", "名称", "简介", "作者", "版本", "类型"],
        [
            [
                "",
                4,
                "github订阅",
                "订阅github用户或仓库",
                "xuanerwa",
                "0.7",
                "普通插件",
            ]
        ],
        text_style=row_style,
    )
    mock_build_message.assert_called_once_with(mock_table_page_return)
    mock_build_message_return.send.assert_awaited_once()

    assert mocked_api["basic_plugins"].called
    assert mocked_api["extra_plugins"].called


async def test_plugin_not_exist_search(
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试插件不存在，搜索插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    init_mocked_api(mocked_api=mocked_api)
    plugin_name = "not_exist_plugin_name"

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = f"搜索插件 {plugin_name}"
        event: GroupMessageEvent = _v11_group_message_event(
            message=raw_message,
            self_id=BotId.QQ_BOT,
            user_id=UserId.SUPERUSER,
            group_id=GroupId.GROUP_ID_LEVEL_5,
            message_id=MessageId.MESSAGE_ID_3,
            to_me=True,
        )
        ctx.receive_event(bot=bot, event=event)
        ctx.should_call_send(
            event=event,
            message=Message(message="未找到相关插件..."),
            result=None,
            bot=bot,
        )
