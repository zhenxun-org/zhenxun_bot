from collections.abc import Callable
import copy
from pathlib import Path
from typing import Any, TypeVar

import cattrs
from nonebot.log import logger as _nonebot_logger
from pydantic import BaseModel, Field
from ruamel.yaml import YAML
from ruamel.yaml.scanner import ScannerError

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.utils.pydantic_compat import (
    _dump_pydantic_obj,
    _is_pydantic_type,
    model_dump,
    parse_as,
)

from .models import (
    AICallableParam,
    AICallableProperties,
    AICallableTag,
    BaseBlock,
    Command,
    ConfigModel,
    Example,
    PluginCdBlock,
    PluginCountBlock,
    PluginExtraData,
    PluginSetting,
    RegisterConfig,
    Task,
)

_yaml = YAML(pure=True)
_yaml.indent = 2
_yaml.allow_unicode = True

T = TypeVar("T")
_MISSING = object()


class _ConfigLogger:
    @staticmethod
    def _emit(level: str, info: str, *, e: Exception | None = None) -> None:
        logger = _nonebot_logger.opt(exception=e) if e else _nonebot_logger
        getattr(logger, level)(info)

    @classmethod
    def debug(cls, info: str, *_, e: Exception | None = None, **__) -> None:
        cls._emit("debug", info, e=e)

    @classmethod
    def info(cls, info: str, *_, e: Exception | None = None, **__) -> None:
        cls._emit("info", info, e=e)

    @classmethod
    def warning(cls, info: str, *_, e: Exception | None = None, **__) -> None:
        cls._emit("warning", info, e=e)

    @classmethod
    def error(cls, info: str, *_, e: Exception | None = None, **__) -> None:
        cls._emit("error", info, e=e)


logger = _ConfigLogger()


class NoSuchConfig(Exception):
    pass


class ConfigGroup(BaseModel):
    """
    配置组
    """

    module: str
    """模块名"""
    name: str | None = None
    """插件名"""
    configs: dict[str, ConfigModel] = Field(default_factory=dict)
    """配置项列表"""

    def get(self, c: str, default: Any = None, *, build_model: bool = True) -> Any:
        """
        获取配置项的值。如果指定了类型，会自动构建实例。
        """
        key = c.upper()
        cfg = self.configs.get(key)

        if cfg is None:
            return default

        value_to_process = cfg.value if cfg.value is not None else cfg.default_value

        if value_to_process is None:
            return default

        if cfg.arg_parser:
            try:
                return cfg.arg_parser(value_to_process)
            except Exception as e:
                logger.debug(
                    f"配置项类型转换 MODULE: [<u><y>{self.module}</y></u>] | "
                    f"KEY: [<u><y>{key}</y></u>] 的自定义解析器失败，将使用原始值",
                    e=e,
                )
                return value_to_process

        if not build_model or not cfg.type:
            return value_to_process

        try:
            if _is_pydantic_type(cfg.type):
                parsed_value = parse_as(cfg.type, value_to_process)
                return parsed_value
            else:
                structured_value = cattrs.structure(value_to_process, cfg.type)
                return structured_value
        except Exception as e:
            logger.error(
                f"❌ 配置项 '{self.module}.{key}' 自动类型转换失败 "
                f"(目标类型: {cfg.type})，将返回原始值。请检查配置文件格式。错误: {e}",
                e=e,
            )
            return value_to_process

    def to_dict(self, **kwargs):
        return model_dump(self, **kwargs)


class ConfigsManager:
    """
    插件配置 与 资源 管理器
    """

    def __init__(self, file: Path):
        self._data: dict[str, ConfigGroup] = {}
        self._simple_data: dict = {}
        self._simple_file = DATA_PATH / "config.yaml"
        self.add_module = []
        if file:
            file.parent.mkdir(exist_ok=True, parents=True)
            self.file = file
            self.load_data()
        if self._simple_file.exists():
            self._load_simple_data(raise_on_error=True)
            self._apply_simple_data(warn_unknown=False)

    def _load_simple_data(self, *, raise_on_error: bool = False) -> None:
        if not self._simple_file.exists():
            self._simple_data = {}
            return
        try:
            with self._simple_file.open(encoding="utf8") as f:
                simple_data = _yaml.load(f) or {}
        except ScannerError as e:
            message = (
                f"{e}\n**********************************************\n"
                f"****** 可能为config.yaml配置文件填写不规范 ******\n"
                f"**********************************************"
            )
            if raise_on_error:
                raise ScannerError(message) from e
            logger.warning(f"读取config.yaml失败，已跳过本次重载: {message}", e=e)
            return
        except Exception as e:
            if raise_on_error:
                raise RuntimeError(f"读取config.yaml失败: {e}") from e
            logger.warning(f"读取config.yaml失败，已跳过本次重载: {e}", e=e)
            return
        if not isinstance(simple_data, dict):
            message = "config.yaml 顶层必须为字典，已忽略当前内容。"
            if raise_on_error:
                raise ValueError(message)
            logger.warning(message)
            self._simple_data = {}
            return
        self._simple_data = simple_data

    @staticmethod
    def _find_mapping_key(data: dict, key: str) -> str | None:
        if key in data:
            return key
        upper_key = key.upper()
        for raw_key in data:
            if str(raw_key).upper() == upper_key:
                return raw_key
        return None

    def _get_simple_config_value(self, module: str, key: str) -> Any:
        module_data = self._simple_data.get(module)
        if not isinstance(module_data, dict):
            return _MISSING
        simple_key = self._find_mapping_key(module_data, key.upper())
        if simple_key is None:
            return _MISSING
        return module_data[simple_key]

    def _apply_simple_data(self, *, warn_unknown: bool) -> None:
        for module, module_data in self._simple_data.items():
            if not isinstance(module_data, dict):
                if warn_unknown:
                    logger.warning(f"配置组 {module} 不是字典，已跳过。")
                continue
            config_group = self._data.get(module)
            if not config_group:
                if warn_unknown:
                    logger.warning(f"未知配置组 {module}，已跳过。")
                continue
            for raw_key, value in module_data.items():
                key = str(raw_key).upper()
                config_key = self._find_mapping_key(config_group.configs, key)
                if config_key is None:
                    if warn_unknown:
                        logger.warning(f"未知配置项 {module}.{raw_key}，已跳过。")
                    continue
                config_group.configs[config_key].value = value

    def set_name(self, module: str, name: str):
        """设置插件配置中文名出

        参数:
            module: 模块名
            name: 中文名称

        异常:
            ValueError: module不能为为空
        """
        if not module:
            raise ValueError("set_name: module不能为为空")
        if data := self._data.get(module):
            data.name = name

    def _merge_dicts(self, new_data: dict, original_data: dict) -> dict:
        """合并两个字典，只进行key值的新增和删除操作，不修改原有key的值

        递归处理嵌套字典，确保所有层级的key保持一致

        参数:
            new_data: 新数据字典
            original_data: 原数据字典

        返回:
            合并后的字典
        """
        result = dict(original_data)

        for key, value in new_data.items():
            if key not in original_data:
                result[key] = value
            elif isinstance(value, dict) and isinstance(original_data[key], dict):
                result[key] = self._merge_dicts(value, original_data[key])

        return result

    def _normalize_config_data(
        self,
        value: Any,
        original_value: Any = _MISSING,
    ) -> Any:
        """标准化配置数据，处理BaseModel和字典的情况

        参数:
            value: 要标准化的值
            original_value: 原始值/用户配置值，用于保留用户配置并补齐字典默认项

        返回:
            标准化后的值
        """
        processed_value = _dump_pydantic_obj(value)

        if original_value is _MISSING:
            return processed_value

        processed_original = _dump_pydantic_obj(original_value)
        if isinstance(processed_value, dict) and isinstance(processed_original, dict):
            return self._merge_dicts(processed_value, processed_original)
        if processed_original is not None:
            return processed_original
        return processed_value

    def add_plugin_config(
        self,
        module: str,
        key: str,
        value: Any,
        *,
        help: str | None = None,
        default_value: Any = None,
        type: type | None = None,
        arg_parser: Callable | None = None,
        _override: bool = False,
    ):
        """为插件添加一个配置，不会被覆盖，只有第一个生效

        参数:
            module: 模块
            key: 键
            value: 值
            help: 配置注解.
            default_value: 默认值.
            type: 值类型.
            arg_parser: 值解析器，一般与webui配合使用.
            _override: 强制覆盖值.

        异常:
            ValueError: module和key不能为为空
            ValueError: 填写错误
        """
        key = key.upper()
        if not module or not key:
            raise ValueError("add_plugin_config: module和key不能为为空")

        existing_value = _MISSING
        if module in self._data and (config := self._data[module].configs.get(key)):
            existing_value = config.value

        simple_value = self._get_simple_config_value(module, key)
        if simple_value is _MISSING:
            processed_value = self._normalize_config_data(value, existing_value)
        else:
            processed_value = self._normalize_config_data(value, simple_value)
        processed_default_value = self._normalize_config_data(default_value)

        self.add_module.append(f"{module}:{key}".lower())
        if module in self._data and (config := self._data[module].configs.get(key)):
            config.help = help
            config.arg_parser = arg_parser
            config.type = type
            if simple_value is not _MISSING or _override:
                config.value = processed_value
                config.default_value = processed_default_value
        else:
            key = key.upper()
            if not self._data.get(module):
                self._data[module] = ConfigGroup(module=module)
            self._data[module].configs[key] = ConfigModel(
                value=processed_value,
                help=help,
                default_value=processed_default_value,
                type=type,
                arg_parser=arg_parser,
            )

    def set_config(
        self,
        module: str,
        key: str,
        value: Any,
        auto_save: bool = False,
    ):
        """设置配置值

        参数:
            module: 模块名
            key: 配置名称
            value: 值
            auto_save: 自动保存.
        """
        key = key.upper()
        if module in self._data:
            if module not in self._simple_data:
                self._simple_data[module] = {}
            if self._data[module].configs.get(key):
                self._data[module].configs[key].value = value
            else:
                self.add_plugin_config(module, key, value)
            self._simple_data[module][key] = value
            if auto_save:
                self.save(save_simple_data=True)

    def get_config(
        self,
        module: str,
        key: str,
        default: Any = None,
        *,
        build_model: bool = True,
    ) -> Any:
        """
        获取指定配置值，自动构建Pydantic模型或其它类型实例。
        - 兼容Pydantic V1/V2。
        - 支持 list[BaseModel] 等泛型容器。
        - 优先使用Pydantic原生方式解析，失败后回退到cattrs。
        """
        key = key.upper()
        config_group = self._data.get(module)
        if not config_group:
            return default

        config = config_group.configs.get(key)
        if not config:
            return default

        value_to_process = (
            config.value if config.value is not None else config.default_value
        )
        if value_to_process is None:
            return default

        if config.arg_parser:
            try:
                return config.arg_parser(value_to_process)
            except Exception as e:
                logger.debug(
                    f"配置项类型转换 MODULE: [<u><y>{module}</y></u>]"
                    f" | KEY: [<u><y>{key}</y></u>] 将使用原始值",
                    e=e,
                )

        if config.type:
            if _is_pydantic_type(config.type):
                if build_model:
                    try:
                        return parse_as(config.type, value_to_process)
                    except Exception as e:
                        logger.warning(
                            f"pydantic类型转换失败 MODULE: [<u><y>{module}</y></u>] | "
                            f"KEY: [<u><y>{key}</y></u>].",
                            e=e,
                        )
            else:
                try:
                    return cattrs.structure(value_to_process, config.type)
                except Exception as e:
                    logger.warning(
                        f"cattrs类型转换失败 MODULE: [<u><y>{module}</y></u>] | "
                        f"KEY: [<u><y>{key}</y></u>].",
                        e=e,
                    )

        return value_to_process

    def get(self, key: str) -> ConfigGroup:
        """获取插件配置数据

        参数:
            key: 键，一般为模块名

        返回:
            ConfigGroup: ConfigGroup
        """
        if key not in self._data:
            self._data[key] = ConfigGroup(module=key)
        return self._data[key]

    def save(self, path: str | Path | None = None, save_simple_data: bool = False):
        """保存数据

        参数:
            path: 路径.
            save_simple_data: 同时保存至config.yaml.
        """
        if save_simple_data:
            with open(self._simple_file, "w", encoding="utf8") as f:
                _yaml.dump(self._simple_data, f)
        path = path or self.file
        save_data = {
            module: {
                config_key: model_dump(config_model, exclude={"type", "arg_parser"})
                for config_key, config_model in config_group.configs.items()
            }
            for module, config_group in self._data.items()
        }
        with open(path, "w", encoding="utf8") as f:
            _yaml.dump(save_data, f)

    def reload(self):
        """重新加载配置文件"""
        self._load_simple_data()
        self._apply_simple_data(warn_unknown=True)
        self.save()

    def load_data(self):
        """加载数据

        异常:
            ValueError: 配置文件为空！
        """
        if not self.file.exists():
            return
        with open(self.file, encoding="utf8") as f:
            temp_data = _yaml.load(f)
        if not temp_data:
            self.file.unlink()
            raise ValueError(
                "配置文件为空！\n"
                "***********************************************************\n"
                "****** 配置文件 plugins2config.yaml 为空，已删除，请重启 ******\n"
                "***********************************************************"
            )
        count = 0
        for module in temp_data:
            config_group = ConfigGroup(module=module)
            for config in temp_data[module]:
                config_group.configs[config] = ConfigModel(**temp_data[module][config])
                count += 1
            self._data[module] = config_group
        logger.info(
            f"加载配置完成，共加载 <u><y>{len(temp_data)}</y></u> 个配置组及对应"
            f" <u><y>{count}</y></u> 个配置项"
        )

    def get_data(self) -> dict[str, ConfigGroup]:
        return copy.deepcopy(self._data)

    def is_empty(self) -> bool:
        return not bool(self._data)

    def keys(self):
        return self._data.keys()

    def __str__(self):
        return str(self._data)

    def __setitem__(self, key, value):
        self._data[key] = value

    def __getitem__(self, key):
        return self._data[key]


__all__ = [
    "AICallableParam",
    "AICallableProperties",
    "AICallableTag",
    "BaseBlock",
    "Command",
    "ConfigGroup",
    "ConfigModel",
    "ConfigsManager",
    "Example",
    "NoSuchConfig",
    "PluginCdBlock",
    "PluginCountBlock",
    "PluginExtraData",
    "PluginSetting",
    "RegisterConfig",
    "Task",
]
