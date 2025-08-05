from collections import namedtuple
from collections.abc import Callable
from pathlib import Path
import platform
from typing import cast

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebug import App
from pytest_mock import MockerFixture

from tests.config import BotId, GroupId, MessageId, UserId
from tests.utils import _v11_group_message_event

platform_uname = platform.uname_result(
    system="Linux",
    node="zhenxun",
    release="5.15.0-1027-azure",
    version="#1 SMP Debian 5.15.0-1027-azure",
    machine="x86_64",
)  # type: ignore
cpuinfo_get_cpu_info = {"brand_raw": "Intel(R) Core(TM) i7-10700K"}


def init_mocker(mocker: MockerFixture, tmp_path: Path):
    mock_psutil = mocker.patch("zhenxun.builtin_plugins.check.data_source.psutil")

    # Define namedtuples for complex return values
    CpuFreqs = namedtuple("CpuFreqs", ["current"])  # noqa: PYI024
    VirtualMemoryInfo = namedtuple("VirtualMemoryInfo", ["used", "total", "percent"])  # noqa: PYI024
    SwapInfo = namedtuple("SwapInfo", ["used", "total", "percent"])  # noqa: PYI024
    DiskUsage = namedtuple("DiskUsage", ["used", "total", "free", "percent"])  # noqa: PYI024

    # Set specific return values for psutil methods
    mock_psutil.cpu_percent.return_value = 1.0  # CPU 使用率
    mock_psutil.cpu_freq.return_value = CpuFreqs(current=0.0)  # CPU 频率
    mock_psutil.cpu_count.return_value = 1  # CPU 核心数

    # Memory Info
    mock_psutil.virtual_memory.return_value = VirtualMemoryInfo(
        used=1 * 1024**3,  # 1 GB in bytes for used memory
        total=1 * 1024**3,  # 1 GB in bytes for total memory
        percent=100.0,  # 100% of memory used
    )

    # Swap Info
    mock_psutil.swap_memory.return_value = SwapInfo(
        used=1 * 1024**3,  # 1 GB in bytes for used swap space
        total=1 * 1024**3,  # 1 GB in bytes for total swap space
        percent=100.0,  # 100% of swap space used
    )

    # Disk Usage
    mock_psutil.disk_usage.return_value = DiskUsage(
        used=1 * 1024**3,  # 1 GB in bytes for used disk space
        total=1 * 1024**3,  # 1 GB in bytes for total disk space
        free=0,  # No free space
        percent=100.0,  # 100% of disk space used
    )

    mock_cpuinfo = mocker.patch("zhenxun.builtin_plugins.check.data_source.cpuinfo")
    mock_cpuinfo.get_cpu_info.return_value = cpuinfo_get_cpu_info

    mock_platform = mocker.patch("zhenxun.builtin_plugins.check.data_source.platform")
    mock_platform.uname.return_value = platform_uname

    mock_template_to_pic = mocker.patch("zhenxun.builtin_plugins.check.template_to_pic")
    mock_template_to_pic_return = mocker.AsyncMock()
    mock_template_to_pic.return_value = mock_template_to_pic_return

    mock_build_message = mocker.patch(
        "zhenxun.builtin_plugins.check.MessageUtils.build_message"
    )
    mock_build_message_return = mocker.AsyncMock()
    mock_build_message.return_value = mock_build_message_return

    mock_template_path_new = tmp_path / "resources" / "template"
    mocker.patch(
        "zhenxun.builtin_plugins.check.TEMPLATE_PATH", new=mock_template_path_new
    )
    return (
        mock_psutil,
        mock_cpuinfo,
        mock_platform,
        mock_template_to_pic,
        mock_template_to_pic_return,
        mock_build_message,
        mock_build_message_return,
        mock_template_path_new,
    )


async def test_check(
    app: App,
    mocker: MockerFixture,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试自检
    """
    from zhenxun.builtin_plugins.check import _self_check_matcher

    (
        mock_psutil,
        mock_cpuinfo,
        mock_platform,
        mock_template_to_pic,
        mock_template_to_pic_return,
        mock_build_message,
        mock_build_message_return,
        mock_template_path_new,
    ) = init_mocker(mocker, tmp_path)
    async with app.test_matcher(_self_check_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = "自检"
        event: GroupMessageEvent = _v11_group_message_event(
            message=raw_message,
            self_id=BotId.QQ_BOT,
            user_id=UserId.SUPERUSER,
            group_id=GroupId.GROUP_ID_LEVEL_5,
            message_id=MessageId.MESSAGE_ID_3,
            to_me=True,
        )
        ctx.receive_event(bot=bot, event=event)
        ctx.should_ignore_rule(_self_check_matcher)

    mock_template_to_pic.assert_awaited_once()
    mock_build_message.assert_called_once_with(mock_template_to_pic_return)
    mock_build_message_return.send.assert_awaited_once()


async def test_check_arm(
    app: App,
    mocker: MockerFixture,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试自检（arm）
    """
    from zhenxun.builtin_plugins.check import _self_check_matcher

    platform_uname_arm = platform.uname_result(
        system="Linux",
        node="zhenxun",
        release="5.15.0-1017-oracle",
        version="#22~20.04.1-Ubuntu SMP Wed Aug 24 11:13:15 UTC 2022",
        machine="aarch64",
    )  # type: ignore
    mock_subprocess_check_output = mocker.patch(
        "zhenxun.builtin_plugins.check.data_source.subprocess.check_output"
    )
    mock_environ_copy = mocker.patch(
        "zhenxun.builtin_plugins.check.data_source.os.environ.copy"
    )
    mock_environ_copy_return = mocker.MagicMock()
    mock_environ_copy.return_value = mock_environ_copy_return
    (
        mock_psutil,
        mock_cpuinfo,
        mock_platform,
        mock_template_to_pic,
        mock_template_to_pic_return,
        mock_build_message,
        mock_build_message_return,
        mock_template_path_new,
    ) = init_mocker(mocker, tmp_path)

    mock_platform.uname.return_value = platform_uname_arm
    mock_cpuinfo.get_cpu_info.return_value = {}
    mock_psutil.cpu_freq.return_value = {}

    async with app.test_matcher(_self_check_matcher) as ctx:
        bot = create_bot(ctx)
        bot: Bot = cast(Bot, bot)
        raw_message = "自检"
        event: GroupMessageEvent = _v11_group_message_event(
            message=raw_message,
            self_id=BotId.QQ_BOT,
            user_id=UserId.SUPERUSER,
            group_id=GroupId.GROUP_ID_LEVEL_5,
            message_id=MessageId.MESSAGE_ID_3,
            to_me=True,
        )
        ctx.receive_event(bot=bot, event=event)
        ctx.should_ignore_rule(_self_check_matcher)
    mock_subprocess_check_output.assert_has_calls(
        [
            mocker.call(["lscpu"], env=mock_environ_copy_return),
            mocker.call().decode(),
            mocker.call().decode().splitlines(),
            mocker.call().decode().splitlines().__iter__(),
            mocker.call(["dmidecode", "-s", "processor-frequency"]),
            mocker.call().decode(),
            mocker.call().decode().split(),
            mocker.call().decode().split().__getitem__(0),
            mocker.call().decode().split().__getitem__().__float__(),
        ]  # type: ignore
    )
    mock_template_to_pic.assert_awaited_once()
    mock_build_message.assert_called_once_with(mock_template_to_pic_return)
    mock_build_message_return.send.assert_awaited_once()
