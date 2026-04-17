import json
import sys
from types import SimpleNamespace
from typing import cast

from nonebot.adapters import Bot
import pytest
from pytest_mock import MockerFixture
from tortoise.exceptions import ConfigurationError


def test_build_restart_command_prefers_uv(mocker: MockerFixture) -> None:
    from zhenxun.utils import _restart_utils

    mocker.patch("shutil.which", return_value="C:\\tools\\uv.exe")

    assert _restart_utils._build_restart_command() == [
        "C:\\tools\\uv.exe",
        "run",
        "zx",
    ]


def test_build_restart_command_falls_back_to_cli_module(
    mocker: MockerFixture,
) -> None:
    from zhenxun.utils import _restart_utils

    mocker.patch("shutil.which", return_value=None)
    mocker.patch.object(sys, "executable", "C:\\Python310\\python.exe")

    assert _restart_utils._build_restart_command() == [
        "C:\\Python310\\python.exe",
        "-m",
        "zhenxun.cli",
        "run",
    ]


@pytest.mark.asyncio
async def test_request_restart_uses_ticket_and_records_source(
    tmp_path,
    mocker: MockerFixture,
) -> None:
    from zhenxun.utils import _restart_utils

    mocker.patch.object(
        _restart_utils,
        "_RESTART_STATE_FILE",
        tmp_path / ".restart_state.json",
    )
    schedule = mocker.patch(
        "zhenxun.utils._restart_utils._schedule_restart",
        new=mocker.AsyncMock(return_value=(True, "执行重启命令成功")),
    )

    _restart_utils.issue_restart_ticket("webui.configure", ttl_seconds=600)
    ok, message = await _restart_utils.request_restart(
        "webui.configure",
        require_ticket="webui.configure",
    )

    state = json.loads((_restart_utils._RESTART_STATE_FILE).read_text(encoding="utf-8"))

    assert ok is True
    assert message == "执行重启命令成功"
    assert "restart_ticket" not in state
    assert state["pending_request"]["source"] == "webui.configure"
    schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_restart_connect_sends_receipt_and_clears_state(
    tmp_path,
    mocker: MockerFixture,
) -> None:
    from zhenxun.utils import _restart_utils

    mocker.patch.object(
        _restart_utils,
        "_RESTART_STATE_FILE",
        tmp_path / ".restart_state.json",
    )
    _restart_utils._write_restart_state(
        {
            "pending_request": {
                "source": "command.matcher",
                "requested_at": 1.0,
                "receipt": {
                    "bot_id": "test-bot",
                    "user_id": "123456",
                },
            }
        }
    )

    target = object()
    mocker.patch("zhenxun.utils.platform.PlatformUtils.get_target", return_value=target)
    send = mocker.AsyncMock()
    build_message = mocker.patch("zhenxun.utils.message.MessageUtils.build_message")
    build_message.return_value = SimpleNamespace(send=send)
    bot = cast(Bot, SimpleNamespace(self_id="test-bot"))

    await _restart_utils.handle_restart_connect(bot)

    build_message.assert_called_once()
    send.assert_awaited_once_with(target, bot=bot)
    assert not (_restart_utils._RESTART_STATE_FILE).exists()


@pytest.mark.asyncio
async def test_disconnect_skips_unconfigured_connection(
    mocker: MockerFixture,
) -> None:
    from zhenxun.services.db_context import disconnect

    close_all = mocker.patch(
        "zhenxun.services.db_context.connections.close_all",
        side_effect=ConfigurationError("not initialized"),
    )
    debug = mocker.patch("zhenxun.services.db_context.logger.debug")

    await disconnect()

    close_all.assert_awaited_once()
    debug.assert_called_once()
