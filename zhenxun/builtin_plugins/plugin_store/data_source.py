from pathlib import Path
import shutil
import subprocess

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
from zhenxun.utils.utils import is_number

from .config import BASE_PATH, DEFAULT_GITHUB_URL, EXTRA_GITHUB_URL

BAT_FILE = Path() / "win启动.bat"

WIN_COMMAND = ["./Python310/python.exe", "-m", "pip", "install", "-r"]

DEFAULT_COMMAND = ["poetry", "run", "pip", "install", "-r"]


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

    existing_requirements = next(
        (path for path in requirement_paths if path.exists()), None
    )

    if not existing_requirements:
        logger.debug(
            f"No requirement.txt found for plugin: {plugin_path.name}", "插件管理"
        )
        return

    try:
        command = WIN_COMMAND if BAT_FILE.exists() else DEFAULT_COMMAND
        command.append(str(existing_requirements))
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        logger.debug(
            "Successfully installed dependencies for"
            f" plugin: {plugin_path.name}. Output:\n{result.stdout}",
            "插件管理",
        )
    except subprocess.CalledProcessError:
        logger.error(
            f"Failed to install dependencies for plugin: {plugin_path.name}. "
            " Error:\n{e.stderr}"
        )


class ShopManage:
    @classmethod
    @cached(60)
    async def get_data(cls) -> dict[str, StorePluginInfo]:
        """获取插件信息数据

        异常:
            ValueError: 访问请求失败

        返回:
            dict: 插件信息数据
        """
        default_github_repo = GithubUtils.parse_github_url(DEFAULT_GITHUB_URL)
        extra_github_repo = GithubUtils.parse_github_url(EXTRA_GITHUB_URL)
        for repo_info in [default_github_repo, extra_github_repo]:
            if await repo_info.update_repo_commit():
                logger.info(f"获取最新提交: {repo_info.branch}", "插件管理")
            else:
                logger.warning(f"获取最新提交失败: {repo_info}", "插件管理")
        default_github_url = await default_github_repo.get_raw_download_urls(
            "plugins.json"
        )
        extra_github_url = await extra_github_repo.get_raw_download_urls("plugins.json")
        res = await AsyncHttpx.get(default_github_url, check_status_code=200)
        res2 = await AsyncHttpx.get(extra_github_url, check_status_code=200)

        # 检查请求结果
        if res.status_code != 200 or res2.status_code != 200:
            raise ValueError(f"下载错误, code: {res.status_code}, {res2.status_code}")

        # 解析并合并返回的 JSON 数据
        data1 = json.loads(res.text)
        data2 = json.loads(res2.text)
        return {
            name: StorePluginInfo(**detail)
            for name, detail in {**data1, **data2}.items()
        }

    @classmethod
    def version_check(cls, plugin_info: StorePluginInfo, suc_plugin: dict[str, str]):
        """版本检查

        参数:
            plugin_info: StorePluginInfo
            suc_plugin: dict[str, str]

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
            suc_plugin: dict[str, str]

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
        data: dict[str, StorePluginInfo] = await cls.get_data()
        column_name = ["-", "ID", "名称", "简介", "作者", "版本", "类型"]
        plugin_list = await cls.get_loaded_plugins("module", "version")
        suc_plugin = {p[0]: (p[1] or "0.1") for p in plugin_list}
        data_list = [
            [
                "已安装" if plugin_info[1].module in suc_plugin else "",
                id,
                plugin_info[0],
                plugin_info[1].description,
                plugin_info[1].author,
                cls.version_check(plugin_info[1], suc_plugin),
                plugin_info[1].plugin_type_name,
            ]
            for id, plugin_info in enumerate(data.items())
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
        data: dict[str, StorePluginInfo] = await cls.get_data()
        try:
            plugin_key = await cls._resolve_plugin_key(plugin_id)
        except ValueError as e:
            return str(e)
        plugin_list = await cls.get_loaded_plugins("module")
        plugin_info = data[plugin_key]
        if plugin_info.module in [p[0] for p in plugin_list]:
            return f"插件 {plugin_key} 已安装，无需重复安装"
        is_external = True
        if plugin_info.github_url is None:
            plugin_info.github_url = DEFAULT_GITHUB_URL
            is_external = False
        version_split = plugin_info.version.split("-")
        if len(version_split) > 1:
            github_url_split = plugin_info.github_url.split("/tree/")
            plugin_info.github_url = f"{github_url_split[0]}/tree/{version_split[1]}"
        logger.info(f"正在安装插件 {plugin_key}...")
        await cls.install_plugin_with_repo(
            plugin_info.github_url,
            plugin_info.module_path,
            plugin_info.is_dir,
            is_external,
        )
        return f"插件 {plugin_key} 安装成功! 重启后生效"

    @classmethod
    async def install_plugin_with_repo(
        cls, github_url: str, module_path: str, is_dir: bool, is_external: bool = False
    ):
        files: list[str]
        repo_api: RepoAPI
        repo_info = GithubUtils.parse_github_url(github_url)
        if await repo_info.update_repo_commit():
            logger.info(f"获取最新提交: {repo_info.branch}", "插件管理")
        else:
            logger.warning(f"获取最新提交失败: {repo_info}", "插件管理")
        logger.debug(f"成功获取仓库信息: {repo_info}", "插件管理")
        for repo_api in GithubUtils.iter_api_strategies():
            try:
                await repo_api.parse_repo_info(repo_info)
                break
            except Exception as e:
                logger.warning(
                    f"获取插件文件失败: {e} | API类型: {repo_api.strategy}", "插件管理"
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
        logger.debug(f"插件下载路径: {download_paths}", "插件管理")
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
                logger.debug(f"获取插件依赖文件列表: {req_files}", "插件管理")
                req_download_urls = [
                    await repo_info.get_raw_download_urls(file) for file in req_files
                ]
                req_paths: list[Path | str] = [plugin_path / file for file in req_files]
                logger.debug(f"插件依赖文件下载路径: {req_paths}", "插件管理")
                if req_files:
                    result = await AsyncHttpx.gather_download_file(
                        req_download_urls, req_paths
                    )
                    for success in result:
                        if not success:
                            raise Exception("插件依赖文件下载失败")
                    logger.debug(f"插件依赖文件列表: {req_paths}", "插件管理")
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
        data: dict[str, StorePluginInfo] = await cls.get_data()
        try:
            plugin_key = await cls._resolve_plugin_key(plugin_id)
        except ValueError as e:
            return str(e)
        plugin_info = data[plugin_key]
        path = BASE_PATH
        if plugin_info.github_url:
            path = BASE_PATH / "plugins"
        for p in plugin_info.module_path.split("."):
            path = path / p
        if not plugin_info.is_dir:
            path = Path(f"{path}.py")
        if not path.exists():
            return f"插件 {plugin_key} 不存在..."
        logger.debug(f"尝试移除插件 {plugin_key} 文件: {path}", "插件管理")
        if plugin_info.is_dir:
            shutil.rmtree(path)
        else:
            path.unlink()
        await PluginInitManager.remove(f"zhenxun.{plugin_info.module_path}")
        return f"插件 {plugin_key} 移除成功! 重启后生效"

    @classmethod
    async def search_plugin(cls, plugin_name_or_author: str) -> BuildImage | str:
        """搜索插件

        参数:
            plugin_name_or_author: 插件名称或作者

        返回:
            BuildImage | str: 返回消息
        """
        data: dict[str, StorePluginInfo] = await cls.get_data()
        plugin_list = await cls.get_loaded_plugins("module", "version")
        suc_plugin = {p[0]: (p[1] or "Unknown") for p in plugin_list}
        filtered_data = [
            (id, plugin_info)
            for id, plugin_info in enumerate(data.items())
            if plugin_name_or_author.lower() in plugin_info[0].lower()
            or plugin_name_or_author.lower() in plugin_info[1].author.lower()
        ]

        data_list = [
            [
                "已安装" if plugin_info[1].module in suc_plugin else "",
                id,
                plugin_info[0],
                plugin_info[1].description,
                plugin_info[1].author,
                cls.version_check(plugin_info[1], suc_plugin),
                plugin_info[1].plugin_type_name,
            ]
            for id, plugin_info in filtered_data
        ]
        if not data_list:
            return "未找到相关插件..."
        column_name = ["-", "ID", "名称", "简介", "作者", "版本", "类型"]
        return await ImageTemplate.table_page(
            "插件列表",
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
        data: dict[str, StorePluginInfo] = await cls.get_data()
        try:
            plugin_key = await cls._resolve_plugin_key(plugin_id)
        except ValueError as e:
            return str(e)
        logger.info(f"尝试更新插件 {plugin_key}", "插件管理")
        plugin_info = data[plugin_key]
        plugin_list = await cls.get_loaded_plugins("module", "version")
        suc_plugin = {p[0]: (p[1] or "Unknown") for p in plugin_list}
        if plugin_info.module not in [p[0] for p in plugin_list]:
            return f"插件 {plugin_key} 未安装，无法更新"
        logger.debug(f"当前插件列表: {suc_plugin}", "插件管理")
        if cls.check_version_is_new(plugin_info, suc_plugin):
            return f"插件 {plugin_key} 已是最新版本"
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
        return f"插件 {plugin_key} 更新成功! 重启后生效"

    @classmethod
    async def update_all_plugin(cls) -> str:
        """更新插件

        参数:
            plugin_id: 插件id

        返回:
            str: 返回消息
        """
        data: dict[str, StorePluginInfo] = await cls.get_data()
        plugin_list = list(data.keys())
        update_failed_list = []
        update_success_list = []
        result = "--已更新{}个插件 {}个失败 {}个成功--"
        logger.info(f"尝试更新全部插件 {plugin_list}", "插件管理")
        for plugin_key in plugin_list:
            try:
                plugin_info = data[plugin_key]
                plugin_list = await cls.get_loaded_plugins("module", "version")
                suc_plugin = {p[0]: (p[1] or "Unknown") for p in plugin_list}
                if plugin_info.module not in [p[0] for p in plugin_list]:
                    logger.debug(f"插件 {plugin_key} 未安装，跳过", "插件管理")
                    continue
                if cls.check_version_is_new(plugin_info, suc_plugin):
                    logger.debug(f"插件 {plugin_key} 已是最新版本，跳过", "插件管理")
                    continue
                logger.info(f"正在更新插件 {plugin_key}", "插件管理")
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
                update_success_list.append(plugin_key)
            except Exception as e:
                logger.error(f"更新插件 {plugin_key} 失败: {e}", "插件管理")
                update_failed_list.append(plugin_key)
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
        data: dict[str, StorePluginInfo] = await cls.get_data()
        if is_number(plugin_id):
            idx = int(plugin_id)
            if idx < 0 or idx >= len(data):
                raise ValueError("插件ID不存在...")
            return list(data.keys())[idx]
        elif isinstance(plugin_id, str):
            if plugin_id not in [v.module for k, v in data.items()]:
                raise ValueError("插件Module不存在...")
            return {v.module: k for k, v in data.items()}[plugin_id]
