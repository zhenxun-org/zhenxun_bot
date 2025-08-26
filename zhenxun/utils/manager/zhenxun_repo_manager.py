"""
真寻仓库管理器
负责真寻主仓库的更新、版本检查、文件处理等功能
"""

import os
from pathlib import Path
import shutil
from typing import ClassVar, Literal
import zipfile

import aiofiles

from zhenxun.configs.path_config import DATA_PATH, TEMP_PATH
from zhenxun.services.log import logger
from zhenxun.utils.github_utils import GithubUtils
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.manager.virtual_env_package_manager import VirtualEnvPackageManager
from zhenxun.utils.repo_utils import AliyunRepoManager, GithubRepoManager
from zhenxun.utils.repo_utils.models import RepoUpdateResult
from zhenxun.utils.repo_utils.utils import check_git

LOG_COMMAND = "ZhenxunRepoManager"


class ZhenxunUpdateException(Exception):
    """资源下载异常"""

    pass


class ZhenxunRepoConfig:
    """真寻仓库配置"""

    # Zhenxun Bot 相关配置
    ZHENXUN_BOT_GIT = "https://github.com/zhenxun-org/zhenxun_bot.git"
    ZHENXUN_BOT_GITHUB_URL = "https://github.com/HibiKier/zhenxun_bot/tree/main"
    ZHENXUN_BOT_DOWNLOAD_FILE_STRING = "zhenxun_bot.zip"
    ZHENXUN_BOT_DOWNLOAD_FILE = TEMP_PATH / ZHENXUN_BOT_DOWNLOAD_FILE_STRING
    ZHENXUN_BOT_UNZIP_PATH = TEMP_PATH / "zhenxun_bot"
    ZHENXUN_BOT_CODE_PATH = Path() / "zhenxun"
    ZHENXUN_BOT_RELEASES_API_URL = (
        "https://api.github.com/repos/HibiKier/zhenxun_bot/releases/latest"
    )
    ZHENXUN_BOT_BACKUP_PATH = Path() / "backup"
    # 需要替换的文件夹
    ZHENXUN_BOT_UPDATE_FOLDERS: ClassVar[list[str]] = [
        "zhenxun/builtin_plugins",
        "zhenxun/services",
        "zhenxun/utils",
        "zhenxun/models",
        "zhenxun/configs",
        "zhenxun/ui",
    ]
    ZHENXUN_BOT_VERSION_FILE_STRING = "__version__"
    ZHENXUN_BOT_VERSION_FILE = Path() / ZHENXUN_BOT_VERSION_FILE_STRING

    # 备份杂项
    BACKUP_FILES: ClassVar[list[str]] = [
        "pyproject.toml",
        "poetry.lock",
        "requirements.txt",
        ".env.dev",
        ".env.example",
    ]

    # WEB UI 相关配置
    WEBUI_GIT = "https://github.com/HibiKier/zhenxun_bot_webui.git"
    WEBUI_DIST_GITHUB_URL = "https://github.com/HibiKier/zhenxun_bot_webui/tree/dist"
    WEBUI_DOWNLOAD_FILE_STRING = "webui_assets.zip"
    WEBUI_DOWNLOAD_FILE = TEMP_PATH / WEBUI_DOWNLOAD_FILE_STRING
    WEBUI_UNZIP_PATH = TEMP_PATH / "web_ui"
    WEBUI_PATH = DATA_PATH / "web_ui" / "public"
    WEBUI_BACKUP_PATH = DATA_PATH / "web_ui" / "backup_public"

    # 资源管理相关配置
    RESOURCE_GIT = "https://github.com/zhenxun-org/zhenxun-bot-resources.git"
    RESOURCE_GITHUB_URL = (
        "https://github.com/zhenxun-org/zhenxun-bot-resources/tree/main"
    )
    RESOURCE_ZIP_FILE_STRING = "resources.zip"
    RESOURCE_ZIP_FILE = TEMP_PATH / RESOURCE_ZIP_FILE_STRING
    RESOURCE_UNZIP_PATH = TEMP_PATH / "resources"
    RESOURCE_PATH = Path() / "resources"

    REQUIREMENTS_FILE_STRING = "requirements.txt"
    REQUIREMENTS_FILE = Path() / REQUIREMENTS_FILE_STRING

    PYPROJECT_FILE_STRING = "pyproject.toml"
    PYPROJECT_FILE = Path() / PYPROJECT_FILE_STRING

    PYPROJECT_LOCK_FILE_STRING = "poetry.lock"
    PYPROJECT_LOCK_FILE = Path() / PYPROJECT_LOCK_FILE_STRING


class ZhenxunRepoManagerClass:
    """真寻仓库管理器"""

    def __init__(self):
        self.config = ZhenxunRepoConfig()

    def __clear_folder(self, folder_path: Path):
        """
        清空文件夹

        参数:
            folder_path: 文件夹路径
        """
        if not folder_path.exists():
            return
        for filename in os.listdir(folder_path):
            file_path = folder_path / filename
            try:
                if file_path.is_file():
                    os.unlink(file_path)
                elif file_path.is_dir() and not filename.startswith("."):
                    shutil.rmtree(file_path)
            except Exception as e:
                logger.warning(f"无法删除 {file_path}", LOG_COMMAND, e=e)

    def __copy_files(self, src_path: Path, dest_path: Path, incremental: bool = False):
        """
        复制文件或文件夹

        参数:
            src_path: 源文件或文件夹路径
            dest_path: 目标文件或文件夹路径
            incremental: 是否增量复制
        """
        if src_path.is_file():
            shutil.copy(src_path, dest_path)
            logger.debug(f"复制文件 {src_path} -> {dest_path}", LOG_COMMAND)
        elif src_path.is_dir():
            for filename in os.listdir(src_path):
                file_path = src_path / filename
                dest_file = dest_path / filename
                dest_file.parent.mkdir(exist_ok=True, parents=True)
                if file_path.is_file():
                    if dest_file.exists():
                        dest_file.unlink()
                    shutil.copy(file_path, dest_file)
                    logger.debug(f"复制文件 {file_path} -> {dest_file}", LOG_COMMAND)
                elif file_path.is_dir():
                    if incremental:
                        self.__copy_files(file_path, dest_file, incremental=True)
                    else:
                        if dest_file.exists():
                            shutil.rmtree(dest_file, True)
                        shutil.copytree(file_path, dest_file)
                        logger.debug(
                            f"复制文件夹 {file_path} -> {dest_file}",
                            LOG_COMMAND,
                        )

    # ==================== Zhenxun Bot 相关方法 ====================

    async def zhenxun_get_version_from_repo(self) -> str:
        """从指定分支获取版本号


        返回:
            str: 版本号
        """
        repo_info = GithubUtils.parse_github_url(self.config.ZHENXUN_BOT_GITHUB_URL)
        version_url = await repo_info.get_raw_download_urls(
            path=self.config.ZHENXUN_BOT_VERSION_FILE_STRING
        )
        try:
            res = await AsyncHttpx.get(version_url)
            if res.status_code == 200:
                return res.text.strip()
        except Exception as e:
            logger.error(f"获取 {repo_info.branch} 分支版本失败", LOG_COMMAND, e=e)
        return "未知版本"

    async def zhenxun_write_version_file(self, version: str):
        """写入版本文件"""
        async with aiofiles.open(
            self.config.ZHENXUN_BOT_VERSION_FILE, "w", encoding="utf8"
        ) as f:
            await f.write(f"__version__: {version}")

    def __backup_zhenxun(self):
        """备份真寻文件"""
        for filename in os.listdir(self.config.ZHENXUN_BOT_CODE_PATH):
            file_path = self.config.ZHENXUN_BOT_CODE_PATH / filename
            if file_path.exists():
                self.__copy_files(
                    file_path,
                    self.config.ZHENXUN_BOT_BACKUP_PATH / filename,
                    True,
                )
        for filename in self.config.BACKUP_FILES:
            file_path = Path() / filename
            if file_path.exists():
                self.__copy_files(
                    file_path,
                    self.config.ZHENXUN_BOT_BACKUP_PATH / filename,
                )

    async def zhenxun_get_latest_releases_data(self) -> dict:
        """获取真寻releases最新版本信息

        返回:
            dict: 最新版本数据
        """
        try:
            res = await AsyncHttpx.get(self.config.ZHENXUN_BOT_RELEASES_API_URL)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            logger.error("检查更新真寻获取版本失败", LOG_COMMAND, e=e)
        return {}

    async def zhenxun_download_zip(self, ver_type: Literal["main", "release"]) -> str:
        """下载真寻最新版文件

        参数:
            ver_type: 版本类型，main 为最新版，release 为最新release版

        返回:
            str: 版本号
        """
        repo_info = GithubUtils.parse_github_url(self.config.ZHENXUN_BOT_GITHUB_URL)
        if ver_type == "main":
            download_url = await repo_info.get_archive_download_urls()
            new_version = await self.zhenxun_get_version_from_repo()
        else:
            release_data = await self.zhenxun_get_latest_releases_data()
            logger.debug(f"获取真寻RELEASES最新版本信息: {release_data}", LOG_COMMAND)
            if not release_data:
                raise ZhenxunUpdateException("获取真寻RELEASES最新版本失败...")
            new_version = release_data.get("name", "")
            download_url = await repo_info.get_release_source_download_urls_tgz(
                new_version
            )
        if not download_url:
            raise ZhenxunUpdateException("获取真寻最新版文件下载链接失败...")
        if self.config.ZHENXUN_BOT_DOWNLOAD_FILE.exists():
            self.config.ZHENXUN_BOT_DOWNLOAD_FILE.unlink()
        if await AsyncHttpx.download_file(
            download_url, self.config.ZHENXUN_BOT_DOWNLOAD_FILE, stream=True
        ):
            logger.debug("下载真寻最新版文件完成...", LOG_COMMAND)
        else:
            raise ZhenxunUpdateException("下载真寻最新版文件失败...")
        return new_version

    async def zhenxun_unzip(self):
        """解压真寻最新版文件"""
        if not self.config.ZHENXUN_BOT_DOWNLOAD_FILE.exists():
            raise FileNotFoundError("真寻最新版文件不存在")
        if self.config.ZHENXUN_BOT_UNZIP_PATH.exists():
            shutil.rmtree(self.config.ZHENXUN_BOT_UNZIP_PATH)
        tf = None
        try:
            tf = zipfile.ZipFile(self.config.ZHENXUN_BOT_DOWNLOAD_FILE)
            tf.extractall(self.config.ZHENXUN_BOT_UNZIP_PATH)
            logger.debug("解压Zhenxun Bot文件压缩包完成!", LOG_COMMAND)
            self.__backup_zhenxun()
            for filename in self.config.BACKUP_FILES:
                self.__copy_files(
                    self.config.ZHENXUN_BOT_UNZIP_PATH / filename,
                    Path() / filename,
                )
            logger.debug("备份真寻更新文件完成!", LOG_COMMAND)
            unzip_dir = next(self.config.ZHENXUN_BOT_UNZIP_PATH.iterdir())
            for folder in self.config.ZHENXUN_BOT_UPDATE_FOLDERS:
                self.__copy_files(unzip_dir / folder, Path() / folder)
            logger.debug("移动真寻更新文件完成!", LOG_COMMAND)
            if self.config.ZHENXUN_BOT_UNZIP_PATH.exists():
                shutil.rmtree(self.config.ZHENXUN_BOT_UNZIP_PATH)
        except Exception as e:
            logger.error("解压真寻最新版文件失败...", LOG_COMMAND, e=e)
            raise
        finally:
            if tf:
                tf.close()

    async def zhenxun_zip_update(self, ver_type: Literal["main", "release"]) -> str:
        """使用zip更新真寻

        参数:
            ver_type: 版本类型，main 为最新版，release 为最新release版

        返回:
            str: 版本号
        """
        new_version = await self.zhenxun_download_zip(ver_type)
        await self.zhenxun_unzip()
        await self.zhenxun_write_version_file(new_version)
        return new_version

    async def zhenxun_git_update(
        self, source: Literal["git", "ali"], branch: str = "main", force: bool = False
    ) -> RepoUpdateResult:
        """使用git或阿里云更新真寻

        参数:
            source: 更新源，git 为 git 更新，ali 为阿里云更新
            branch: 分支名称
            force: 是否强制更新
        """
        if source == "git":
            return await GithubRepoManager.update_via_git(
                self.config.ZHENXUN_BOT_GIT,
                Path(),
                branch=branch,
                force=force,
            )
        else:
            return await AliyunRepoManager.update_via_git(
                self.config.ZHENXUN_BOT_GIT,
                Path(),
                branch=branch,
                force=force,
            )

    async def zhenxun_update(
        self,
        source: Literal["git", "ali"] = "ali",
        branch: str = "main",
        force: bool = False,
        ver_type: Literal["main", "release"] = "main",
    ):
        """更新真寻

        参数:
            source: 更新源，git 为 git 更新，ali 为阿里云更新
            branch: 分支名称
            force: 是否强制更新
            ver_type: 版本类型，main 为最新版，release 为最新release版
        """
        if await check_git():
            await self.zhenxun_git_update(source, branch, force)
            logger.debug("使用git更新真寻!", LOG_COMMAND)
        else:
            await self.zhenxun_zip_update(ver_type)
            logger.debug("使用zip更新真寻!", LOG_COMMAND)

    async def install_requirements(self):
        """安装真寻依赖"""
        await VirtualEnvPackageManager.install_requirement(
            self.config.REQUIREMENTS_FILE
        )

    # ==================== 资源管理相关方法 ====================

    def check_resources_exists(self) -> bool:
        """检查资源文件是否存在

        返回:
            bool: 是否存在
        """
        if self.config.RESOURCE_PATH.exists():
            font_path = self.config.RESOURCE_PATH / "font"
            if font_path.exists() and os.listdir(font_path):
                return True
        return False

    async def resources_download_zip(self):
        """下载资源文件"""
        download_url = await GithubUtils.parse_github_url(
            self.config.RESOURCE_GITHUB_URL
        ).get_archive_download_urls()
        logger.debug("开始下载resources资源包...", LOG_COMMAND)
        if await AsyncHttpx.download_file(
            download_url, self.config.RESOURCE_ZIP_FILE, stream=True
        ):
            logger.debug("下载resources资源文件压缩包成功!", LOG_COMMAND)
        else:
            raise ZhenxunUpdateException("下载resources资源包失败...")

    async def resources_unzip(self):
        """解压资源文件"""
        if not self.config.RESOURCE_ZIP_FILE.exists():
            raise FileNotFoundError("资源文件压缩包不存在")
        if self.config.RESOURCE_UNZIP_PATH.exists():
            shutil.rmtree(self.config.RESOURCE_UNZIP_PATH)
        tf = None
        try:
            tf = zipfile.ZipFile(self.config.RESOURCE_ZIP_FILE)
            tf.extractall(self.config.RESOURCE_UNZIP_PATH)
            logger.debug("解压文件压缩包完成...", LOG_COMMAND)
            unzip_dir = next(self.config.RESOURCE_UNZIP_PATH.iterdir())
            self.__copy_files(unzip_dir, self.config.RESOURCE_PATH, True)
            logger.debug("复制资源文件完成!", LOG_COMMAND)
            shutil.rmtree(self.config.RESOURCE_UNZIP_PATH, ignore_errors=True)
        except Exception as e:
            logger.error("解压资源文件失败...", LOG_COMMAND, e=e)
            raise
        finally:
            if tf:
                tf.close()

    async def resources_zip_update(self):
        """使用zip更新资源文件"""
        await self.resources_download_zip()
        await self.resources_unzip()

    async def resources_git_update(
        self, source: Literal["git", "ali"], branch: str = "main", force: bool = False
    ) -> RepoUpdateResult:
        """使用git或阿里云更新资源文件

        参数:
            source: 更新源，git 为 git 更新，ali 为阿里云更新
            branch: 分支名称
            force: 是否强制更新
        """
        if source == "git":
            return await GithubRepoManager.update_via_git(
                self.config.RESOURCE_GIT,
                self.config.RESOURCE_PATH,
                branch=branch,
                force=force,
            )
        else:
            return await AliyunRepoManager.update_via_git(
                self.config.RESOURCE_GIT,
                self.config.RESOURCE_PATH,
                branch=branch,
                force=force,
            )

    async def resources_update(
        self,
        source: Literal["git", "ali"] = "ali",
        branch: str = "main",
        force: bool = False,
    ):
        """更新资源文件

        参数:
            source: 更新源，git 为 git 更新，ali 为阿里云更新
            branch: 分支名称
            force: 是否强制更新
        """
        if await check_git():
            await self.resources_git_update(source, branch, force)
            logger.debug("使用git更新资源文件!", LOG_COMMAND)
        else:
            await self.resources_zip_update()
            logger.debug("使用zip更新资源文件!", LOG_COMMAND)

    # ==================== Web UI 管理相关方法 ====================

    def check_webui_exists(self) -> bool:
        """检查 Web UI 资源是否存在"""
        return bool(
            self.config.WEBUI_PATH.exists() and os.listdir(self.config.WEBUI_PATH)
        )

    async def webui_download_zip(self):
        """下载 WEBUI_ASSETS 资源"""
        download_url = await GithubUtils.parse_github_url(
            self.config.WEBUI_DIST_GITHUB_URL
        ).get_archive_download_urls()
        logger.info("开始下载 WEBUI_ASSETS 资源...", LOG_COMMAND)
        if await AsyncHttpx.download_file(
            download_url, self.config.WEBUI_DOWNLOAD_FILE, follow_redirects=True
        ):
            logger.info("下载 WEBUI_ASSETS 成功!", LOG_COMMAND)
        else:
            raise ZhenxunUpdateException("下载 WEBUI_ASSETS 失败", LOG_COMMAND)

    def __backup_webui(self):
        """备份 WEBUI_ASSERT 资源"""
        if self.config.WEBUI_PATH.exists():
            if self.config.WEBUI_BACKUP_PATH.exists():
                logger.debug(
                    f"删除旧的备份webui文件夹 {self.config.WEBUI_BACKUP_PATH}",
                    LOG_COMMAND,
                )
                shutil.rmtree(self.config.WEBUI_BACKUP_PATH)
            shutil.copytree(self.config.WEBUI_PATH, self.config.WEBUI_BACKUP_PATH)

    async def webui_unzip(self):
        """解压 WEBUI_ASSETS 资源

        返回:
            str: 更新结果
        """
        if not self.config.WEBUI_DOWNLOAD_FILE.exists():
            raise FileNotFoundError("webui文件压缩包不存在")
        tf = None
        try:
            self.__backup_webui()
            self.__clear_folder(self.config.WEBUI_PATH)
            tf = zipfile.ZipFile(self.config.WEBUI_DOWNLOAD_FILE)
            tf.extractall(self.config.WEBUI_UNZIP_PATH)
            logger.debug("Web UI 解压文件压缩包完成...", LOG_COMMAND)
            unzip_dir = next(self.config.WEBUI_UNZIP_PATH.iterdir())
            self.__copy_files(unzip_dir, self.config.WEBUI_PATH)
            logger.debug("Web UI 复制 WEBUI_ASSETS 成功!", LOG_COMMAND)
            shutil.rmtree(self.config.WEBUI_UNZIP_PATH, ignore_errors=True)
        except Exception as e:
            if self.config.WEBUI_BACKUP_PATH.exists():
                self.__copy_files(self.config.WEBUI_BACKUP_PATH, self.config.WEBUI_PATH)
                logger.debug("恢复备份 WEBUI_ASSETS 成功!", LOG_COMMAND)
                shutil.rmtree(self.config.WEBUI_BACKUP_PATH, ignore_errors=True)
            logger.error("Web UI 更新失败", LOG_COMMAND, e=e)
            raise
        finally:
            if tf:
                tf.close()

    async def webui_zip_update(self):
        """使用zip更新 Web UI"""
        await self.webui_download_zip()
        await self.webui_unzip()

    async def webui_git_update(
        self, source: Literal["git", "ali"], branch: str = "dist", force: bool = False
    ) -> RepoUpdateResult:
        """使用git或阿里云更新 Web UI

        参数:
            source: 更新源，git 为 git 更新，ali 为阿里云更新
            branch: 分支名称
            force: 是否强制更新
        """
        if source == "git":
            return await GithubRepoManager.update_via_git(
                self.config.WEBUI_GIT,
                self.config.WEBUI_PATH,
                branch=branch,
                force=force,
            )
        else:
            return await AliyunRepoManager.update_via_git(
                self.config.WEBUI_GIT,
                self.config.WEBUI_PATH,
                branch=branch,
                force=force,
            )

    async def webui_update(
        self,
        source: Literal["git", "ali"] = "ali",
        branch: str = "dist",
        force: bool = False,
    ):
        """更新 Web UI

        参数:
            source: 更新源，git 为 git 更新，ali 为阿里云更新
        """
        if await check_git():
            await self.webui_git_update(source, branch, force)
            logger.debug("使用git更新Web UI!", LOG_COMMAND)
        else:
            await self.webui_zip_update()
            logger.debug("使用zip更新Web UI!", LOG_COMMAND)


ZhenxunRepoManager = ZhenxunRepoManagerClass()
