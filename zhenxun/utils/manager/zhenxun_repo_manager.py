"""
真寻仓库管理器
负责真寻主仓库的更新、版本检查、文件处理等功能
"""

import os
from pathlib import Path
import shutil
import tarfile
from typing import ClassVar
import zipfile

from zhenxun.configs.path_config import DATA_PATH, FONT_PATH, TEMP_PATH
from zhenxun.services.log import logger
from zhenxun.utils.github_utils import GithubUtils
from zhenxun.utils.github_utils.models import RepoInfo
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.manager.virtual_env_package_manager import VirtualEnvPackageManager
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.repo_utils import AliyunRepoManager, GithubRepoManager
from zhenxun.utils.repo_utils.models import (
    SubmoduleConfig,
)
from zhenxun.utils.repo_utils.submodule_manager import SubmoduleManager
from zhenxun.utils.repo_utils.utils import clean_git

LOG_COMMAND = "ZhenxunRepoManager"


class DownloadException(Exception):
    """资源下载异常"""

    pass


class ZhenxunRepoConfig:
    """真寻仓库配置"""

    # GitHub 仓库 URL
    ZHENXUN_BOT_GIT = "https://github.com/zhenxun-org/zhenxun_bot.git"
    ZHENXUN_BOT_GITHUB_URL = "https://github.com/HibiKier/zhenxun_bot/tree/main"
    DEFAULT_GITHUB_URL = "https://github.com/HibiKier/zhenxun_bot/tree/main"
    RELEASE_URL = "https://api.github.com/repos/HibiKier/zhenxun_bot/releases/latest"

    # 资源仓库 URL
    RESOURCE_GITHUB_URL = (
        "https://github.com/zhenxun-org/zhenxun-bot-resources/tree/main"
    )

    # Web UI 仓库 URL
    WEBUI_GIT = "https://github.com/HibiKier/zhenxun_bot_webui.git"

    # 文件路径配置
    VERSION_FILE_STRING = "__version__"
    VERSION_FILE = Path() / VERSION_FILE_STRING

    PYPROJECT_FILE_STRING = "pyproject.toml"
    PYPROJECT_FILE = Path() / PYPROJECT_FILE_STRING
    PYPROJECT_LOCK_FILE_STRING = "poetry.lock"
    PYPROJECT_LOCK_FILE = Path() / PYPROJECT_LOCK_FILE_STRING
    REQ_TXT_FILE_STRING = "requirements.txt"
    REQ_TXT_FILE = Path() / REQ_TXT_FILE_STRING

    BASE_PATH_STRING = "zhenxun"
    BASE_PATH = Path() / BASE_PATH_STRING

    # 资源路径配置
    RESOURCE_PATH = Path() / "resources"

    # Web UI 路径配置
    WEBUI_PATH = DATA_PATH / "web_ui" / "public"
    WEBUI_BACKUP_PATH = DATA_PATH / "web_ui" / "backup_public"
    WEBUI_GIT_PATH = DATA_PATH / "web_ui" / "git_web_ui"

    # 临时文件路径
    TMP_PATH = TEMP_PATH / "zhenxun_update"
    BACKUP_PATH = Path() / "backup"
    RESOURCE_TMP_PATH = TEMP_PATH / "_resource_tmp"

    # 下载文件配置
    DOWNLOAD_GZ_FILE_STRING = "download_latest_file.tar.gz"
    DOWNLOAD_ZIP_FILE_STRING = "download_latest_file.zip"
    DOWNLOAD_GZ_FILE = TMP_PATH / DOWNLOAD_GZ_FILE_STRING
    DOWNLOAD_ZIP_FILE = TMP_PATH / DOWNLOAD_ZIP_FILE_STRING

    # 资源文件配置
    RESOURCE_ZIP_FILE = RESOURCE_TMP_PATH / "resources.zip"
    UNZIP_PATH: Path | None = None

    # 需要替换的文件夹
    REPLACE_FOLDERS: ClassVar[list[str]] = [
        "builtin_plugins",
        "services",
        "utils",
        "models",
        "configs",
    ]

    # 日志标识
    COMMAND = "真寻仓库管理"


class ZhenxunRepoManager:
    """真寻仓库管理器"""

    def __init__(self):
        self.config = ZhenxunRepoConfig()
        # 初始化子模块管理器
        self.submodule_manager = SubmoduleManager(GithubRepoManager)

    def __clear_folder(self, folder_path: Path):
        for filename in os.listdir(folder_path):
            file_path = folder_path / filename
            try:
                if file_path.is_file():
                    os.unlink(file_path)
                elif file_path.is_dir() and not filename.startswith("."):
                    shutil.rmtree(file_path)
            except Exception as e:
                logger.warning(f"无法删除 {file_path}", LOG_COMMAND, e=e)

    async def check_version(self) -> str:
        """检查真寻更新版本

        返回:
            str: 更新信息
        """
        cur_version = self._get_current_version()
        data = await self._get_latest_release_data()
        if not data:
            return "检查更新获取版本失败..."
        return (
            "检测到当前版本更新\n"
            f"当前版本：{cur_version}\n"
            f"最新版本：{data.get('name')}\n"
            f"创建日期：{data.get('created_at')}\n"
            f"更新内容：\n{data.get('body')}"
        )

    async def update_repository(
        self,
        bot,
        user_id: str,
        version_type: str,
        force: bool,
        source: str,
        zip_update: bool,
        update_type: str,
    ) -> str:
        """更新真寻仓库

        参数:
            bot: Bot实例
            user_id: 用户ID
            version_type: 更新版本类型 (main/release)
            force: 是否强制更新
            source: 更新源 (git/ali)
            zip_update: 是否下载zip文件
            update_type: 更新方式 (git/download)

        返回:
            str: 更新结果消息
        """
        cur_version = self._get_current_version()
        await PlatformUtils.send_superuser(
            bot,
            f"检测真寻已更新，当前版本：{cur_version}\n开始更新...",
            user_id,
        )

        if zip_update:
            return await self._zip_update(version_type)
        elif source == "git":
            result = await GithubRepoManager.update(
                self.config.ZHENXUN_BOT_GIT,
                Path(),
                use_git=update_type == "git",
                force=force,
            )
        else:
            result = await AliyunRepoManager.update(
                self.config.ZHENXUN_BOT_GIT,
                Path(),
                force=force,
            )

        if not result.success:
            return f"版本更新失败...错误: {result.error_message}"

        await PlatformUtils.send_superuser(
            bot, "真寻更新完成，开始安装依赖...", user_id
        )
        await VirtualEnvPackageManager.install_requirement(self.config.REQ_TXT_FILE)

        return (
            f"版本更新完成！\n"
            f"版本: {cur_version} -> {result.new_version}\n"
            f"变更文件个数: {len(result.changed_files)}"
            f"{'' if source == 'git' else '（阿里云更新不支持查看变更文件）'}\n"
            "请重新启动真寻以完成更新!"
        )

    async def _zip_update(self, version_type: str) -> str:
        """ZIP文件更新

        参数:
            version_type: 版本类型 (main/release)

        返回:
            str: 更新结果
        """
        logger.info("开始下载真寻最新版文件....", self.config.COMMAND)
        cur_version = self._get_current_version()
        url = None
        new_version = None
        repo_info = GithubUtils.parse_github_url(self.config.DEFAULT_GITHUB_URL)

        if version_type in {"main"}:
            repo_info.branch = version_type
            new_version = await self._get_version_from_repo(repo_info)
            if new_version:
                new_version = new_version.split(":")[-1].strip()
            url = await repo_info.get_archive_download_urls()
        elif version_type == "release":
            data = await self._get_latest_release_data()
            if not data:
                return "获取更新版本失败..."
            new_version = data.get("name", "")
            url = await repo_info.get_release_source_download_urls_tgz(new_version)

        if not url:
            return "获取版本下载链接失败..."

        if self.config.TMP_PATH.exists():
            logger.debug(f"删除临时文件夹 {self.config.TMP_PATH}", self.config.COMMAND)
            shutil.rmtree(self.config.TMP_PATH)

        logger.debug(
            f"开始更新版本：{cur_version} -> {new_version} | 下载链接：{url}",
            self.config.COMMAND,
        )

        download_file = (
            self.config.DOWNLOAD_GZ_FILE
            if version_type == "release"
            else self.config.DOWNLOAD_ZIP_FILE
        )

        if await AsyncHttpx.download_file(url, download_file, stream=True):
            logger.debug("下载真寻最新版文件完成...", self.config.COMMAND)
            self._handle_downloaded_files(new_version)
            result = "版本更新完成"
            return (
                f"{result}\n"
                f"版本: {cur_version} -> {new_version}\n"
                "请重新启动真寻以完成更新!"
            )
        else:
            logger.debug("下载真寻最新版文件失败...", self.config.COMMAND)
        return ""

    def _handle_downloaded_files(self, latest_version: str | None):
        """处理下载的文件

        参数:
            latest_version: 最新版本号
        """
        self.config.BACKUP_PATH.mkdir(exist_ok=True, parents=True)
        logger.debug("开始解压文件压缩包...", self.config.COMMAND)

        download_file = self.config.DOWNLOAD_GZ_FILE
        if self.config.DOWNLOAD_GZ_FILE.exists():
            tf = tarfile.open(self.config.DOWNLOAD_GZ_FILE)
        else:
            download_file = self.config.DOWNLOAD_ZIP_FILE
            tf = zipfile.ZipFile(self.config.DOWNLOAD_ZIP_FILE)

        tf.extractall(self.config.TMP_PATH)
        logger.debug("解压文件压缩包完成...", self.config.COMMAND)

        download_file_path = self.config.TMP_PATH / next(
            x
            for x in os.listdir(self.config.TMP_PATH)
            if (self.config.TMP_PATH / x).is_dir()
        )

        _pyproject = download_file_path / self.config.PYPROJECT_FILE_STRING
        _lock_file = download_file_path / self.config.PYPROJECT_LOCK_FILE_STRING
        _req_file = download_file_path / self.config.REQ_TXT_FILE_STRING
        extract_path = download_file_path / self.config.BASE_PATH_STRING
        target_path = self.config.BASE_PATH

        # 备份现有文件
        if self.config.PYPROJECT_FILE.exists():
            logger.debug(
                f"移除备份文件: {self.config.PYPROJECT_FILE}", self.config.COMMAND
            )
            shutil.move(
                self.config.PYPROJECT_FILE,
                self.config.BACKUP_PATH / self.config.PYPROJECT_FILE_STRING,
            )
        if self.config.PYPROJECT_LOCK_FILE.exists():
            logger.debug(
                f"移除备份文件: {self.config.PYPROJECT_LOCK_FILE}", self.config.COMMAND
            )
            shutil.move(
                self.config.PYPROJECT_LOCK_FILE,
                self.config.BACKUP_PATH / self.config.PYPROJECT_LOCK_FILE_STRING,
            )
        if self.config.REQ_TXT_FILE.exists():
            logger.debug(
                f"移除备份文件: {self.config.REQ_TXT_FILE}", self.config.COMMAND
            )
            shutil.move(
                self.config.REQ_TXT_FILE,
                self.config.BACKUP_PATH / self.config.REQ_TXT_FILE_STRING,
            )

        # 移动新文件
        if _pyproject.exists():
            logger.debug("移动文件: pyproject.toml", self.config.COMMAND)
            shutil.move(_pyproject, self.config.PYPROJECT_FILE)
        if _lock_file.exists():
            logger.debug("移动文件: poetry.lock", self.config.COMMAND)
            shutil.move(_lock_file, self.config.PYPROJECT_LOCK_FILE)
        if _req_file.exists():
            logger.debug("移动文件: requirements.txt", self.config.COMMAND)
            shutil.move(_req_file, self.config.REQ_TXT_FILE)

        # 处理文件夹
        for folder in self.config.REPLACE_FOLDERS:
            _dir = self.config.BASE_PATH / folder
            _backup_dir = self.config.BACKUP_PATH / folder
            if _backup_dir.exists():
                logger.debug(f"删除备份文件夹 {_backup_dir}", self.config.COMMAND)
                shutil.rmtree(_backup_dir)
            if _dir.exists():
                logger.debug(f"移动旧文件夹 {_dir}", self.config.COMMAND)
                shutil.move(_dir, _backup_dir)
            else:
                logger.warning(f"文件夹 {_dir} 不存在，跳过删除", self.config.COMMAND)

        for folder in self.config.REPLACE_FOLDERS:
            src_folder_path = extract_path / folder
            dest_folder_path = target_path / folder
            if src_folder_path.exists():
                logger.debug(
                    f"移动文件夹: {src_folder_path} -> {dest_folder_path}",
                    self.config.COMMAND,
                )
                shutil.move(src_folder_path, dest_folder_path)
            else:
                logger.debug(f"源文件夹不存在: {src_folder_path}", self.config.COMMAND)

        # 清理临时文件
        if tf:
            tf.close()
        if download_file.exists():
            logger.debug(f"删除下载文件: {download_file}", self.config.COMMAND)
            download_file.unlink()
        if extract_path.exists():
            logger.debug(f"删除解压文件夹: {extract_path}", self.config.COMMAND)
            shutil.rmtree(extract_path)
        if self.config.TMP_PATH.exists():
            shutil.rmtree(self.config.TMP_PATH)

        # 更新版本文件
        if latest_version:
            with open(self.config.VERSION_FILE, "w", encoding="utf8") as f:
                f.write(f"__version__: {latest_version}")

    def _get_current_version(self) -> str:
        """获取当前版本

        返回:
            str: 当前版本号
        """
        _version = "v0.0.0"
        if self.config.VERSION_FILE.exists():
            if text := self.config.VERSION_FILE.open(encoding="utf8").readline():
                _version = text.split(":")[-1].strip()
        return _version

    async def _get_latest_release_data(self) -> dict:
        """获取最新版本信息

        返回:
            dict: 最新版本数据
        """
        for _ in range(3):
            try:
                res = await AsyncHttpx.get(self.config.RELEASE_URL)
                if res.status_code == 200:
                    return res.json()
            except TimeoutError:
                pass
            except Exception as e:
                logger.error("检查更新真寻获取版本失败", e=e)
        return {}

    async def _get_version_from_repo(self, repo_info: RepoInfo) -> str:
        """从指定分支获取版本号

        参数:
            repo_info: 仓库信息

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

    # ==================== 资源管理相关方法 ====================

    async def init_resources(
        self, force: bool = False, is_zip: bool = False, git_source: str = "ali"
    ) -> str:
        """初始化资源文件

        参数:
            force: 是否强制更新
            is_zip: 是否下载zip文件
            git_source: 更新源 (ali/git)

        返回:
            str: 操作结果
        """
        if (FONT_PATH.exists() and os.listdir(FONT_PATH)) and not force:
            return "资源文件已存在，跳过初始化"

        try:
            if is_zip:
                if self.config.RESOURCE_TMP_PATH.exists():
                    logger.debug(
                        "resources临时文件夹已存在，移除resources临时文件夹",
                        self.config.COMMAND,
                    )
                    await clean_git(self.config.RESOURCE_TMP_PATH)
                    shutil.rmtree(self.config.RESOURCE_TMP_PATH, ignore_errors=True)
                self.config.RESOURCE_TMP_PATH.mkdir(parents=True, exist_ok=True)
                await self._download_resources()
                self._handle_resource_files()
            else:
                if git_source == "ali":
                    result = await AliyunRepoManager.update(
                        self.config.RESOURCE_GITHUB_URL, self.config.RESOURCE_PATH
                    )
                else:
                    result = await GithubRepoManager.update(
                        self.config.RESOURCE_GITHUB_URL, self.config.RESOURCE_PATH
                    )
                if not result.success:
                    return f"资源更新失败...错误: {result.error_message}"
                self.config.UNZIP_PATH = self.config.RESOURCE_TMP_PATH / "resources"
                self._handle_resource_files()

            if self.config.RESOURCE_TMP_PATH.exists():
                logger.debug("移除resources临时文件夹", self.config.COMMAND)
                await clean_git(self.config.RESOURCE_TMP_PATH)
                shutil.rmtree(self.config.RESOURCE_TMP_PATH)

            return "资源文件初始化成功！"
        except Exception as e:
            logger.error("资源文件初始化失败", self.config.COMMAND, e=e)
            return f"资源文件初始化失败: {e}"

    def _handle_resource_files(self):
        """处理资源文件"""
        if not hasattr(self.config, "UNZIP_PATH") or not self.config.UNZIP_PATH:
            return
        self._recursive_folder(self.config.UNZIP_PATH, ".")

    def _recursive_folder(self, dir: Path, parent_path: str):
        """递归处理文件夹

        参数:
            dir: 目录路径
            parent_path: 父路径
        """
        for file in dir.iterdir():
            if file.is_dir():
                self._recursive_folder(file, f"{parent_path}/{file.name}")
            else:
                res_file = Path(parent_path) / file.name
                if res_file.exists():
                    res_file.unlink()
                res_file.parent.mkdir(parents=True, exist_ok=True)
                file.rename(res_file)

    async def _download_resources(self):
        """下载资源文件"""
        repo_info = GithubUtils.parse_github_url(self.config.RESOURCE_GITHUB_URL)
        url = await repo_info.get_archive_download_urls()
        logger.debug("开始下载resources资源包...", self.config.COMMAND)

        if not await AsyncHttpx.download_file(
            url, self.config.RESOURCE_ZIP_FILE, stream=True
        ):
            logger.error(
                "下载resources资源包失败，请尝试重启重新下载或前往 "
                "https://github.com/zhenxun-org/zhenxun-bot-resources 手动下载..."
            )
            raise DownloadException("下载resources资源包失败...")

        logger.debug("下载resources资源文件压缩包完成...", self.config.COMMAND)
        tf = zipfile.ZipFile(self.config.RESOURCE_ZIP_FILE)
        tf.extractall(self.config.RESOURCE_TMP_PATH)
        logger.debug("解压文件压缩包完成...", self.config.COMMAND)

        download_file_path = self.config.RESOURCE_TMP_PATH / next(
            x
            for x in os.listdir(self.config.RESOURCE_TMP_PATH)
            if (self.config.RESOURCE_TMP_PATH / x).is_dir()
        )
        self.config.UNZIP_PATH = download_file_path / "resources"

        if tf:
            tf.close()

    # ==================== 子模块管理相关方法 ====================

    async def init_submodules(self) -> str:
        """初始化子模块

        返回:
            str: 操作结果
        """
        try:
            # 定义子模块配置
            submodule_configs = [
                SubmoduleConfig(
                    name="resources",
                    path="resources",
                    repo_url=self.config.RESOURCE_GITHUB_URL,
                    branch="main",
                    enabled=True,
                ),
                SubmoduleConfig(
                    name="web_ui",
                    path="data/web_ui/public",
                    repo_url=self.config.WEBUI_GIT,
                    branch="main",
                    enabled=True,
                ),
            ]

            # 初始化子模块
            success = await self.submodule_manager.init_submodules(
                Path(), submodule_configs
            )

            if success:
                return "子模块初始化成功！"
            else:
                return "子模块初始化失败！"

        except Exception as e:
            logger.error("子模块初始化失败", self.config.COMMAND, e=e)
            return f"子模块初始化失败: {e}"

    async def update_submodules(self) -> str:
        """更新子模块

        返回:
            str: 操作结果
        """
        try:
            # 定义子模块配置
            submodule_configs = [
                SubmoduleConfig(
                    name="resources",
                    path="resources",
                    repo_url=self.config.RESOURCE_GITHUB_URL,
                    branch="main",
                    enabled=True,
                ),
                SubmoduleConfig(
                    name="web_ui",
                    path="data/web_ui/public",
                    repo_url=self.config.WEBUI_GIT,
                    branch="main",
                    enabled=True,
                ),
            ]

            # 更新子模块
            results = await self.submodule_manager.update_submodules(
                Path(), submodule_configs
            )

            success_count = sum(1 for result in results if result.success)
            total_count = len(results)

            return f"子模块更新完成！成功: {success_count}/{total_count}"

        except Exception as e:
            logger.error("子模块更新失败", self.config.COMMAND, e=e)
            return f"子模块更新失败: {e}"

    async def get_submodule_info(self) -> str:
        """获取子模块信息

        返回:
            str: 子模块信息
        """
        try:
            # 定义子模块配置
            submodule_configs = [
                SubmoduleConfig(
                    name="resources",
                    path="resources",
                    repo_url=self.config.RESOURCE_GITHUB_URL,
                    branch="main",
                    enabled=True,
                ),
                SubmoduleConfig(
                    name="web_ui",
                    path="data/web_ui/public",
                    repo_url=self.config.WEBUI_GIT,
                    branch="main",
                    enabled=True,
                ),
            ]

            # 获取子模块信息
            submodule_infos = await self.submodule_manager.get_submodule_info(
                Path(), submodule_configs
            )

            info_text = "子模块信息:\n"
            for info in submodule_infos:
                info_text += f"- {info.config.name}:\n"
                info_text += f"  路径: {info.config.path}\n"
                info_text += f"  当前版本: {info.current_version}\n"
                info_text += f"  最新版本: {info.latest_version}\n"
                info_text += f"  状态: {info.update_status}\n"

            return info_text

        except Exception as e:
            logger.error("获取子模块信息失败", self.config.COMMAND, e=e)
            return f"获取子模块信息失败: {e}"

    # ==================== Web UI 管理相关方法 ====================

    async def webui_download_zip(self) -> str:
        """下载 WEBUI_ASSETS 资源"""
        webui_assets_path = TEMP_PATH / "webui_assets.zip"
        download_url = await GithubUtils.parse_github_url(
            self.config.WEBUI_GIT
        ).get_archive_download_urls()
        logger.info("开始下载 WEBUI_ASSETS 资源...", LOG_COMMAND)
        if await AsyncHttpx.download_file(
            download_url, webui_assets_path, follow_redirects=True
        ):
            logger.info("下载 WEBUI_ASSETS 成功!", LOG_COMMAND)
        raise DownloadException("下载 WEBUI_ASSETS 失败", LOG_COMMAND)

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

    # async def webui_unzip(self) -> str:
    #     """使用zip更新 Web UI

    #     参数:
    #         is_zip: 是否下载 ZIP 文件
    #         source: 更新源 (git/ali)

    #     返回:
    #         str: 更新结果
    #     """
    #     self.__backup_webui()
    #     self.__clear_folder(self.config.WEBUI_PATH)
