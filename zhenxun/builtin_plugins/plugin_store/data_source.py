from pathlib import Path
import shutil

from aiocache import cached
import ujson as json

from zhenxun.builtin_plugins.auto_update.config import REQ_TXT_FILE_STRING
from zhenxun.builtin_plugins.plugin_store.models import StorePluginInfo
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.log import logger
from zhenxun.services.plugin_init import PluginInitManager
from zhenxun.utils.github_utils import GithubUtils
from zhenxun.utils.github_utils.models import RepoAPI
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.image_utils import BuildImage, ImageTemplate, RowStyle
from zhenxun.utils.manager.virtual_env_package_manager import VirtualEnvPackageManager
from zhenxun.utils.utils import is_number

from .config import (
    BASE_PATH,
    DEFAULT_GITHUB_URL,
    EXTRA_GITHUB_URL,
    LOG_COMMAND,
)


def row_style(column: str, text: str) -> RowStyle:
    """被动技能文本风格

    参数:
        column: 表头
        text: 文本内容

    返回:
        RowStyle: RowStyle
    """
    style = RowStyle()
    if column == "-" and text == "已安装":
        style.font_color = "#67C23A"
    return style


def install_requirement(plugin_path: Path):
    requirement_files = ["requirement.txt", "requirements.txt"]
    requirement_paths = [plugin_path / file for file in requirement_files]

    if existing_requirements := next(
        (path for path in requirement_paths if path.exists()), None
    ):
        VirtualEnvPackageManager.install_requirement(existing_requirements)


class StoreManager:
    @classmethod
    async def get_github_plugins(cls) -> list[StorePluginInfo]:
        """获取github插件列表信息

        返回:
            list[StorePluginInfo]: 插件列表数据
        """
        repo_info = GithubUtils.parse_github_url(DEFAULT_GITHUB_URL)
        if await repo_info.update_repo_commit():
            logger.info(f"获取最新提交: {repo_info.branch}", LOG_COMMAND)
        else:
            logger.warning(f"获取最新提交失败: {repo_info}", LOG_COMMAND)
        default_github_url = await repo_info.get_raw_download_urls("plugins.json")
        response = await AsyncHttpx.get(default_github_url, check_status_code=200)
        if response.status_code == 200:
            logger.info("获取github插件列表成功", LOG_COMMAND)
            return [StorePluginInfo(**detail) for detail in json.loads(response.text)]
        else:
            logger.warning(
                f"获取github插件列表失败: {response.status_code}", LOG_COMMAND
            )
        return []

    @classmethod
    async def get_extra_plugins(cls) -> list[StorePluginInfo]:
        """获取额外插件列表信息

        返回:
            list[StorePluginInfo]: 插件列表数据
        """
        repo_info = GithubUtils.parse_github_url(EXTRA_GITHUB_URL)
        if await repo_info.update_repo_commit():
            logger.info(f"获取最新提交: {repo_info.branch}", LOG_COMMAND)
        else:
            logger.warning(f"获取最新提交失败: {repo_info}", LOG_COMMAND)
        extra_github_url = await repo_info.get_raw_download_urls("plugins.json")
        response = await AsyncHttpx.get(extra_github_url, check_status_code=200)
        if response.status_code == 200:
            return [StorePluginInfo(**detail) for detail in json.loads(response.text)]
        else:
            logger.warning(
                f"获取github扩展插件列表失败: {response.status_code}", LOG_COMMAND
            )
        return []

    @classmethod
    @cached(60)
    async def get_data(cls) -> list[StorePluginInfo]:
        """获取插件信息数据

        返回:
            list[StorePluginInfo]: 插件信息数据
        """
        plugins = await cls.get_github_plugins()
        extra_plugins = await cls.get_extra_plugins()
        return [*plugins, *extra_plugins]

    @classmethod
    def version_check(cls, plugin_info: StorePluginInfo, suc_plugin: dict[str, str]):
        """版本检查

        参数:
            plugin_info: StorePluginInfo
            suc_plugin: 模块名: 版本号

        返回:
            str: 版本号
        """
        module = plugin_info.module
        if suc_plugin.get(module) and not cls.check_version_is_new(
            plugin_info, suc_plugin
        ):
            return f"{suc_plugin[module]} (有更新->{plugin_info.version})"
        return plugin_info.version

    @classmethod
    def check_version_is_new(
        cls, plugin_info: StorePluginInfo, suc_plugin: dict[str, str]
    ):
        """检查版本是否有更新

        参数:
            plugin_info: StorePluginInfo
            suc_plugin: 模块名: 版本号

        返回:
            bool: 是否有更新
        """
        module = plugin_info.module
        return suc_plugin.get(module) and plugin_info.version == suc_plugin[module]

    @classmethod
    async def get_loaded_plugins(cls, *args) -> list[tuple[str, str]]:
        """获取已加载的插件

        返回:
            list[str]: 已加载的插件
        """
        return await PluginInfo.filter(load_status=True).values_list(*args)

    @classmethod
    async def get_plugins_info(cls) -> BuildImage | str:
        """插件列表

        返回:
            BuildImage | str: 返回消息
        """
        plugin_list: list[StorePluginInfo] = await cls.get_data()
        column_name = ["-", "ID", "名称", "简介", "作者", "版本", "类型"]
        db_plugin_list = await cls.get_loaded_plugins("module", "version")
        suc_plugin = {p[0]: (p[1] or "0.1") for p in db_plugin_list}
        data_list = [
            [
                "已安装" if plugin_info.module in suc_plugin else "",
                id,
                plugin_info.name,
                plugin_info.description,
                plugin_info.author,
                cls.version_check(plugin_info, suc_plugin),
                plugin_info.plugin_type_name,
            ]
            for id, plugin_info in enumerate(plugin_list)
        ]
        return await ImageTemplate.table_page(
            "插件列表",
            "通过添加/移除插件 ID 来管理插件",
            column_name,
            data_list,
            text_style=row_style,
        )

    @classmethod
    async def add_plugin(cls, plugin_id: str) -> str:
        """添加插件

        参数:
            plugin_id: 插件id或模块名

        返回:
            str: 返回消息
        """
        plugin_list: list[StorePluginInfo] = await cls.get_data()
        try:
            plugin_key = await cls._resolve_plugin_key(plugin_id)
        except ValueError as e:
            return str(e)
        db_plugin_list = await cls.get_loaded_plugins("module")
        plugin_info = next((p for p in plugin_list if p.module == plugin_key), None)
        if plugin_info is None:
            return f"未找到插件 {plugin_key}"
        if plugin_info.module in [p[0] for p in db_plugin_list]:
            return f"插件 {plugin_info.name} 已安装，无需重复安装"
        is_external = True
        if plugin_info.github_url is None:
            plugin_info.github_url = DEFAULT_GITHUB_URL
            is_external = False
        version_split = plugin_info.version.split("-")
        if len(version_split) > 1:
            github_url_split = plugin_info.github_url.split("/tree/")
            plugin_info.github_url = f"{github_url_split[0]}/tree/{version_split[1]}"
        logger.info(f"正在安装插件 {plugin_info.name}...", LOG_COMMAND)
        await cls.install_plugin_with_repo(
            plugin_info.github_url,
            plugin_info.module_path,
            plugin_info.is_dir,
            is_external,
        )
        return f"插件 {plugin_info.name} 安装成功! 重启后生效"

    @classmethod
    async def install_plugin_with_repo(
        cls,
        github_url: str,
        module_path: str,
        is_dir: bool,
        is_external: bool = False,
    ):
        repo_api: RepoAPI
        repo_info = GithubUtils.parse_github_url(github_url)
        if await repo_info.update_repo_commit():
            logger.info(f"获取最新提交: {repo_info.branch}", LOG_COMMAND)
        else:
            logger.warning(f"获取最新提交失败: {repo_info}", LOG_COMMAND)
        logger.debug(f"成功获取仓库信息: {repo_info}", LOG_COMMAND)
        for repo_api in GithubUtils.iter_api_strategies():
            try:
                await repo_api.parse_repo_info(repo_info)
                break
            except Exception as e:
                logger.warning(
                    f"获取插件文件失败 | API类型: {repo_api.strategy}",
                    LOG_COMMAND,
                    e=e,
                )
                continue
        else:
            raise ValueError("所有API获取插件文件失败，请检查网络连接")
        if module_path == ".":
            module_path = ""
        replace_module_path = module_path.replace(".", "/")
        files = repo_api.get_files(
            module_path=replace_module_path + ("" if is_dir else ".py"),
            is_dir=is_dir,
        )
        download_urls = [await repo_info.get_raw_download_urls(file) for file in files]
        base_path = BASE_PATH / "plugins" if is_external else BASE_PATH
        base_path = base_path if module_path else base_path / repo_info.repo
        download_paths: list[Path | str] = [base_path / file for file in files]
        logger.debug(f"插件下载路径: {download_paths}", LOG_COMMAND)
        result = await AsyncHttpx.gather_download_file(download_urls, download_paths)
        for _id, success in enumerate(result):
            if not success:
                break
        else:
            # 安装依赖
            plugin_path = base_path / "/".join(module_path.split("."))
            try:
                req_files = repo_api.get_files(
                    f"{replace_module_path}/{REQ_TXT_FILE_STRING}", False
                )
                req_files.extend(
                    repo_api.get_files(f"{replace_module_path}/requirement.txt", False)
                )
                logger.debug(f"获取插件依赖文件列表: {req_files}", LOG_COMMAND)
                req_download_urls = [
                    await repo_info.get_raw_download_urls(file) for file in req_files
                ]
                req_paths: list[Path | str] = [plugin_path / file for file in req_files]
                logger.debug(f"插件依赖文件下载路径: {req_paths}", LOG_COMMAND)
                if req_files:
                    result = await AsyncHttpx.gather_download_file(
                        req_download_urls, req_paths
                    )
                    for success in result:
                        if not success:
                            raise Exception("插件依赖文件下载失败")
                    logger.debug(f"插件依赖文件列表: {req_paths}", LOG_COMMAND)
                    install_requirement(plugin_path)
            except ValueError as e:
                logger.warning("未获取到依赖文件路径...", e=e)
            return True
        raise Exception("插件下载失败...")

    @classmethod
    async def remove_plugin(cls, plugin_id: str) -> str:
        """移除插件

        参数:
            plugin_id: 插件id或模块名

        返回:
            str: 返回消息
        """
        plugin_list: list[StorePluginInfo] = await cls.get_data()
        try:
            plugin_key = await cls._resolve_plugin_key(plugin_id)
        except ValueError as e:
            return str(e)
        plugin_info = next((p for p in plugin_list if p.module == plugin_key), None)
        if plugin_info is None:
            return f"未找到插件 {plugin_key}"
        path = BASE_PATH
        if plugin_info.github_url:
            path = BASE_PATH / "plugins"
        for p in plugin_info.module_path.split("."):
            path = path / p
        if not plugin_info.is_dir:
            path = Path(f"{path}.py")
        if not path.exists():
            return f"插件 {plugin_info.name} 不存在..."
        logger.debug(f"尝试移除插件 {plugin_info.name} 文件: {path}", LOG_COMMAND)
        if plugin_info.is_dir:
            shutil.rmtree(path)
        else:
            path.unlink()
        await PluginInitManager.remove(f"zhenxun.{plugin_info.module_path}")
        return f"插件 {plugin_info.name} 移除成功! 重启后生效"

    @classmethod
    async def search_plugin(cls, plugin_name_or_author: str) -> BuildImage | str:
        """搜索插件

        参数:
            plugin_name_or_author: 插件名称或作者

        返回:
            BuildImage | str: 返回消息
        """
        plugin_list: list[StorePluginInfo] = await cls.get_data()
        db_plugin_list = await cls.get_loaded_plugins("module", "version")
        suc_plugin = {p[0]: (p[1] or "Unknown") for p in db_plugin_list}
        filtered_data = [
            (id, plugin_info)
            for id, plugin_info in enumerate(plugin_list)
            if plugin_name_or_author.lower() in plugin_info.name.lower()
            or plugin_name_or_author.lower() in plugin_info.author.lower()
        ]

        data_list = [
            [
                "已安装" if plugin_info.module in suc_plugin else "",
                id,
                plugin_info.name,
                plugin_info.description,
                plugin_info.author,
                cls.version_check(plugin_info, suc_plugin),
                plugin_info.plugin_type_name,
            ]
            for id, plugin_info in filtered_data
        ]
        if not data_list:
            return "未找到相关插件..."
        column_name = ["-", "ID", "名称", "简介", "作者", "版本", "类型"]
        return await ImageTemplate.table_page(
            "商店插件列表",
            "通过添加/移除插件 ID 来管理插件",
            column_name,
            data_list,
            text_style=row_style,
        )

    @classmethod
    async def update_plugin(cls, plugin_id: str) -> str:
        """更新插件

        参数:
            plugin_id: 插件id

        返回:
            str: 返回消息
        """
        plugin_list: list[StorePluginInfo] = await cls.get_data()
        try:
            plugin_key = await cls._resolve_plugin_key(plugin_id)
        except ValueError as e:
            return str(e)
        plugin_info = next((p for p in plugin_list if p.module == plugin_key), None)
        if plugin_info is None:
            return f"未找到插件 {plugin_key}"
        logger.info(f"尝试更新插件 {plugin_info.name}", LOG_COMMAND)
        db_plugin_list = await cls.get_loaded_plugins("module", "version")
        suc_plugin = {p[0]: (p[1] or "Unknown") for p in db_plugin_list}
        if plugin_info.module not in [p[0] for p in db_plugin_list]:
            return f"插件 {plugin_info.name} 未安装，无法更新"
        logger.debug(f"当前插件列表: {suc_plugin}", LOG_COMMAND)
        if cls.check_version_is_new(plugin_info, suc_plugin):
            return f"插件 {plugin_info.name} 已是最新版本"
        is_external = True
        if plugin_info.github_url is None:
            plugin_info.github_url = DEFAULT_GITHUB_URL
            is_external = False
        await cls.install_plugin_with_repo(
            plugin_info.github_url,
            plugin_info.module_path,
            plugin_info.is_dir,
            is_external,
        )
        return f"插件 {plugin_info.name} 更新成功! 重启后生效"

    @classmethod
    async def update_all_plugin(cls) -> str:
        """更新插件

        参数:
            plugin_id: 插件id

        返回:
            str: 返回消息
        """
        plugin_list: list[StorePluginInfo] = await cls.get_data()
        plugin_name_list = [p.name for p in plugin_list]
        update_failed_list = []
        update_success_list = []
        result = "--已更新{}个插件 {}个失败 {}个成功--"
        logger.info(f"尝试更新全部插件 {plugin_name_list}", LOG_COMMAND)
        for plugin_info in plugin_list:
            try:
                db_plugin_list = await cls.get_loaded_plugins("module", "version")
                suc_plugin = {p[0]: (p[1] or "Unknown") for p in db_plugin_list}
                if plugin_info.module not in [p[0] for p in db_plugin_list]:
                    logger.debug(
                        f"插件 {plugin_info.name}({plugin_info.module}) 未安装，跳过",
                        LOG_COMMAND,
                    )
                    continue
                if cls.check_version_is_new(plugin_info, suc_plugin):
                    logger.debug(
                        f"插件 {plugin_info.name}({plugin_info.module}) "
                        "已是最新版本，跳过",
                        LOG_COMMAND,
                    )
                    continue
                logger.info(
                    f"正在更新插件 {plugin_info.name}({plugin_info.module})",
                    LOG_COMMAND,
                )
                is_external = True
                if plugin_info.github_url is None:
                    plugin_info.github_url = DEFAULT_GITHUB_URL
                    is_external = False
                await cls.install_plugin_with_repo(
                    plugin_info.github_url,
                    plugin_info.module_path,
                    plugin_info.is_dir,
                    is_external,
                )
                update_success_list.append(plugin_info.name)
            except Exception as e:
                logger.error(
                    f"更新插件 {plugin_info.name}({plugin_info.module}) 失败",
                    LOG_COMMAND,
                    e=e,
                )
                update_failed_list.append(plugin_info.name)
        if not update_success_list and not update_failed_list:
            return "全部插件已是最新版本"
        if update_success_list:
            result += "\n* 以下插件更新成功:\n\t- {}".format(
                "\n\t- ".join(update_success_list)
            )
        if update_failed_list:
            result += "\n* 以下插件更新失败:\n\t- {}".format(
                "\n\t- ".join(update_failed_list)
            )
        return (
            result.format(
                len(update_success_list) + len(update_failed_list),
                len(update_failed_list),
                len(update_success_list),
            )
            + "\n重启后生效"
        )

    @classmethod
    async def _resolve_plugin_key(cls, plugin_id: str) -> str:
        """获取插件module

        参数:
            plugin_id: module，id或插件名称

        异常:
            ValueError: 插件不存在
            ValueError: 插件不存在

        返回:
            str: 插件模块名
        """
        plugin_list: list[StorePluginInfo] = await cls.get_data()
        if is_number(plugin_id):
            idx = int(plugin_id)
            if idx < 0 or idx >= len(plugin_list):
                raise ValueError("插件ID不存在...")
            return plugin_list[idx].module
        elif isinstance(plugin_id, str):
            result = (
                None if plugin_id not in [v.module for v in plugin_list] else plugin_id
            ) or next(v for v in plugin_list if v.name == plugin_id).module
            if not result:
                raise ValueError("插件 Module / 名称 不存在...")
            return result
