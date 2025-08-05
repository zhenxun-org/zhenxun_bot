from typing import Literal

from nonebot.adapters import Bot

from zhenxun.services.log import logger
from zhenxun.utils.manager.virtual_env_package_manager import VirtualEnvPackageManager
from zhenxun.utils.manager.zhenxun_repo_manager import (
    ZhenxunRepoConfig,
    ZhenxunRepoManager,
)
from zhenxun.utils.platform import PlatformUtils

LOG_COMMAND = "AutoUpdate"


class UpdateManager:
    @classmethod
    async def check_version(cls) -> str:
        """检查更新版本

        返回:
            str: 更新信息
        """
        cur_version = cls.__get_version()
        release_data = await ZhenxunRepoManager.zhenxun_get_latest_releases_data()
        if not release_data:
            return "检查更新获取版本失败..."
        return (
            "检测到当前版本更新\n"
            f"当前版本：{cur_version}\n"
            f"最新版本：{release_data.get('name')}\n"
            f"创建日期：{release_data.get('created_at')}\n"
            f"更新内容：\n{release_data.get('body')}"
        )

    @classmethod
    async def update_webui(
        cls,
        source: Literal["git", "ali"] | None,
        branch: str = "dist",
        force: bool = False,
    ):
        """更新WebUI

        参数:
            source: 更新源
            branch: 分支
            force: 是否强制更新

        返回:
            str: 返回消息
        """
        if not source:
            await ZhenxunRepoManager.webui_zip_update()
            return "WebUI更新完成!"
        result = await ZhenxunRepoManager.webui_git_update(
            source,
            branch=branch,
            force=force,
        )
        if not result.success:
            logger.error(f"WebUI更新失败...错误: {result.error_message}", LOG_COMMAND)
            return f"WebUI更新失败...错误: {result.error_message}"
        return "WebUI更新完成!"

    @classmethod
    async def update_resources(
        cls,
        source: Literal["git", "ali"] | None,
        branch: str = "main",
        force: bool = False,
    ) -> str:
        """更新资源

        参数:
            source: 更新源
            branch: 分支
            force: 是否强制更新

        返回:
            str: 返回消息
        """
        if not source:
            await ZhenxunRepoManager.resources_zip_update()
            return "真寻资源更新完成!"
        result = await ZhenxunRepoManager.resources_git_update(
            source,
            branch=branch,
            force=force,
        )
        if not result.success:
            logger.error(
                f"真寻资源更新失败...错误: {result.error_message}", LOG_COMMAND
            )
            return f"真寻资源更新失败...错误: {result.error_message}"
        return "真寻资源更新完成!"

    @classmethod
    async def update_zhenxun(
        cls,
        bot: Bot,
        user_id: str,
        version_type: Literal["main", "release"],
        force: bool,
        source: Literal["git", "ali"],
        zip: bool,
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
            new_version = await ZhenxunRepoManager.zhenxun_zip_update(version_type)
            await PlatformUtils.send_superuser(
                bot, "真寻更新完成，开始安装依赖...", user_id
            )
            await VirtualEnvPackageManager.install_requirement(
                ZhenxunRepoConfig.REQUIREMENTS_FILE
            )
            return (
                f"版本更新完成！\n版本: {cur_version} -> {new_version}\n"
                "请重新启动真寻以完成更新!"
            )
        else:
            result = await ZhenxunRepoManager.zhenxun_git_update(
                source,
                branch=version_type,
                force=force,
            )
            if not result.success:
                logger.error(
                    f"真寻版本更新失败...错误: {result.error_message}",
                    LOG_COMMAND,
                )
                return f"版本更新失败...错误: {result.error_message}"
            await PlatformUtils.send_superuser(
                bot, "真寻更新完成，开始安装依赖...", user_id
            )
            await VirtualEnvPackageManager.install_requirement(
                ZhenxunRepoConfig.REQUIREMENTS_FILE
            )
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
        if ZhenxunRepoConfig.ZHENXUN_BOT_VERSION_FILE.exists():
            if text := ZhenxunRepoConfig.ZHENXUN_BOT_VERSION_FILE.open(
                encoding="utf8"
            ).readline():
                _version = text.split(":")[-1].strip()
        return _version
