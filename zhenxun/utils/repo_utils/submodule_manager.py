"""
子模块管理工具
"""

import json
from pathlib import Path

from zhenxun.services.log import logger

from .config import LOG_COMMAND
from .github_manager import GithubManager
from .models import SubmoduleConfig, SubmoduleInfo, SubmoduleUpdateResult
from .utils import run_git_command


class SubmoduleManager:
    """子模块管理器"""

    def __init__(self, github_manager: GithubManager):
        """
        初始化子模块管理器

        参数:
            github_manager: GitHub管理器实例
        """
        self.github_manager = github_manager

    async def init_submodules(
        self, main_repo_path: Path, submodule_configs: list[SubmoduleConfig]
    ) -> bool:
        """
        初始化子模块

        参数:
            main_repo_path: 主仓库路径
            submodule_configs: 子模块配置列表

        返回:
            bool: 是否成功
        """
        try:
            # 检查是否在Git仓库中
            success, stdout, stderr = await run_git_command("status", main_repo_path)
            if not success:
                logger.error(f"路径 {main_repo_path} 不是有效的Git仓库", LOG_COMMAND)
                return False

            # 初始化每个子模块
            for config in submodule_configs:
                if not config.enabled:
                    continue

                await self._init_single_submodule(main_repo_path, config)

            # 更新子模块
            await self._update_submodules(main_repo_path)

            return True

        except Exception as e:
            logger.error(f"初始化子模块失败: {e}", LOG_COMMAND)
            return False

    async def _init_single_submodule(
        self, main_repo_path: Path, config: SubmoduleConfig
    ) -> bool:
        """
        初始化单个子模块

        参数:
            main_repo_path: 主仓库路径
            config: 子模块配置

        返回:
            bool: 是否成功
        """
        try:
            submodule_path = main_repo_path / config.path

            # 检查子模块是否已存在
            if submodule_path.exists() and (submodule_path / ".git").exists():
                logger.info(f"子模块 {config.name} 已存在，跳过初始化", LOG_COMMAND)
                return True

            # 添加子模块
            success, stdout, stderr = await run_git_command(
                f"submodule add -b {config.branch} {config.repo_url} {config.path}",
                main_repo_path,
            )

            if not success:
                logger.error(f"添加子模块 {config.name} 失败: {stderr}", LOG_COMMAND)
                return False

            logger.info(f"成功添加子模块 {config.name}", LOG_COMMAND)
            return True

        except Exception as e:
            logger.error(f"初始化子模块 {config.name} 失败: {e}", LOG_COMMAND)
            return False

    async def _update_submodules(self, main_repo_path: Path) -> bool:
        """
        更新所有子模块

        参数:
            main_repo_path: 主仓库路径

        返回:
            bool: 是否成功
        """
        try:
            # 更新子模块
            success, stdout, stderr = await run_git_command(
                "submodule update --init --recursive", main_repo_path
            )

            if not success:
                logger.error(f"更新子模块失败: {stderr}", LOG_COMMAND)
                return False

            logger.info("成功更新所有子模块", LOG_COMMAND)
            return True

        except Exception as e:
            logger.error(f"更新子模块失败: {e}", LOG_COMMAND)
            return False

    async def update_submodules(
        self, main_repo_path: Path, submodule_configs: list[SubmoduleConfig]
    ) -> list[SubmoduleUpdateResult]:
        """
        更新子模块

        参数:
            main_repo_path: 主仓库路径
            submodule_configs: 子模块配置列表

        返回:
            List[SubmoduleUpdateResult]: 更新结果列表
        """
        results = []

        for config in submodule_configs:
            if not config.enabled:
                continue

            result = await self._update_single_submodule(main_repo_path, config)
            results.append(result)

        return results

    async def _update_single_submodule(
        self, main_repo_path: Path, config: SubmoduleConfig
    ) -> SubmoduleUpdateResult:
        """
        更新单个子模块

        参数:
            main_repo_path: 主仓库路径
            config: 子模块配置

        返回:
            SubmoduleUpdateResult: 更新结果
        """
        result = SubmoduleUpdateResult(
            submodule_name=config.name,
            submodule_path=config.path,
            old_version="",
            new_version="",
        )

        try:
            submodule_path = main_repo_path / config.path

            # 检查子模块是否存在
            if not submodule_path.exists():
                result.error_message = f"子模块路径不存在: {submodule_path}"
                return result

            # 获取当前版本
            success, stdout, stderr = await run_git_command(
                "rev-parse HEAD", submodule_path
            )

            if not success:
                result.error_message = f"获取当前版本失败: {stderr}"
                return result

            old_version = stdout.strip()
            result.old_version = old_version

            # 获取远程最新版本
            success, stdout, stderr = await run_git_command(
                f"ls-remote origin {config.branch}", submodule_path
            )

            if not success:
                result.error_message = f"获取远程版本失败: {stderr}"
                return result

            # 解析最新版本
            lines = stdout.strip().split("\n")
            if not lines or not lines[0]:
                result.error_message = "无法获取远程版本信息"
                return result

            latest_version = lines[0].split("\t")[0]
            result.new_version = latest_version

            # 检查是否需要更新
            if old_version == latest_version:
                result.success = True
                logger.info(f"子模块 {config.name} 已是最新版本", LOG_COMMAND)
                return result

            # 更新子模块
            success, stdout, stderr = await run_git_command(
                f"pull origin {config.branch}", submodule_path
            )

            if not success:
                result.error_message = f"更新子模块失败: {stderr}"
                return result

            # 更新主仓库中的子模块引用
            success, stdout, stderr = await run_git_command(
                f"add {config.path}", main_repo_path
            )

            if not success:
                result.error_message = f"更新主仓库引用失败: {stderr}"
                return result

            result.success = True
            logger.info(
                f"成功更新子模块 {config.name}: {old_version} -> {latest_version}",
                LOG_COMMAND,
            )

        except Exception as e:
            result.error_message = f"更新子模块时发生错误: {e}"
            logger.error(f"更新子模块 {config.name} 失败: {e}", LOG_COMMAND)

        return result

    async def get_submodule_info(
        self, main_repo_path: Path, submodule_configs: list[SubmoduleConfig]
    ) -> list[SubmoduleInfo]:
        """
        获取子模块信息

        参数:
            main_repo_path: 主仓库路径
            submodule_configs: 子模块配置列表

        返回:
            List[SubmoduleInfo]: 子模块信息列表
        """
        submodule_infos = []

        for config in submodule_configs:
            if not config.enabled:
                continue

            info = await self._get_single_submodule_info(main_repo_path, config)
            submodule_infos.append(info)

        return submodule_infos

    async def _get_single_submodule_info(
        self, main_repo_path: Path, config: SubmoduleConfig
    ) -> SubmoduleInfo:
        """
        获取单个子模块信息

        参数:
            main_repo_path: 主仓库路径
            config: 子模块配置

        返回:
            SubmoduleInfo: 子模块信息
        """
        info = SubmoduleInfo(config=config)

        try:
            submodule_path = main_repo_path / config.path

            if not submodule_path.exists():
                info.update_status = "error"
                return info

            # 获取当前版本
            success, stdout, stderr = await run_git_command(
                "rev-parse HEAD", submodule_path
            )

            if success:
                info.current_version = stdout.strip()

            # 获取远程最新版本
            success, stdout, stderr = await run_git_command(
                f"ls-remote origin {config.branch}", submodule_path
            )

            if success and stdout.strip():
                lines = stdout.strip().split("\n")
                if lines and lines[0]:
                    info.latest_version = lines[0].split("\t")[0]

            # 确定更新状态
            if info.current_version and info.latest_version:
                if info.current_version == info.latest_version:
                    info.update_status = "up_to_date"
                else:
                    info.update_status = "outdated"
            else:
                info.update_status = "unknown"

        except Exception as e:
            info.update_status = "error"
            logger.error(f"获取子模块 {config.name} 信息失败: {e}", LOG_COMMAND)

        return info

    def save_submodule_configs(
        self, main_repo_path: Path, submodule_configs: list[SubmoduleConfig]
    ) -> bool:
        """
        保存子模块配置到文件

        参数:
            main_repo_path: 主仓库路径
            submodule_configs: 子模块配置列表

        返回:
            bool: 是否成功
        """
        try:
            config_file = main_repo_path / ".submodules.json"

            # 转换为字典格式
            configs_dict = []
            for config in submodule_configs:
                config_dict = {
                    "name": config.name,
                    "path": config.path,
                    "repo_url": config.repo_url,
                    "branch": config.branch,
                    "enabled": config.enabled,
                    "include_patterns": config.include_patterns,
                    "exclude_patterns": config.exclude_patterns,
                }
                configs_dict.append(config_dict)

            # 保存到文件
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(configs_dict, f, indent=2, ensure_ascii=False)

            logger.info(f"子模块配置已保存到 {config_file}", LOG_COMMAND)
            return True

        except Exception as e:
            logger.error(f"保存子模块配置失败: {e}", LOG_COMMAND)
            return False

    def load_submodule_configs(self, main_repo_path: Path) -> list[SubmoduleConfig]:
        """
        从文件加载子模块配置

        参数:
            main_repo_path: 主仓库路径

        返回:
            List[SubmoduleConfig]: 子模块配置列表
        """
        try:
            config_file = main_repo_path / ".submodules.json"

            if not config_file.exists():
                logger.warning(f"子模块配置文件不存在: {config_file}", LOG_COMMAND)
                return []

            with open(config_file, encoding="utf-8") as f:
                configs_dict = json.load(f)

            # 转换为SubmoduleConfig对象
            configs = []
            for config_dict in configs_dict:
                config = SubmoduleConfig(
                    name=config_dict["name"],
                    path=config_dict["path"],
                    repo_url=config_dict["repo_url"],
                    branch=config_dict.get("branch", "main"),
                    enabled=config_dict.get("enabled", True),
                    include_patterns=config_dict.get("include_patterns"),
                    exclude_patterns=config_dict.get("exclude_patterns"),
                )
                configs.append(config)

            logger.info(
                f"从 {config_file} 加载了 {len(configs)} 个子模块配置", LOG_COMMAND
            )
            return configs

        except Exception as e:
            logger.error(f"加载子模块配置失败: {e}", LOG_COMMAND)
            return []
