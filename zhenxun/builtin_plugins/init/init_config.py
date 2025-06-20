from pathlib import Path

import nonebot
from nonebot import get_loaded_plugins
from nonebot.drivers import Driver
from nonebot.plugin import Plugin
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from zhenxun.configs.config import Config
from zhenxun.configs.path_config import DATA_PATH
from zhenxun.configs.utils import RegisterConfig
from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

_yaml = YAML(pure=True)
_yaml.allow_unicode = True
_yaml.indent = 2

driver: Driver = nonebot.get_driver()

SIMPLE_CONFIG_FILE = DATA_PATH / "config.yaml"

old_config_file = Path() / "zhenxun" / "configs" / "config.yaml"
if old_config_file.exists():
    old_config_file.rename(SIMPLE_CONFIG_FILE)


def _handle_config(plugin: Plugin, exists_module: list[str]):
    """处理配置项

    参数:
        plugin: Plugin
    """
    if plugin.metadata and plugin.metadata.extra:
        extra = plugin.metadata.extra
        if configs := extra.get("configs"):
            for config in configs:
                reg_config = RegisterConfig(**config)
                module = reg_config.module or plugin.name
                g_config = Config.get(module)
                g_config.name = plugin.metadata.name
                Config.add_plugin_config(
                    module,
                    reg_config.key,
                    reg_config.value,
                    help=reg_config.help,
                    default_value=reg_config.default_value,
                    type=reg_config.type,
                    arg_parser=reg_config.arg_parser,
                    _override=False,
                )
                exists_module.append(f"{module}:{reg_config.key}".lower())


def _generate_simple_config(exists_module: list[str]):
    """
    生成简易配置

    异常:
        AttributeError: _description_
    """
    # 读取用户配置
    _data = {}
    _tmp_data = {}
    exists_module += Config.add_module
    if SIMPLE_CONFIG_FILE.exists():
        _data = _yaml.load(SIMPLE_CONFIG_FILE.open(encoding="utf8"))
    # 将简易配置文件的数据填充到配置文件
    for module in Config.keys():
        _tmp_data[module] = {}
        for k in Config[module].configs.keys():
            try:
                if _data.get(module) and k in _data[module].keys():
                    Config.set_config(module, k, _data[module][k])
                if f"{module}:{k}".lower() in exists_module:
                    _tmp_data[module][k] = Config.get_config(module, k)
            except AttributeError as e:
                raise AttributeError(f"{e}\n可能为config.yaml配置文件填写不规范") from e
        if not _tmp_data[module]:
            _tmp_data.pop(module)
    Config.save()
    temp_file = DATA_PATH / "temp_config.yaml"
    # 重新生成简易配置文件
    try:
        with open(temp_file, "w", encoding="utf8") as wf:
            _yaml.dump(_tmp_data, wf)
        with open(temp_file, encoding="utf8") as rf:
            _data = _yaml.load(rf)
        # 添加注释
        for module in _data.keys():
            help_text = ""
            plugin_name = Config.get(module).name or module
            help_text += plugin_name + "\n"
            for k in _data[module].keys():
                help_text += f"{k}: {Config[module].configs[k].help}" + "\n"
            _data.yaml_set_comment_before_after_key(after=help_text[:-1], key=module)
        with SIMPLE_CONFIG_FILE.open("w", encoding="utf8") as wf:
            _yaml.dump(_data, wf)
    except Exception as e:
        logger.error("生成简易配置注释错误...", e=e)
    if temp_file.exists():
        temp_file.unlink()


@PriorityLifecycle.on_startup(priority=0)
def _():
    """
    初始化插件数据配置
    """
    plugins2config_file = DATA_PATH / "configs" / "plugins2config.yaml"
    exists_module = []
    for plugin in get_loaded_plugins():
        if plugin.metadata:
            _handle_config(plugin, exists_module)
    if not Config.is_empty():
        Config.save()
        _data: CommentedMap = _yaml.load(plugins2config_file.open(encoding="utf8"))
        for module in _data.keys():
            plugin_name = Config.get(module).name
            _data.yaml_set_comment_before_after_key(
                after=f"{plugin_name}",
                key=module,
            )
        # 存完插件基本设置
        with plugins2config_file.open("w", encoding="utf8") as wf:
            _yaml.dump(_data, wf)
    _generate_simple_config(exists_module)
    Config.reload()
