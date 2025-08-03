#!/usr/bin/env python3
"""
GitHub子模块快速设置脚本
"""

import asyncio
from pathlib import Path
import sys

from zhenxun.services.log import logger
from zhenxun.utils.repo_utils import (
    GithubRepoManager,
    SubmoduleConfig,
)


def create_sample_configs():
    """创建示例子模块配置"""
    return [
        SubmoduleConfig(
            name="frontend-ui",
            path="frontend/ui",
            repo_url="https://github.com/your-org/frontend-ui",
            branch="main",
            enabled=True,
            include_patterns=["*.js", "*.css", "*.html", "*.vue", "*.ts"],
            exclude_patterns=["node_modules/*", "*.log", "dist/*", "coverage/*"],
        ),
        SubmoduleConfig(
            name="backend-api",
            path="backend/api",
            repo_url="https://github.com/your-org/backend-api",
            branch="develop",
            enabled=True,
            include_patterns=["*.py", "*.json", "requirements.txt", "*.yml"],
            exclude_patterns=["__pycache__/*", "*.pyc", "venv/*", ".pytest_cache/*"],
        ),
        SubmoduleConfig(
            name="shared-lib",
            path="libs/shared",
            repo_url="https://github.com/your-org/shared-lib",
            branch="main",
            enabled=True,
            include_patterns=["*.py", "*.js", "*.ts", "*.json"],
            exclude_patterns=["tests/*", "docs/*", "examples/*"],
        ),
    ]


async def setup_submodules(project_path: str, configs: list[SubmoduleConfig]):
    """设置子模块"""
    main_repo_path = Path(project_path)

    logger.info(f"正在为项目 {project_path} 设置子模块...")

    # 检查路径是否存在
    if not main_repo_path.exists():
        logger.info(f"错误: 项目路径 {project_path} 不存在")
        return False

    # 检查是否是Git仓库
    git_dir = main_repo_path / ".git"
    if not git_dir.exists():
        logger.info(f"错误: {project_path} 不是Git仓库")
        logger.info("请先执行: git init")
        return False

    # 初始化子模块
    logger.info("正在初始化子模块...")
    success = await GithubRepoManager.init_submodules(main_repo_path, configs)

    if not success:
        logger.info("子模块初始化失败！")
        return False

    # 保存配置
    logger.info("正在保存子模块配置...")
    await GithubRepoManager.save_submodule_configs(main_repo_path, configs)

    logger.info("✓ 子模块设置完成！")
    logger.info(f"配置文件已保存到: {main_repo_path / '.submodules.json'}")

    return True


async def update_submodules(project_path: str):
    """更新子模块"""
    main_repo_path = Path(project_path)

    logger.info(f"正在更新项目 {project_path} 的子模块...")

    # 加载配置
    configs = await GithubRepoManager.load_submodule_configs(main_repo_path)

    if not configs:
        logger.info("未找到子模块配置")
        return False

    logger.info(f"找到 {len(configs)} 个子模块配置")

    # 获取子模块信息
    infos = await GithubRepoManager.get_submodule_info(main_repo_path, configs)

    logger.info("\n子模块状态:")
    for info in infos:
        status_icon = (
            "✓"
            if info.update_status == "up_to_date"
            else "⚠"
            if info.update_status == "outdated"
            else "✗"
        )
        logger.info(
            f"{status_icon} {info.config.name}"
            f"({info.config.path}) - {info.update_status}"
        )

    # 更新子模块
    logger.info("\n正在更新子模块...")
    results = await GithubRepoManager.update_submodules(main_repo_path, configs)

    success_count = 0
    for result in results:
        if result.success:
            success_count += 1
            if result.old_version != result.new_version:
                logger.info(f"  ✓ {result.submodule_name} 已更新")
            else:
                logger.info(f"  ✓ {result.submodule_name} 已是最新版本")
        else:
            logger.info(f"  ✗ {result.submodule_name} 更新失败: {result.error_message}")

    logger.info(f"\n更新完成: {success_count}/{len(results)} 个子模块更新成功")
    return success_count == len(results)


async def show_submodule_info(project_path: str):
    """显示子模块信息"""
    main_repo_path = Path(project_path)

    logger.info(f"项目 {project_path} 的子模块信息:")

    # 加载配置
    configs = await GithubRepoManager.load_submodule_configs(main_repo_path)

    if not configs:
        logger.info("未找到子模块配置")
        return

    # 获取详细信息
    infos = await GithubRepoManager.get_submodule_info(main_repo_path, configs)

    for info in infos:
        logger.info(f"\n子模块: {info.config.name}")
        logger.info(f"  路径: {info.config.path}")
        logger.info(f"  仓库: {info.config.repo_url}")
        logger.info(f"  分支: {info.config.branch}")
        logger.info(f"  状态: {info.update_status}")
        logger.info(f"  启用: {info.config.enabled}")

        if info.current_version:
            logger.info(f"  当前版本: {info.current_version[:8]}")
        if info.latest_version:
            logger.info(f"  最新版本: {info.latest_version[:8]}")

        if info.config.include_patterns:
            logger.info(f"  包含文件: {', '.join(info.config.include_patterns)}")
        if info.config.exclude_patterns:
            logger.info(f"  排除文件: {', '.join(info.config.exclude_patterns)}")


def print_info_usage():
    """打印使用说明"""
    logger.info("GitHub子模块管理工具")
    logger.info("用法:")
    logger.info("  python submodule_setup.py setup <项目路径>")
    logger.info("  python submodule_setup.py update <项目路径>")
    logger.info("  python submodule_setup.py info <项目路径>")
    logger.info("示例:")
    logger.info("  python submodule_setup.py setup ./my_project")
    logger.info("  python submodule_setup.py update ./my_project")
    logger.info("  python submodule_setup.py info ./my_project")


async def main():
    """主函数"""
    if len(sys.argv) < 3:
        print_info_usage()
        return

    command = sys.argv[1]
    project_path = sys.argv[2]

    if command == "setup":
        configs = create_sample_configs()
        await setup_submodules(project_path, configs)

    elif command == "update":
        await update_submodules(project_path)

    elif command == "info":
        await show_submodule_info(project_path)

    else:
        logger.info(f"未知命令: {command}")
        print_info_usage()


if __name__ == "__main__":
    asyncio.run(main())
