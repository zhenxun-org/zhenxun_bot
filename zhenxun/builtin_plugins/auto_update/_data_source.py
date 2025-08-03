import os
from pathlib import Path
import shutil
import tarfile
import zipfile

from nonebot.adapters import Bot
from nonebot.utils import run_sync

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger
from zhenxun.utils.github_utils import GithubUtils
from zhenxun.utils.github_utils.models import RepoInfo
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.manager.virtual_env_package_manager import VirtualEnvPackageManager
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.repo_utils import AliyunRepoManager, GithubRepoManager

from .config import (
    BACKUP_PATH,
    BASE_PATH,
    BASE_PATH_STRING,
    COMMAND,
    DEFAULT_GITHUB_URL,
    DOWNLOAD_GZ_FILE,
    DOWNLOAD_ZIP_FILE,
    GIT_GITHUB_URL,
    GIT_WEBUI_UI_URL,
    PYPROJECT_FILE,
    PYPROJECT_FILE_STRING,
    PYPROJECT_LOCK_FILE,
    PYPROJECT_LOCK_FILE_STRING,
    RELEASE_URL,
    REPLACE_FOLDERS,
    REQ_TXT_FILE,
    REQ_TXT_FILE_STRING,
    TMP_PATH,
    VERSION_FILE,
)


@run_sync
def _file_handle(latest_version: str | None):
    """文件移动操作

    参数:
        latest_version: 版本号
    """
    BACKUP_PATH.mkdir(exist_ok=True, parents=True)
    logger.debug("开始解压文件压缩包...", COMMAND)
    download_file = DOWNLOAD_GZ_FILE
    if DOWNLOAD_GZ_FILE.exists():
        tf = tarfile.open(DOWNLOAD_GZ_FILE)
    else:
        download_file = DOWNLOAD_ZIP_FILE
        tf = zipfile.ZipFile(DOWNLOAD_ZIP_FILE)
    tf.extractall(TMP_PATH)
    logger.debug("解压文件压缩包完成...", COMMAND)
    download_file_path = TMP_PATH / next(
        x for x in os.listdir(TMP_PATH) if (TMP_PATH / x).is_dir()
    )
    _pyproject = download_file_path / PYPROJECT_FILE_STRING
    _lock_file = download_file_path / PYPROJECT_LOCK_FILE_STRING
    _req_file = download_file_path / REQ_TXT_FILE_STRING
    extract_path = download_file_path / BASE_PATH_STRING
    target_path = BASE_PATH
    if PYPROJECT_FILE.exists():
        logger.debug(f"移除备份文件: {PYPROJECT_FILE}", COMMAND)
        shutil.move(PYPROJECT_FILE, BACKUP_PATH / PYPROJECT_FILE_STRING)
    if PYPROJECT_LOCK_FILE.exists():
        logger.debug(f"移除备份文件: {PYPROJECT_LOCK_FILE}", COMMAND)
        shutil.move(PYPROJECT_LOCK_FILE, BACKUP_PATH / PYPROJECT_LOCK_FILE_STRING)
    if REQ_TXT_FILE.exists():
        logger.debug(f"移除备份文件: {REQ_TXT_FILE}", COMMAND)
        shutil.move(REQ_TXT_FILE, BACKUP_PATH / REQ_TXT_FILE_STRING)
    if _pyproject.exists():
        logger.debug("移动文件: pyproject.toml", COMMAND)
        shutil.move(_pyproject, PYPROJECT_FILE)
    if _lock_file.exists():
        logger.debug("移动文件: poetry.lock", COMMAND)
        shutil.move(_lock_file, PYPROJECT_LOCK_FILE)
    if _req_file.exists():
        logger.debug("移动文件: requirements.txt", COMMAND)
        shutil.move(_req_file, REQ_TXT_FILE)
    for folder in REPLACE_FOLDERS:
        """移动指定文件夹"""
        _dir = BASE_PATH / folder
        _backup_dir = BACKUP_PATH / folder
        if _backup_dir.exists():
            logger.debug(f"删除备份文件夹 {_backup_dir}", COMMAND)
            shutil.rmtree(_backup_dir)
        if _dir.exists():
            logger.debug(f"移动旧文件夹 {_dir}", COMMAND)
            shutil.move(_dir, _backup_dir)
        else:
            logger.warning(f"文件夹 {_dir} 不存在，跳过删除", COMMAND)
    for folder in REPLACE_FOLDERS:
        src_folder_path = extract_path / folder
        dest_folder_path = target_path / folder
        if src_folder_path.exists():
            logger.debug(
                f"移动文件夹: {src_folder_path} -> {dest_folder_path}", COMMAND
            )
            shutil.move(src_folder_path, dest_folder_path)
        else:
            logger.debug(f"源文件夹不存在: {src_folder_path}", COMMAND)
    if tf:
        tf.close()
    if download_file.exists():
        logger.debug(f"删除下载文件: {download_file}", COMMAND)
        download_file.unlink()
    if extract_path.exists():
        logger.debug(f"删除解压文件夹: {extract_path}", COMMAND)
        shutil.rmtree(extract_path)
    if TMP_PATH.exists():
        shutil.rmtree(TMP_PATH)
    if latest_version:
        with open(VERSION_FILE, "w", encoding="utf8") as f:
            f.write(f"__version__: {latest_version}")


class UpdateManager:
    @classmethod
    async def update_webui(cls, is_zip: bool, source: str) -> str:
        from zhenxun.builtin_plugins.web_ui.public.data_source import (
            update_webui_assets,
        )

        WEBUI_PATH = DATA_PATH / "web_ui" / "public"
        BACKUP_PATH = DATA_PATH / "web_ui" / "backup_public"
        GIT_WEBUI_PATH = DATA_PATH / "web_ui" / "git_web_ui"
        if WEBUI_PATH.exists():
            if BACKUP_PATH.exists():
                logger.debug(f"删除旧的备份webui文件夹 {BACKUP_PATH}", COMMAND)
                shutil.rmtree(BACKUP_PATH)
            WEBUI_PATH.rename(BACKUP_PATH)
        try:
            if is_zip:
                await update_webui_assets()
                logger.info("更新webui成功...", COMMAND)
            else:
                if source == "ali":
                    result = await AliyunRepoManager.update(
                        GIT_WEBUI_UI_URL, GIT_WEBUI_PATH, "dist", force=True
                    )
                else:
                    result = await GithubRepoManager.update(
                        GIT_WEBUI_UI_URL, GIT_WEBUI_PATH, "dist", force=True
                    )
                if not result.success:
                    return f"Webui更新失败...错误: {result.error_message}"
                shutil.rmtree(WEBUI_PATH, ignore_errors=True)
                shutil.copytree(GIT_WEBUI_PATH / "dist", WEBUI_PATH)
            if BACKUP_PATH.exists():
                logger.debug(f"删除旧的webui文件夹 {BACKUP_PATH}", COMMAND)
                shutil.rmtree(BACKUP_PATH)
            return "Webui更新成功！"
        except Exception as e:
            logger.error("更新webui失败...", COMMAND, e=e)
            if BACKUP_PATH.exists():
                logger.debug(f"恢复旧的webui文件夹 {BACKUP_PATH}", COMMAND)
                BACKUP_PATH.rename(WEBUI_PATH)
            raise e

    @classmethod
    async def check_version(cls) -> str:
        """检查更新版本

        返回:
            str: 更新信息
        """
        cur_version = cls.__get_version()
        data = await cls.__get_latest_data()
        if not data:
            return "检查更新获取版本失败..."
        return (
            "检测到当前版本更新\n"
            f"当前版本：{cur_version}\n"
            f"最新版本：{data.get('name')}\n"
            f"创建日期：{data.get('created_at')}\n"
            f"更新内容：\n{data.get('body')}"
        )

    @classmethod
    async def __zip_update(cls, version_type: str):
        logger.info("开始下载真寻最新版文件....", COMMAND)
        cur_version = cls.__get_version()
        url = None
        new_version = None
        repo_info = GithubUtils.parse_github_url(DEFAULT_GITHUB_URL)
        if version_type in {"main"}:
            repo_info.branch = version_type
            new_version = await cls.__get_version_from_repo(repo_info)
            if new_version:
                new_version = new_version.split(":")[-1].strip()
            url = await repo_info.get_archive_download_urls()
        elif version_type == "release":
            data = await cls.__get_latest_data()
            if not data:
                return "获取更新版本失败..."
            new_version = data.get("name", "")
            url = await repo_info.get_release_source_download_urls_tgz(new_version)
        if not url:
            return "获取版本下载链接失败..."
        if TMP_PATH.exists():
            logger.debug(f"删除临时文件夹 {TMP_PATH}", COMMAND)
            shutil.rmtree(TMP_PATH)
        logger.debug(
            f"开始更新版本：{cur_version} -> {new_version} | 下载链接：{url}",
            COMMAND,
        )
        download_file = (
            DOWNLOAD_GZ_FILE if version_type == "release" else DOWNLOAD_ZIP_FILE
        )
        if await AsyncHttpx.download_file(url, download_file, stream=True):
            logger.debug("下载真寻最新版文件完成...", COMMAND)
            await _file_handle(new_version)
            result = "版本更新完成"
            return (
                f"{result}\n"
                f"版本: {cur_version} -> {new_version}\n"
                "请重新启动真寻以完成更新!"
            )
        else:
            logger.debug("下载真寻最新版文件失败...", COMMAND)
        return ""

    @classmethod
    async def update(
        cls,
        bot: Bot,
        user_id: str,
        version_type: str,
        force: bool,
        source: str,
        zip: bool,
        update_type: str,
    ) -> str:
        """更新操作

        参数:
            bot: Bot
            user_id: 用户id
            version_type: 更新版本类型
            force: 是否强制更新
            source: 更新源
            zip: 是否下载zip文件
            update_type: 更新方式

        返回:
            str | None: 返回消息
        """
        cur_version = cls.__get_version()
        await PlatformUtils.send_superuser(
            bot,
            f"检测真寻已更新，当前版本：{cur_version}\n开始更新...",
            user_id,
        )
        if zip:
            return await cls.__zip_update(version_type)
        elif source == "git":
            result = await GithubRepoManager.update(
                GIT_GITHUB_URL,
                Path(),
                use_git=update_type == "git",
                force=force,
            )
        else:
            result = await AliyunRepoManager.update(
                GIT_GITHUB_URL,
                Path(),
                force=force,
            )
        if not result.success:
            return f"版本更新失败...错误: {result.error_message}"
        await PlatformUtils.send_superuser(
            bot, "真寻更新完成，开始安装依赖...", user_id
        )
        await VirtualEnvPackageManager.install_requirement(REQ_TXT_FILE)
        return (
            f"版本更新完成！\n"
            f"版本: {cur_version} -> {result.new_version}\n"
            f"变更文件个数: {len(result.changed_files)}"
            f"{'' if source == 'git' else '（阿里云更新不支持查看变更文件）'}\n"
            "请重新启动真寻以完成更新!"
        )

    @classmethod
    def __get_version(cls) -> str:
        """获取当前版本

        返回:
            str: 当前版本号
        """
        _version = "v0.0.0"
        if VERSION_FILE.exists():
            if text := VERSION_FILE.open(encoding="utf8").readline():
                _version = text.split(":")[-1].strip()
        return _version

    @classmethod
    async def __get_latest_data(cls) -> dict:
        """获取最新版本信息

        返回:
            dict: 最新版本数据
        """
        for _ in range(3):
            try:
                res = await AsyncHttpx.get(RELEASE_URL)
                if res.status_code == 200:
                    return res.json()
            except TimeoutError:
                pass
            except Exception as e:
                logger.error("检查更新真寻获取版本失败", e=e)
        return {}

    @classmethod
    async def __get_version_from_repo(cls, repo_info: RepoInfo) -> str:
        """从指定分支获取版本号

        参数:
            branch: 分支名称

        返回:
            str: 版本号
        """
        version_url = await repo_info.get_raw_download_urls(path="__version__")
        try:
            res = await AsyncHttpx.get(version_url)
            if res.status_code == 200:
                return res.text.strip()
        except Exception as e:
            logger.error(f"获取 {repo_info.branch} 分支版本失败", e=e)
        return "未知版本"
