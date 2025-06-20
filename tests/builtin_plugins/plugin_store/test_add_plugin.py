from collections.abc import Callable
from pathlib import Path
from typing import cast

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebug import App
import pytest
from pytest_mock import MockerFixture
from respx import MockRouter

from tests.builtin_plugins.plugin_store.utils import init_mocked_api
from tests.config import BotId, GroupId, MessageId, UserId
from tests.utils import _v11_group_message_event


@pytest.mark.parametrize("package_api", ["jsd", "gh"])
@pytest.mark.parametrize("is_commit", [True, False])
async def test_add_plugin_basic(
    package_api: str,
    is_commit: bool,
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试添加基础插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    init_mocked_api(mocked_api=mocked_api)
    mock_base_path = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )

    if package_api != "jsd":
        mocked_api["zhenxun_bot_plugins_metadata"].respond(404)
    if package_api != "gh":
        mocked_api["zhenxun_bot_plugins_tree"].respond(404)

    if not is_commit:
        mocked_api["zhenxun_bot_plugins_commit"].respond(404)
        mocked_api["zhenxun_bot_plugins_commit_proxy"].respond(404)
        mocked_api["zhenxun_bot_plugins_index_commit"].respond(404)
        mocked_api["zhenxun_bot_plugins_index_commit_proxy"].respond(404)

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
            message=Message(message="插件 识图 安装成功! 重启后生效"),
            result=None,
            bot=bot,
        )
    if is_commit:
        assert mocked_api["search_image_plugin_file_init_commit"].called
        assert mocked_api["basic_plugins"].called
        assert mocked_api["extra_plugins"].called
    else:
        assert mocked_api["search_image_plugin_file_init"].called
        assert mocked_api["basic_plugins_no_commit"].called
        assert mocked_api["extra_plugins_no_commit"].called
    assert (mock_base_path / "plugins" / "search_image" / "__init__.py").is_file()


@pytest.mark.parametrize("package_api", ["jsd", "gh"])
@pytest.mark.parametrize("is_commit", [True, False])
async def test_add_plugin_basic_commit_version(
    package_api: str,
    is_commit: bool,
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试添加基础插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    init_mocked_api(mocked_api=mocked_api)
    mock_base_path = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )

    if package_api != "jsd":
        mocked_api["zhenxun_bot_plugins_metadata_commit"].respond(404)
    if package_api != "gh":
        mocked_api["zhenxun_bot_plugins_tree_commit"].respond(404)

    if not is_commit:
        mocked_api["zhenxun_bot_plugins_commit"].respond(404)
        mocked_api["zhenxun_bot_plugins_commit_proxy"].respond(404)
        mocked_api["zhenxun_bot_plugins_index_commit"].respond(404)
        mocked_api["zhenxun_bot_plugins_index_commit_proxy"].respond(404)
    plugin_id = 3

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
            message=Message(message="插件 B站订阅 安装成功! 重启后生效"),
            result=None,
            bot=bot,
        )
    if package_api == "jsd":
        assert mocked_api["zhenxun_bot_plugins_metadata_commit"].called
    if package_api == "gh":
        assert mocked_api["zhenxun_bot_plugins_tree_commit"].called
    if is_commit:
        assert mocked_api["basic_plugins"].called
        assert mocked_api["extra_plugins"].called
    else:
        assert mocked_api["basic_plugins_no_commit"].called
        assert mocked_api["extra_plugins_no_commit"].called
    assert mocked_api["bilibili_sub_plugin_file_init"].called
    assert (mock_base_path / "plugins" / "bilibili_sub" / "__init__.py").is_file()


@pytest.mark.parametrize("package_api", ["jsd", "gh"])
@pytest.mark.parametrize("is_commit", [True, False])
async def test_add_plugin_basic_is_not_dir(
    package_api: str,
    is_commit: bool,
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试添加基础插件，插件不是目录
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    init_mocked_api(mocked_api=mocked_api)
    mock_base_path = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )

    if package_api != "jsd":
        mocked_api["zhenxun_bot_plugins_metadata"].respond(404)
    if package_api != "gh":
        mocked_api["zhenxun_bot_plugins_tree"].respond(404)

    if not is_commit:
        mocked_api["zhenxun_bot_plugins_commit"].respond(404)
        mocked_api["zhenxun_bot_plugins_commit_proxy"].respond(404)
        mocked_api["zhenxun_bot_plugins_index_commit"].respond(404)
        mocked_api["zhenxun_bot_plugins_index_commit_proxy"].respond(404)

    plugin_id = 0

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
            message=Message(message="插件 鸡汤 安装成功! 重启后生效"),
            result=None,
            bot=bot,
        )
    if is_commit:
        assert mocked_api["jitang_plugin_file_commit"].called
        assert mocked_api["basic_plugins"].called
        assert mocked_api["extra_plugins"].called
    else:
        assert mocked_api["jitang_plugin_file"].called
        assert mocked_api["basic_plugins_no_commit"].called
        assert mocked_api["extra_plugins_no_commit"].called
    assert (mock_base_path / "plugins" / "alapi" / "jitang.py").is_file()


@pytest.mark.parametrize("package_api", ["jsd", "gh"])
@pytest.mark.parametrize("is_commit", [True, False])
async def test_add_plugin_extra(
    package_api: str,
    is_commit: bool,
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试添加额外插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    init_mocked_api(mocked_api=mocked_api)
    mock_base_path = mocker.patch(
        "zhenxun.builtin_plugins.plugin_store.data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )

    if package_api != "jsd":
        mocked_api["zhenxun_github_sub_metadata"].respond(404)
    if package_api != "gh":
        mocked_api["zhenxun_github_sub_tree"].respond(404)

    if not is_commit:
        mocked_api["zhenxun_github_sub_commit"].respond(404)
        mocked_api["zhenxun_github_sub_commit_proxy"].respond(404)
        mocked_api["zhenxun_bot_plugins_commit"].respond(404)
        mocked_api["zhenxun_bot_plugins_commit_proxy"].respond(404)
        mocked_api["zhenxun_bot_plugins_index_commit"].respond(404)
        mocked_api["zhenxun_bot_plugins_index_commit_proxy"].respond(404)

    plugin_id = 4

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
            message=Message(message=f"正在添加插件 Id: {plugin_id}"),
            result=None,
            bot=bot,
        )
        ctx.should_call_send(
            event=event,
            message=Message(message="插件 github订阅 安装成功! 重启后生效"),
            result=None,
            bot=bot,
        )
    if is_commit:
        assert mocked_api["github_sub_plugin_file_init_commit"].called
        assert mocked_api["basic_plugins"].called
        assert mocked_api["extra_plugins"].called
    else:
        assert mocked_api["github_sub_plugin_file_init"].called
        assert mocked_api["basic_plugins_no_commit"].called
        assert mocked_api["extra_plugins_no_commit"].called
    assert (mock_base_path / "plugins" / "github_sub" / "__init__.py").is_file()


async def test_plugin_not_exist_add(
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试插件不存在，添加插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    init_mocked_api(mocked_api=mocked_api)
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
            message=Message(message="插件ID不存在..."),
            result=None,
            bot=bot,
        )


async def test_add_plugin_exist(
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试插件已经存在，添加插件
    """
    from zhenxun.builtin_plugins.plugin_store import _matcher

    init_mocked_api(mocked_api=mocked_api)
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
            message=Message(message="插件 识图 已安装，无需重复安装"),
            result=None,
            bot=bot,
        )
