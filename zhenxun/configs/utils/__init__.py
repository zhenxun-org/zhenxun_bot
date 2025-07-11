from collections.abc import Callable
import copy
from pathlib import Path
from typing import Any, TypeVar, get_args, get_origin

import cattrs
from nonebot.compat import model_dump
from pydantic import VERSION, BaseModel, Field
from ruamel.yaml import YAML
from ruamel.yaml.scanner import ScannerError

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger

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


class NoSuchConfig(Exception):
    pass


def _dump_pydantic_obj(obj: Any) -> Any:
    """
    递归地将一个对象内部的 Pydantic BaseModel 实例转换为字典。
    支持单个实例、实例列表、实例字典等情况。
    """
    if isinstance(obj, BaseModel):
        return model_dump(obj)
    if isinstance(obj, list):
        return [_dump_pydantic_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _dump_pydantic_obj(value) for key, value in obj.items()}
    return obj


def _is_pydantic_type(t: Any) -> bool:
    """
    递归检查一个类型注解是否与 Pydantic BaseModel 相关。
    """
    if t is None:
        return False
    origin = get_origin(t)
    if origin:
        return any(_is_pydantic_type(arg) for arg in get_args(t))
    return isinstance(t, type) and issubclass(t, BaseModel)


def parse_as(type_: type[T], obj: Any) -> T:
    """
    一个兼容 Pydantic V1 的 parse_obj_as 和V2的TypeAdapter.validate_python 的辅助函数。
    """
    if VERSION.startswith("1"):
        from pydantic import parse_obj_as

        return parse_obj_as(type_, obj)
    else:
        from pydantic import TypeAdapter  # type: ignore

        return TypeAdapter(type_).validate_python(obj)


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
        _yaml = YAML()
        if file:
            file.parent.mkdir(exist_ok=True, parents=True)
            self.file = file
            self.load_data()
        if self._simple_file.exists():
            try:
                with self._simple_file.open(encoding="utf8") as f:
                    self._simple_data = _yaml.load(f)
            except ScannerError as e:
                raise ScannerError(
                    f"{e}\n**********************************************\n"
                    f"****** 可能为config.yaml配置文件填写不规范 ******\n"
                    f"**********************************************"
                ) from e

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

        # 遍历新数据的键
        for key, value in new_data.items():
            # 如果键不在原数据中，添加它
            if key not in original_data:
                result[key] = value
            # 如果两边都是字典，递归处理
            elif isinstance(value, dict) and isinstance(original_data[key], dict):
                result[key] = self._merge_dicts(value, original_data[key])
            # 如果键已存在，保留原值，不覆盖
            # (不做任何操作，保持原值)

        return result

    def _normalize_config_data(self, value: Any, original_value: Any = None) -> Any:
        """标准化配置数据，处理BaseModel和字典的情况

        参数:
            value: 要标准化的值
            original_value: 原始值，用于合并字典

        返回:
            标准化后的值
        """
        # 处理BaseModel
        processed_value = _dump_pydantic_obj(value)

        # 如果处理后的值是字典，且原始值也存在
        if isinstance(processed_value, dict) and original_value is not None:
            # 处理原始值
            processed_original = _dump_pydantic_obj(original_value)

            # 如果原始值也是字典，合并它们
            if isinstance(processed_original, dict):
                return self._merge_dicts(processed_value, processed_original)

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

        # 获取现有配置值（如果存在）
        existing_value = None
        if module in self._data and (config := self._data[module].configs.get(key)):
            existing_value = config.value

        # 标准化值和默认值
        processed_value = self._normalize_config_data(value, existing_value)
        processed_default_value = self._normalize_config_data(default_value)

        self.add_module.append(f"{module}:{key}".lower())
        if module in self._data and (config := self._data[module].configs.get(key)):
            config.help = help
            config.arg_parser = arg_parser
            config.type = type
            if _override:
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

        # 1. 最高优先级：自定义的参数解析器
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
        return self._data.get(key) or ConfigGroup(module="")

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
        if self._simple_file.exists():
            with open(self._simple_file, encoding="utf8") as f:
                self._simple_data = _yaml.load(f)
        for key in self._simple_data.keys():
            for k in self._simple_data[key].keys():
                self._data[key].configs[k].value = self._simple_data[key][k]
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
