from collections.abc import Callable
import json
import os
from pathlib import Path

import nonebot
from nonebug import NONEBOT_INIT_KWARGS
from nonebug.app import App
from nonebug.mixin.process import MatcherContext
import pytest
from pytest_asyncio import is_async_test
from pytest_mock import MockerFixture
from respx import MockRouter

from tests.config import BotId, GroupId, UserId

nonebot.load_plugin("nonebot_plugin_session")


def get_response_json(path: str) -> dict:
    return json.loads(
        (Path(__file__).parent / "response" / path).read_text(encoding="utf8")
    )


def pytest_collection_modifyitems(items: list[pytest.Item]):
    pytest_asyncio_tests = (item for item in items if is_async_test(item))
    session_scope_marker = pytest.mark.asyncio(loop_scope="session")
    for async_test in pytest_asyncio_tests:
        async_test.add_marker(session_scope_marker, append=False)


def pytest_configure(config: pytest.Config) -> None:
    config.stash[NONEBOT_INIT_KWARGS] = {
        "driver": "~fastapi+~httpx+~websockets",
        "superusers": [UserId.SUPERUSER.__str__()],
        "command_start": [""],
        "session_running_expression": "别急呀,小真寻要宕机了!QAQ",
        "image_to_bytes": False,
        "nickname": ["真寻", "小真寻", "绪山真寻", "小寻子"],
        "session_expire_timeout": 30,
        "self_nickname": "小真寻",
        "db_url": "sqlite://:memory:",
        "platform_superusers": {
            "qq": [UserId.SUPERUSER_QQ.__str__()],
            "dodo": [UserId.SUPERUSER_DODO.__str__()],
        },
        "host": "127.0.0.1",
        "port": 8080,
        "log_level": "INFO",
    }


@pytest.fixture(scope="session", autouse=True)
def _init_bot(nonebug_init: None):
    from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)

    nonebot.load_plugin("nonebot_plugin_alconna")
    nonebot.load_plugin("nonebot_plugin_apscheduler")
    nonebot.load_plugin("nonebot_plugin_htmlrender")
    nonebot.load_plugins("zhenxun/builtin_plugins")
    nonebot.load_plugins("zhenxun/plugins")

    # 手动缓存 uninfo 所需信息
    from nonebot_plugin_uninfo import (
        Scene,
        SceneType,
        Session,
        SupportAdapter,
        SupportScope,
        User,
    )
    from nonebot_plugin_uninfo.adapters.onebot11.main import fetcher as onebot11_fetcher
    from nonebot_plugin_uninfo.adapters.onebot12.main import fetcher as onebot12_fetcher

    onebot11_fetcher.session_cache = {
        f"group_{GroupId.GROUP_ID_LEVEL_5}_{UserId.SUPERUSER}": Session(
            self_id="test",
            adapter=SupportAdapter.onebot11,
            scope=SupportScope.qq_client,
            scene=Scene(str(GroupId.GROUP_ID_LEVEL_0), SceneType.GROUP),
            user=User(str(UserId.SUPERUSER)),
        ),
    }
    onebot12_fetcher.session_cache = {
        f"group_{GroupId.GROUP_ID_LEVEL_5}_{UserId.SUPERUSER}": Session(
            self_id="test",
            adapter=SupportAdapter.onebot12,
            scope=SupportScope.qq_client,
            scene=Scene(str(GroupId.GROUP_ID_LEVEL_0), SceneType.GROUP),
            user=User(str(UserId.SUPERUSER)),
        ),
    }


@pytest.fixture
async def app(app: App, tmp_path: Path, mocker: MockerFixture):
    from zhenxun.services.db_context import disconnect, init

    driver = nonebot.get_driver()
    # 清除连接钩子，现在 NoneBug 会自动触发 on_bot_connect
    driver._bot_connection_hook.clear()
    mock_config_path = mocker.MagicMock()
    mock_config_path.LOG_PATH = tmp_path / "log"
    # mock_config_path.LOG_PATH.mkdir(parents=True, exist_ok=True)
    mock_config_path.DATA_PATH = tmp_path / "data"
    # mock_config_path.DATA_PATH.mkdir(parents=True, exist_ok=True)
    mock_config_path.TEMP_PATH = tmp_path / "resources" / "temp"
    # mock_config_path.TEMP_PATH.mkdir(parents=True, exist_ok=True)

    mocker.patch("zhenxun.configs.path_config", new=mock_config_path)

    await init()
    # await driver._lifespan.startup()
    os.environ["AIOCACHE_DISABLE"] = "1"
    os.environ["PYTEST_CURRENT_TEST"] = "1"

    yield app

    del os.environ["AIOCACHE_DISABLE"]
    # await driver._lifespan.shutdown()
    await disconnect()


@pytest.fixture
def create_bot() -> Callable:
    from nonebot.adapters.onebot.v11 import Adapter, Bot

    def _create_bot(context: MatcherContext) -> Bot:
        return context.create_bot(
            base=Bot,
            adapter=nonebot.get_adapter(Adapter),
            self_id=BotId.QQ_BOT.__str__(),
        )

    return _create_bot


@pytest.fixture
def mocked_api(respx_mock: MockRouter):
    return respx_mock
