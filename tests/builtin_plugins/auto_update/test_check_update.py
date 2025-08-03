from collections.abc import Callable
import io
import os
from pathlib import Path
import tarfile
from typing import cast
import zipfile

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.message import Message
from nonebug import App
from pytest_mock import MockerFixture
from respx import MockRouter

from tests.config import BotId, GroupId, MessageId, UserId
from tests.utils import (
    _v11_group_message_event,
    _v11_private_message_send,
    get_reply_cq,
)
from tests.utils import get_response_json as _get_response_json


def get_response_json(file: str) -> dict:
    return _get_response_json(Path() / "auto_update", file)


def init_mocked_api(mocked_api: MockRouter) -> None:
    mocked_api.get(
        url="https://api.github.com/repos/HibiKier/zhenxun_bot/releases/latest",
        name="release_latest",
    ).respond(json=get_response_json("release_latest.json"))

    mocked_api.head(
        url="https://raw.githubusercontent.com/",
        name="head_raw",
    ).respond(text="")
    mocked_api.head(
        url="https://github.com/",
        name="head_github",
    ).respond(text="")
    mocked_api.head(
        url="https://codeload.github.com/",
        name="head_codeload",
    ).respond(text="")

    mocked_api.get(
        url="https://raw.githubusercontent.com/HibiKier/zhenxun_bot/dev/__version__",
        name="dev_branch_version",
    ).respond(text="__version__: v0.2.2-e6f17c4")
    mocked_api.get(
        url="https://raw.githubusercontent.com/HibiKier/zhenxun_bot/main/__version__",
        name="main_branch_version",
    ).respond(text="__version__: v0.2.2-e6f17c4")
    mocked_api.get(
        url="https://api.github.com/repos/HibiKier/zhenxun_bot/tarball/v0.2.2",
        name="release_download_url",
    ).respond(
        status_code=302,
        headers={
            "Location": "https://codeload.github.com/HibiKier/zhenxun_bot/legacy.tar.gz/refs/tags/v0.2.2"
        },
    )

    tar_buffer = io.BytesIO()
    zip_bytes = io.BytesIO()

    from zhenxun.builtin_plugins.auto_update.config import (
        PYPROJECT_FILE_STRING,
        PYPROJECT_LOCK_FILE_STRING,
        REPLACE_FOLDERS,
        REQ_TXT_FILE_STRING,
    )

    # 指定要添加到压缩文件中的文件路径列表
    file_paths: list[str] = [
        PYPROJECT_FILE_STRING,
        PYPROJECT_LOCK_FILE_STRING,
        REQ_TXT_FILE_STRING,
    ]

    # 打开一个tarfile对象，写入到上面创建的BytesIO对象中
    with tarfile.open(mode="w:gz", fileobj=tar_buffer) as tar:
        add_files_and_folders_to_tar(tar, file_paths, folders=REPLACE_FOLDERS)

    with zipfile.ZipFile(zip_bytes, mode="w", compression=zipfile.ZIP_DEFLATED) as zipf:
        add_files_and_folders_to_zip(zipf, file_paths, folders=REPLACE_FOLDERS)

    mocked_api.get(
        url="https://codeload.github.com/HibiKier/zhenxun_bot/legacy.tar.gz/refs/tags/v0.2.2",
        name="release_download_url_redirect",
    ).respond(
        content=tar_buffer.getvalue(),
    )
    mocked_api.get(
        url="https://github.com/HibiKier/zhenxun_bot/archive/refs/heads/dev.zip",
        name="dev_download_url",
    ).respond(
        content=zip_bytes.getvalue(),
    )
    mocked_api.get(
        url="https://github.com/HibiKier/zhenxun_bot/archive/refs/heads/main.zip",
        name="main_download_url",
    ).respond(
        content=zip_bytes.getvalue(),
    )


# TODO Rename this here and in `init_mocked_api`
def add_files_and_folders_to_zip(
    zipf: zipfile.ZipFile, file_paths: list[str], folders: list[str] = []
):
    """Add files and folders to a zip archive.

    This function creates a directory structure within the specified zip
    archive and adds the provided files to it. It also creates additional
    subdirectories as specified in the folders list.

    Args:
        zipf: The zip archive to which files and folders will be added.
        file_paths: A list of file names to be added to the zip archive.
        folders: An optional list of subdirectory names to be created
                 within the base folder.
    """

    # 假设有一个文件夹名为 folder_name
    folder_name = "my_folder/"

    # 添加文件夹到 ZIP 中，注意 ZIP 中文件夹路径应以 '/' 结尾
    zipf.writestr(folder_name, "")  # 空内容表示这是一个文件夹

    for file_path in file_paths:
        # 将文件添加到 ZIP 中，路径为 folder_name + file_name
        zipf.writestr(f"{folder_name}{os.path.basename(file_path)}", b"new")
    base_folder = f"{folder_name}zhenxun/"
    zipf.writestr(base_folder, "")

    for folder in folders:
        zipf.writestr(f"{base_folder}{folder}/", "")


# TODO Rename this here and in `init_mocked_api`
def add_files_and_folders_to_tar(
    tar: tarfile.TarFile, file_paths: list[str], folders: list[str] = []
):
    """Add files and folders to a tar archive.

    This function creates a directory structure within the specified tar
    archive and adds the provided files to it. It also creates additional
    subdirectories as specified in the folders list.

    Args:
        tar: The tar archive to which files and folders will be added.
        file_paths: A list of file names to be added to the tar archive.
        folders: An optional list of subdirectory names to be created
                 within the base folder.
    """

    folder_name = "my_folder"
    tarinfo = tarfile.TarInfo(folder_name)
    add_directory_to_tar(tarinfo, tar)
    # 读取并添加指定的文件
    for file_path in file_paths:
        # 创建TarInfo对象
        tar_buffer = io.BytesIO(b"new")
        tarinfo = tarfile.TarInfo(
            f"{folder_name}/{file_path}"
        )  # 使用文件名作为tar中的名字
        tarinfo.mode = 0o644  # 设置文件夹权限
        tarinfo.size = len(tar_buffer.getvalue())  # 设置文件大小

        # 添加文件
        tar.addfile(tarinfo, fileobj=tar_buffer)

    base_folder = f"{folder_name}/zhenxun"
    tarinfo = tarfile.TarInfo(base_folder)
    add_directory_to_tar(tarinfo, tar)
    for folder in folders:
        tarinfo = tarfile.TarInfo(f"{base_folder}{folder}")
        add_directory_to_tar(tarinfo, tar)


# TODO Rename this here and in `_extracted_from_init_mocked_api_43`
def add_directory_to_tar(tarinfo, tar):
    """Add a directory entry to a tar archive.

    This function modifies the provided tarinfo object to set its type
    as a directory and assigns the appropriate permissions before adding
    it to the specified tar archive.

    Args:
        tarinfo: The tarinfo object representing the directory.
        tar: The tar archive to which the directory will be added.
    """

    tarinfo.type = tarfile.DIRTYPE
    tarinfo.mode = 0o755
    tar.addfile(tarinfo)


def init_mocker_path(mocker: MockerFixture, tmp_path: Path):
    from zhenxun.builtin_plugins.auto_update.config import (
        PYPROJECT_FILE_STRING,
        PYPROJECT_LOCK_FILE_STRING,
        REQ_TXT_FILE_STRING,
        VERSION_FILE_STRING,
    )

    mocker.patch(
        "zhenxun.utils.manager.virtual_env_package_manager.VirtualEnvPackageManager.install_requirement",
        return_value=None,
    )
    mock_tmp_path = mocker.patch(
        "zhenxun.builtin_plugins.auto_update._data_source.TMP_PATH",
        new=tmp_path / "auto_update",
    )
    mock_base_path = mocker.patch(
        "zhenxun.builtin_plugins.auto_update._data_source.BASE_PATH",
        new=tmp_path / "zhenxun",
    )
    mock_backup_path = mocker.patch(
        "zhenxun.builtin_plugins.auto_update._data_source.BACKUP_PATH",
        new=tmp_path / "backup",
    )
    mock_download_gz_file = mocker.patch(
        "zhenxun.builtin_plugins.auto_update._data_source.DOWNLOAD_GZ_FILE",
        new=mock_tmp_path / "download_latest_file.tar.gz",
    )
    mock_download_zip_file = mocker.patch(
        "zhenxun.builtin_plugins.auto_update._data_source.DOWNLOAD_ZIP_FILE",
        new=mock_tmp_path / "download_latest_file.zip",
    )
    mock_pyproject_file = mocker.patch(
        "zhenxun.builtin_plugins.auto_update._data_source.PYPROJECT_FILE",
        new=tmp_path / PYPROJECT_FILE_STRING,
    )
    mock_pyproject_lock_file = mocker.patch(
        "zhenxun.builtin_plugins.auto_update._data_source.PYPROJECT_LOCK_FILE",
        new=tmp_path / PYPROJECT_LOCK_FILE_STRING,
    )
    mock_req_txt_file = mocker.patch(
        "zhenxun.builtin_plugins.auto_update._data_source.REQ_TXT_FILE",
        new=tmp_path / REQ_TXT_FILE_STRING,
    )
    mock_version_file = mocker.patch(
        "zhenxun.builtin_plugins.auto_update._data_source.VERSION_FILE",
        new=tmp_path / VERSION_FILE_STRING,
    )
    open(mock_version_file, "w").write("__version__: v0.2.2")
    return (
        mock_tmp_path,
        mock_base_path,
        mock_backup_path,
        mock_download_gz_file,
        mock_download_zip_file,
        mock_pyproject_file,
        mock_pyproject_lock_file,
        mock_req_txt_file,
        mock_version_file,
    )


async def test_check_update_release(
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试检查更新（release）
    """
    from zhenxun.builtin_plugins.auto_update import _matcher
    from zhenxun.builtin_plugins.auto_update.config import (
        PYPROJECT_FILE_STRING,
        PYPROJECT_LOCK_FILE_STRING,
        REPLACE_FOLDERS,
        REQ_TXT_FILE_STRING,
    )

    init_mocked_api(mocked_api=mocked_api)

    (
        mock_tmp_path,
        mock_base_path,
        mock_backup_path,
        mock_download_gz_file,
        mock_download_zip_file,
        mock_pyproject_file,
        mock_pyproject_lock_file,
        mock_req_txt_file,
        mock_version_file,
    ) = init_mocker_path(mocker, tmp_path)

    # 确保目录下有一个子目录，以便 os.listdir() 能返回一个目录名
    mock_tmp_path.mkdir(parents=True, exist_ok=True)

    for folder in REPLACE_FOLDERS:
        (mock_base_path / folder).mkdir(parents=True, exist_ok=True)

    mock_pyproject_file.write_bytes(b"")
    mock_pyproject_lock_file.write_bytes(b"")
    mock_req_txt_file.write_bytes(b"")

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot = cast(Bot, bot)
        raw_message = "检查更新 release"
        event = _v11_group_message_event(
            raw_message,
            self_id=BotId.QQ_BOT,
            user_id=UserId.SUPERUSER,
            group_id=GroupId.GROUP_ID_LEVEL_5,
            message_id=MessageId.MESSAGE_ID,
            to_me=True,
        )
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event=event,
            message=Message(f"{get_reply_cq(MessageId.MESSAGE_ID)}正在进行检查更新..."),
            result=None,
            bot=bot,
        )
        ctx.should_call_api(
            "send_msg",
            _v11_private_message_send(
                message="检测真寻已更新，版本更新：v0.2.2 -> v0.2.2\n开始更新...",
                user_id=UserId.SUPERUSER,
            ),
        )
        ctx.should_call_send(
            event=event,
            message=Message(
                "版本更新完成\n版本: v0.2.2 -> v0.2.2\n请重新启动真寻以完成更新!"
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished(_matcher)
    assert mocked_api["release_latest"].called
    assert mocked_api["release_download_url_redirect"].called

    assert (mock_backup_path / PYPROJECT_FILE_STRING).exists()
    assert (mock_backup_path / PYPROJECT_LOCK_FILE_STRING).exists()
    assert (mock_backup_path / REQ_TXT_FILE_STRING).exists()

    assert not mock_download_gz_file.exists()
    assert not mock_download_zip_file.exists()

    assert mock_pyproject_file.read_bytes() == b"new"
    assert mock_pyproject_lock_file.read_bytes() == b"new"
    assert mock_req_txt_file.read_bytes() == b"new"

    for folder in REPLACE_FOLDERS:
        assert not (mock_base_path / folder).exists()
    for folder in REPLACE_FOLDERS:
        assert (mock_backup_path / folder).exists()


async def test_check_update_main(
    app: App,
    mocker: MockerFixture,
    mocked_api: MockRouter,
    create_bot: Callable,
    tmp_path: Path,
) -> None:
    """
    测试检查更新（正式环境）
    """
    from zhenxun.builtin_plugins.auto_update import _matcher
    from zhenxun.builtin_plugins.auto_update.config import (
        PYPROJECT_FILE_STRING,
        PYPROJECT_LOCK_FILE_STRING,
        REPLACE_FOLDERS,
        REQ_TXT_FILE_STRING,
    )

    init_mocked_api(mocked_api=mocked_api)

    (
        mock_tmp_path,
        mock_base_path,
        mock_backup_path,
        mock_download_gz_file,
        mock_download_zip_file,
        mock_pyproject_file,
        mock_pyproject_lock_file,
        mock_req_txt_file,
        mock_version_file,
    ) = init_mocker_path(mocker, tmp_path)

    # 确保目录下有一个子目录，以便 os.listdir() 能返回一个目录名
    mock_tmp_path.mkdir(parents=True, exist_ok=True)
    for folder in REPLACE_FOLDERS:
        (mock_base_path / folder).mkdir(parents=True, exist_ok=True)

    mock_pyproject_file.write_bytes(b"")
    mock_pyproject_lock_file.write_bytes(b"")
    mock_req_txt_file.write_bytes(b"")

    async with app.test_matcher(_matcher) as ctx:
        bot = create_bot(ctx)
        bot = cast(Bot, bot)
        raw_message = "检查更新 main -r"
        event = _v11_group_message_event(
            raw_message,
            self_id=BotId.QQ_BOT,
            user_id=UserId.SUPERUSER,
            group_id=GroupId.GROUP_ID_LEVEL_5,
            message_id=MessageId.MESSAGE_ID,
            to_me=True,
        )
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event=event,
            message=Message(f"{get_reply_cq(MessageId.MESSAGE_ID)}正在进行检查更新..."),
            result=None,
            bot=bot,
        )
        ctx.should_call_api(
            "send_msg",
            _v11_private_message_send(
                message="检测真寻已更新，版本更新：v0.2.2 -> v0.2.2-e6f17c4\n"
                "开始更新...",
                user_id=UserId.SUPERUSER,
            ),
        )
        ctx.should_call_send(
            event=event,
            message=Message(
                "版本更新完成\n"
                "版本: v0.2.2 -> v0.2.2-e6f17c4\n"
                "请重新启动真寻以完成更新!\n"
                "资源文件更新成功！"
            ),
            result=None,
            bot=bot,
        )
        ctx.should_finished(_matcher)
    assert mocked_api["main_download_url"].called
    assert (mock_backup_path / PYPROJECT_FILE_STRING).exists()
    assert (mock_backup_path / PYPROJECT_LOCK_FILE_STRING).exists()
    assert (mock_backup_path / REQ_TXT_FILE_STRING).exists()

    assert not mock_download_gz_file.exists()
    assert not mock_download_zip_file.exists()

    assert mock_pyproject_file.read_bytes() == b"new"
    assert mock_pyproject_lock_file.read_bytes() == b"new"
    assert mock_req_txt_file.read_bytes() == b"new"

    for folder in REPLACE_FOLDERS:
        assert (mock_base_path / folder).exists()
    for folder in REPLACE_FOLDERS:
        assert (mock_backup_path / folder).exists()
